from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ai_code_quality.checks.common import (
    COVERAGE_REPORT_PATTERNS,
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    resolve_command,
    run_command,
)
from ai_code_quality.models import CloneFragment, DuplicationClone, DuplicationResult

JSCPD_VERSION: Final[str] = "5.0.14"


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"Invalid jscpd report object at {location}")
    return value


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid jscpd number at {location}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Invalid jscpd number at {location}")
    return number


def _integer(value: object, location: str) -> int:
    number = _number(value, location)
    if not number.is_integer() or number < 0:
        raise ValueError(f"Invalid jscpd integer at {location}")
    return int(number)


def _positive_integer(value: object, location: str) -> int:
    number = _integer(value, location)
    if number < 1:
        raise ValueError(f"Invalid jscpd positive integer at {location}")
    return number


def _percentage(value: object, location: str) -> float:
    number = _number(value, location)
    if number < 0 or number > 100:
        raise ValueError(f"Invalid jscpd percentage at {location}")
    return number


def _string(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid jscpd string at {location}")
    return value


def _fragment(value: object, location: str) -> CloneFragment:
    item = _object(value, location)
    start = _integer(item.get("start"), f"{location}.start")
    end = _integer(item.get("end"), f"{location}.end")
    if start < 1 or end < start:
        raise ValueError(f"Invalid jscpd source range at {location}")
    return CloneFragment(
        path=normalize_scanner_path(_string(item.get("name"), f"{location}.name")),
        start_line=start,
        end_line=end,
    )


def parse_jscpd_report(path: Path) -> DuplicationResult:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read jscpd JSON report: {exc}") from exc
    root = _object(data, "root")
    statistics = _object(root.get("statistics"), "statistics")
    total = _object(statistics.get("total"), "statistics.total")
    raw_duplicates = root.get("duplicates")
    if not isinstance(raw_duplicates, list):
        raise ValueError("Invalid jscpd report array at duplicates")

    clones: list[DuplicationClone] = []
    for index, raw_duplicate in enumerate(raw_duplicates):
        location = f"duplicates[{index}]"
        duplicate = _object(raw_duplicate, location)
        clones.append(
            DuplicationClone(
                first=_fragment(duplicate.get("firstFile"), f"{location}.firstFile"),
                second=_fragment(duplicate.get("secondFile"), f"{location}.secondFile"),
                lines=_positive_integer(duplicate.get("lines"), f"{location}.lines"),
                tokens=_positive_integer(duplicate.get("tokens"), f"{location}.tokens"),
                language=_string(duplicate.get("format"), f"{location}.format"),
            )
        )

    percentage = _percentage(total.get("percentage"), "statistics.total.percentage")
    duplicated_lines = _integer(total.get("duplicatedLines"), "statistics.total.duplicatedLines")
    total_lines = _integer(total.get("lines"), "statistics.total.lines")
    total_tokens = _integer(total.get("tokens", 0), "statistics.total.tokens")
    if duplicated_lines > total_lines:
        raise ValueError("Invalid jscpd totals: duplicated lines exceed total lines")
    if total_lines == 0 and percentage != 0:
        raise ValueError("Invalid jscpd totals: empty input has nonzero percentage")
    expected_percentage = duplicated_lines / total_lines * 100 if total_lines else 0.0
    if not math.isclose(
        percentage,
        expected_percentage,
        rel_tol=1e-12,
        abs_tol=0.005,
    ):
        raise ValueError(
            "Invalid jscpd totals: percentage does not match duplicated and total lines"
        )
    return DuplicationResult(
        percentage=percentage,
        duplicated_lines=duplicated_lines,
        total_lines=total_lines,
        clones=tuple(clones),
        total_tokens=total_tokens,
    )


def run_jscpd(root: Path, command: Sequence[str] | None = None) -> DuplicationResult:
    repository = root.resolve()
    if not repository.is_dir():
        raise ValueError(f"Repository path is not a directory: {root}")
    if command is None:
        command = (resolve_command("npx"), "--yes", f"jscpd@{JSCPD_VERSION}")
    directory_patterns = [
        pattern
        for directory in DEFAULT_EXCLUDED_DIRECTORIES
        for pattern in (f"{directory}/**", f"**/{directory}/**")
    ]
    ignore = ",".join([*directory_patterns, *COVERAGE_REPORT_PATTERNS])
    with tempfile.TemporaryDirectory(prefix="ai-code-quality-jscpd-") as temporary:
        output = Path(temporary)
        run_command(
            (
                *command,
                "--min-lines",
                "5",
                "--min-tokens",
                "50",
                "--skip-comments",
                "--ignore",
                ignore,
                "--reporters",
                "json",
                "--output",
                str(output),
                "--no-colors",
                "--no-tips",
                "--silent",
                ".",
            ),
            cwd=repository,
        )
        return parse_jscpd_report(output / "jscpd-report.json")
