from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_code_quality.checks.common import (
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    resolve_command,
    run_command_capture,
)
from ai_code_quality.models import ToolFinding


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Invalid typos {label}")
    return value


def _source_column(
    repository: Path | None,
    path: str,
    line: int,
    byte_offset: int,
    typo: str,
) -> int:
    if repository is None:
        return byte_offset + 1
    source_path = Path(path)
    if not source_path.is_absolute():
        source_path = repository / normalize_scanner_path(path)
    try:
        source_line = source_path.read_bytes().splitlines()[line - 1]
        prefix = source_line[:byte_offset].decode("utf-8")
        suffix = source_line[byte_offset:].decode("utf-8")
    except (IndexError, OSError, UnicodeDecodeError):
        return 1
    if not suffix.startswith(typo):
        return 1
    return len(prefix) + 1


def parse_typos_jsonl(
    payload: str, *, repository: Path | None = None
) -> tuple[ToolFinding, ...]:
    findings: list[ToolFinding] = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid typos JSON Lines output") from exc
        if not isinstance(item, dict):
            raise ValueError("Invalid typos finding")
        message_type = item.get("type")
        if message_type in {"binary_file", "file_type", "file", "parse"}:
            continue
        if message_type == "error":
            message = item.get("msg")
            if not isinstance(message, str) or not message:
                raise ValueError("Invalid typos scanner error")
            raise ValueError(f"typos reported scanner error: {message}")
        if message_type != "typo":
            raise ValueError("Invalid typos finding")
        path = item.get("path")
        typo = item.get("typo")
        corrections = item.get("corrections")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(typo, str)
            or not typo
            or not isinstance(corrections, list)
            or not all(isinstance(value, str) and value for value in corrections)
        ):
            raise ValueError("Invalid typos finding text")
        line_raw = item.get("line_num")
        path_context = line_raw is None
        line = 1 if path_context else _nonnegative_int(line_raw, "line")
        byte_offset = _nonnegative_int(item.get("byte_offset"), "byte offset")
        if line < 1:
            raise ValueError("Invalid typos line")
        column = (
            byte_offset + 1
            if path_context
            else _source_column(repository, path, line, byte_offset, typo)
        )
        findings.append(
            ToolFinding(
                tool="typos",
                rule="typo",
                path=normalize_scanner_path(path),
                line=line,
                column=column,
                end_line=line,
                end_column=column + len(typo) - 1,
                message=(
                    f"Possible typo {typo!r} in file path"
                    if path_context
                    else f"Possible typo {typo!r}"
                ),
                severity="warning",
                suggestions=tuple(corrections),
                path_context=path_context,
            )
        )
    return tuple(
        sorted(
            findings, key=lambda finding: (finding.path, finding.line, finding.column, finding.rule)
        )
    )


def run_typos(repository: Path) -> tuple[ToolFinding, ...]:
    command = [resolve_command("typos"), "--format", "json", "--sort"]
    for directory in DEFAULT_EXCLUDED_DIRECTORIES:
        command.extend(("--exclude", directory))
    command.append(".")
    output = run_command_capture(
        command,
        cwd=repository,
        accepted_exit_codes=frozenset({0, 2}),
    )
    return parse_typos_jsonl(output.stdout, repository=repository)
