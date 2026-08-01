from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Final

from ai_code_quality.checks.common import (
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    resolve_command,
    run_command_capture,
)
from ai_code_quality.models import ToolFinding
from ai_code_quality.policies import markdownlint_config

MARKDOWNLINT_VERSION: Final = "0.49.1"


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"Invalid markdownlint {label}")
    return value


def parse_markdownlint_json(payload: str) -> tuple[ToolFinding, ...]:
    if not payload.strip():
        return ()
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid markdownlint JSON output") from exc
    if not isinstance(document, list):
        raise ValueError("Invalid markdownlint JSON document")

    findings: list[ToolFinding] = []
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("Invalid markdownlint finding")
        names = item.get("ruleNames")
        path = item.get("fileName")
        message = item.get("ruleDescription")
        severity = item.get("severity", "error")
        if (
            not isinstance(names, list)
            or not names
            or not isinstance(names[0], str)
            or not names[0]
            or not isinstance(path, str)
            or not path
            or not isinstance(message, str)
            or not message
            or severity not in {"error", "warning"}
        ):
            raise ValueError("Invalid markdownlint finding text")
        line = _positive_int(item.get("lineNumber"), "line")
        error_range = item.get("errorRange")
        if error_range is None:
            column = 1
            end_column = 1
        elif (
            isinstance(error_range, list)
            and len(error_range) == 2
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 1
                for value in error_range
            )
        ):
            column, length = error_range
            end_column = column + length - 1
        else:
            raise ValueError("Invalid markdownlint range")
        detail = item.get("errorDetail")
        if isinstance(detail, str) and detail:
            message = f"{message}: {detail}"
        findings.append(
            ToolFinding(
                tool="markdownlint",
                rule=names[0],
                path=normalize_scanner_path(path),
                line=line,
                column=column,
                end_line=line,
                end_column=end_column,
                message=message,
                severity=severity,
            )
        )
    return tuple(
        sorted(
            findings, key=lambda finding: (finding.path, finding.line, finding.column, finding.rule)
        )
    )


def run_markdownlint(repository: Path, policy: str) -> tuple[ToolFinding, ...]:
    with tempfile.TemporaryDirectory(prefix="ai-quality-markdownlint-") as temporary:
        config_path = Path(temporary) / "markdownlint.json"
        config_path.write_text(
            json.dumps(markdownlint_config(policy), indent=2) + "\n", encoding="utf-8"
        )
        command = [
            resolve_command("markdownlint"),
            "--config",
            str(config_path),
            "--json",
            "--dot",
        ]
        ignore_path = repository / ".markdownlintignore"
        if ignore_path.is_file():
            command.extend(("--ignore-path", str(ignore_path)))
        for directory in DEFAULT_EXCLUDED_DIRECTORIES:
            command.extend(("--ignore", f"**/{directory}/**"))
        command.append(".")
        output = run_command_capture(
            command,
            cwd=repository,
            accepted_exit_codes=frozenset({0, 1}),
        )
    if (
        output.returncode == 0
        and not output.stderr.strip()
        and output.stdout.startswith("Usage: markdownlint [options] [files|directories|globs...]")
    ):
        return ()
    payload = output.stderr if output.stderr.strip() else output.stdout
    return parse_markdownlint_json(payload)
