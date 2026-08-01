from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Final

from ai_code_quality.checks.common import (
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    resolve_command,
    run_command_capture,
)
from ai_code_quality.models import ToolFinding
from ai_code_quality.policies import yamllint_config

YAMLLINT_VERSION: Final = "1.38.0"
_LINE: Final = re.compile(r"^(.*):(\d+):(\d+): \[(warning|error)\] (.*) \(([^()]+)\)$")


def parse_yamllint_output(payload: str) -> tuple[ToolFinding, ...]:
    findings: list[ToolFinding] = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        match = _LINE.fullmatch(raw_line)
        if match is None:
            raise ValueError("Invalid yamllint parsable output")
        path, line_raw, column_raw, severity, message, rule = match.groups()
        line = int(line_raw)
        column = int(column_raw)
        findings.append(
            ToolFinding(
                tool="yamllint",
                rule=rule,
                path=normalize_scanner_path(path),
                line=line,
                column=column,
                end_line=line,
                end_column=column,
                message=message,
                severity=severity,
            )
        )
    return tuple(
        sorted(
            findings, key=lambda finding: (finding.path, finding.line, finding.column, finding.rule)
        )
    )


def run_yamllint(repository: Path, policy: str) -> tuple[ToolFinding, ...]:
    with tempfile.TemporaryDirectory(prefix="ai-quality-yamllint-") as temporary:
        config_path = Path(temporary) / "yamllint.yml"
        config = yamllint_config(policy)
        config["ignore"] = "\n".join(
            f"**/{directory}/**" for directory in DEFAULT_EXCLUDED_DIRECTORIES
        )
        config_path.write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        output = run_command_capture(
            [resolve_command("yamllint"), "-c", str(config_path), "-f", "parsable", "."],
            cwd=repository,
            accepted_exit_codes=frozenset({0, 1}),
        )
    return parse_yamllint_output(output.stdout)
