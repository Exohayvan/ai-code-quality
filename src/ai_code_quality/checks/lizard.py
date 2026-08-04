from __future__ import annotations

import csv
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ai_code_quality.checks.common import (
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    run_command,
)
from ai_code_quality.models import ComplexityFunction

LIZARD_VERSION: Final[str] = "1.23.0"


def parse_lizard_csv(path: Path) -> tuple[ComplexityFunction, ...]:
    functions: list[ComplexityFunction] = []
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            for row_number, row in enumerate(rows, start=1):
                if not row:
                    continue
                if len(row) != 11:
                    raise ValueError(
                        f"Invalid Lizard CSV row {row_number}: expected 11 columns, got {len(row)}"
                    )
                try:
                    ccn = int(row[1])
                    parameter_count = int(row[3])
                    length = int(row[4])
                    start_line = int(row[9])
                    end_line = int(row[10])
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid numeric value in Lizard CSV row {row_number}"
                    ) from exc
                if (
                    row[7] == "*global*"
                    and start_line == 0
                    and end_line >= 0
                    and length == end_line + 1
                ):
                    start_line = 1
                    end_line += 1
                if (
                    ccn < 1
                    or parameter_count < 0
                    or length < 1
                    or start_line < 1
                    or end_line < start_line
                ):
                    raise ValueError(f"Invalid range in Lizard CSV row {row_number}")
                functions.append(
                    ComplexityFunction(
                        path=normalize_scanner_path(row[6]),
                        start_line=start_line,
                        end_line=end_line,
                        symbol=row[7] or "<anonymous>",
                        ccn=ccn,
                        length=length,
                        parameter_count=parameter_count,
                    )
                )
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Unable to read Lizard CSV report: {exc}") from exc
    return tuple(sorted(functions, key=lambda item: (item.path, item.start_line, item.symbol)))


def run_lizard(root: Path, command: Sequence[str] | None = None) -> tuple[ComplexityFunction, ...]:
    repository = root.resolve()
    if not repository.is_dir():
        raise ValueError(f"Repository path is not a directory: {root}")
    if command is None:
        command = (sys.executable, "-m", "lizard")
    exclusions = tuple(
        pattern
        for directory in DEFAULT_EXCLUDED_DIRECTORIES
        for pattern in (f"./{directory}/*", f"*/{directory}/*")
    )
    arguments: list[str] = [*command, "--csv"]
    for exclusion in exclusions:
        arguments.extend(("--exclude", exclusion))
    arguments.append(".")
    output = run_command(arguments, cwd=repository)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".csv", delete=False
    ) as stream:
        stream.write(output)
        report = Path(stream.name)
    try:
        return parse_lizard_csv(report)
    finally:
        report.unlink(missing_ok=True)
