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
from ai_code_quality.policies import semgrep_config

SEMGREP_VERSION: Final = "1.172.0"
_VALID_SEVERITIES: Final = {"ERROR", "WARNING", "INFO"}


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"Invalid Semgrep {label}")
    return value


def _partial_coordinate(value: object, label: str) -> tuple[int, int, int]:
    if not isinstance(value, dict) or set(value) != {"line", "col", "offset"}:
        raise ValueError(f"Invalid Semgrep partial parsing {label}")
    line = _positive_int(value["line"], f"partial parsing {label} line")
    column = _positive_int(value["col"], f"partial parsing {label} column")
    offset = value["offset"]
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"Invalid Semgrep partial parsing {label} offset")
    return line, column, offset


def _partial_parsing_coordinates(value: dict[str, Any], path: str) -> tuple[int, int, int, int]:
    error_type = value.get("type")
    spans = value.get("spans")
    if (
        set(value) != {"code", "level", "type", "message", "path", "spans"}
        or not isinstance(error_type, list)
        or len(error_type) != 2
        or error_type[0] != "PartialParsing"
        or not isinstance(error_type[1], list)
        or len(error_type[1]) != 1
        or not isinstance(spans, list)
        or len(spans) != 1
    ):
        raise ValueError("Invalid Semgrep partial parsing metadata")
    location = error_type[1][0]
    span = spans[0]
    if (
        not isinstance(location, dict)
        or set(location) != {"path", "start", "end"}
        or not isinstance(span, dict)
        or set(span) != {"file", "start", "end"}
        or location["path"] != path
        or span["file"] != path
    ):
        raise ValueError("Invalid Semgrep partial parsing span")

    location_start = _partial_coordinate(location["start"], "location start")
    location_end = _partial_coordinate(location["end"], "location end")
    span_start = _partial_coordinate(span["start"], "span start")
    span_end = _partial_coordinate(span["end"], "span end")
    if location_start != span_start or location_end != span_end:
        raise ValueError("Invalid Semgrep partial parsing span")
    line, column, start_offset = location_start
    end_line, end_column, end_offset = location_end
    if (end_line, end_column) < (line, column) or end_offset < start_offset:
        raise ValueError("Invalid Semgrep partial parsing range")
    return line, column, end_line, end_column


def _syntax_error_finding(value: object) -> ToolFinding:
    if not isinstance(value, dict):
        raise ValueError("Semgrep reported scanner errors")
    error_type = value.get("type")
    if error_type != "Syntax error" and not (
        isinstance(error_type, list) and error_type and error_type[0] == "PartialParsing"
    ):
        raise ValueError("Semgrep reported scanner errors")
    if value.get("code") != 3 or value.get("level") not in {"warn", "error"}:
        raise ValueError("Invalid Semgrep syntax error metadata")
    path = value.get("path")
    message = value.get("message")
    if not isinstance(path, str) or not path or not isinstance(message, str) or not message:
        raise ValueError("Invalid Semgrep syntax error text")
    location_prefix = f"Syntax error at line {path}:"
    if not message.startswith(location_prefix):
        raise ValueError("Invalid Semgrep syntax error location")
    line_text, separator, _ = message.removeprefix(location_prefix).partition(":")
    if not separator or not line_text.isdecimal():
        raise ValueError("Invalid Semgrep syntax error line")
    line = _positive_int(int(line_text), "syntax error line")
    column = 1
    end_line = line
    end_column = 1
    if value.get("type") != "Syntax error":
        line, column, end_line, end_column = _partial_parsing_coordinates(value, path)
        if line != int(line_text):
            raise ValueError("Invalid Semgrep partial parsing message line")
    return ToolFinding(
        tool="semgrep",
        rule="semgrep.syntax-error",
        path=normalize_scanner_path(path),
        line=line,
        column=column,
        end_line=end_line,
        end_column=end_column,
        message=message,
        severity="error",
    )


def parse_semgrep_json(payload: str) -> tuple[ToolFinding, ...]:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Semgrep JSON output") from exc
    if not isinstance(document, dict):
        raise ValueError("Invalid Semgrep JSON document")
    if document.get("version") != SEMGREP_VERSION:
        raise ValueError("Unexpected Semgrep report version")
    errors = document.get("errors")
    if not isinstance(errors, list):
        raise ValueError("Invalid Semgrep errors collection")
    error_findings = [_syntax_error_finding(error) for error in errors]
    results = document.get("results")
    if not isinstance(results, list):
        raise ValueError("Invalid Semgrep results collection")

    findings: list[ToolFinding] = list(error_findings)
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Invalid Semgrep finding")
        start = item.get("start")
        end = item.get("end")
        extra = item.get("extra")
        if not isinstance(start, dict) or not isinstance(end, dict) or not isinstance(extra, dict):
            raise ValueError("Invalid Semgrep finding coordinates")
        rule = item.get("check_id")
        path = item.get("path")
        message = extra.get("message")
        severity = extra.get("severity")
        if (
            not isinstance(rule, str)
            or not rule
            or not isinstance(path, str)
            or not path
            or not isinstance(message, str)
            or not message
        ):
            raise ValueError("Invalid Semgrep finding text")
        if severity not in _VALID_SEVERITIES:
            raise ValueError("Invalid Semgrep finding severity")
        line = _positive_int(start.get("line"), "start line")
        column = _positive_int(start.get("col"), "start column")
        end_line = _positive_int(end.get("line"), "end line")
        end_column = _positive_int(end.get("col"), "end column")
        if (end_line, end_column) < (line, column):
            raise ValueError("Invalid Semgrep finding range")
        findings.append(
            ToolFinding(
                tool="semgrep",
                rule=rule,
                path=normalize_scanner_path(path),
                line=line,
                column=column,
                end_line=end_line,
                end_column=end_column,
                message=message,
                severity=severity.lower(),
            )
        )
    return tuple(
        sorted(
            findings, key=lambda finding: (finding.path, finding.line, finding.column, finding.rule)
        )
    )


def run_semgrep(repository: Path, policy: str) -> tuple[ToolFinding, ...]:
    with tempfile.TemporaryDirectory(prefix="ai-quality-semgrep-") as temporary:
        config_path = Path(temporary) / "semgrep.yml"
        config_path.write_text(
            json.dumps(semgrep_config(policy), indent=2) + "\n", encoding="utf-8"
        )
        command = [
            resolve_command("semgrep"),
            "scan",
            "--config",
            str(config_path),
            "--json",
            "--quiet",
            "--strict",
            "--metrics",
            "off",
            "--disable-version-check",
            "--oss-only",
            "--jobs",
            "1",
            "--timeout",
            "0",
            "--timeout-threshold",
            "0",
        ]
        for directory in DEFAULT_EXCLUDED_DIRECTORIES:
            command.extend(("--exclude", directory))
        command.append(".")
        output = run_command_capture(
            command,
            cwd=repository,
            timeout=900,
            accepted_exit_codes=frozenset({0, 3}),
        )
    findings = parse_semgrep_json(output.stdout)
    if output.returncode == 3 and not any(
        finding.rule == "semgrep.syntax-error" for finding in findings
    ):
        raise RuntimeError("Semgrep reported unexplained exit code 3")
    return findings
