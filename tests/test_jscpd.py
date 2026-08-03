from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_code_quality.checks.jscpd import parse_jscpd_report, run_jscpd


def duplicate_body(name: str) -> str:
    return f"""def {name}(values):
    total = 0
    positive = []
    negative = []
    for value in values:
        if value > 0:
            positive.append(value)
            total += value
        elif value < 0:
            negative.append(value)
            total -= value
        else:
            total += 1
    return {{"total": total, "positive": positive, "negative": negative}}
"""


def test_parse_jscpd_report_extracts_ranges_and_totals(tmp_path: Path) -> None:
    report = {
        "duplicates": [
            {
                "format": "python",
                "lines": 8,
                "tokens": 55,
                "firstFile": {"name": "src/a.py", "start": 3, "end": 10},
                "secondFile": {"name": "src/b.py", "start": 7, "end": 14},
            }
        ],
        "statistics": {
            "total": {
                "percentage": 12.5,
                "duplicatedLines": 8,
                "lines": 64,
                "tokens": 512,
            }
        },
    }
    path = tmp_path / "jscpd-report.json"
    path.write_text(json.dumps(report))

    result = parse_jscpd_report(path)

    assert result.percentage == 12.5
    assert result.duplicated_lines == 8
    assert result.total_lines == 64
    assert result.total_tokens == 512
    assert result.clones[0].first.path == "src/a.py"
    assert result.clones[0].second.start_line == 7


def test_parse_jscpd_report_rejects_unsafe_paths(tmp_path: Path) -> None:
    report = {
        "duplicates": [
            {
                "format": "python",
                "lines": 8,
                "tokens": 55,
                "firstFile": {"name": "../outside.py", "start": 1, "end": 8},
                "secondFile": {"name": "src/b.py", "start": 1, "end": 8},
            }
        ],
        "statistics": {"total": {"percentage": 10, "duplicatedLines": 8, "lines": 80}},
    }
    path = tmp_path / "jscpd-report.json"
    path.write_text(json.dumps(report))

    with pytest.raises(ValueError, match="unsafe path"):
        parse_jscpd_report(path)


@pytest.mark.parametrize("percentage", [-0.01, 100.01, float("nan"), float("inf")])
def test_parse_jscpd_report_rejects_invalid_percentages(tmp_path: Path, percentage: float) -> None:
    path = tmp_path / "jscpd-report.json"
    path.write_text(
        json.dumps(
            {
                "duplicates": [],
                "statistics": {
                    "total": {
                        "percentage": percentage,
                        "duplicatedLines": 0,
                        "lines": 100,
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="percentage"):
        parse_jscpd_report(path)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (0, 8),
        (9, 8),
    ],
)
def test_parse_jscpd_report_rejects_invalid_fragment_ranges(
    tmp_path: Path, start: int, end: int
) -> None:
    report = {
        "duplicates": [
            {
                "format": "python",
                "lines": 8,
                "tokens": 55,
                "firstFile": {"name": "src/a.py", "start": start, "end": end},
                "secondFile": {"name": "src/b.py", "start": 1, "end": 8},
            }
        ],
        "statistics": {"total": {"percentage": 10, "duplicatedLines": 8, "lines": 80}},
    }
    path = tmp_path / "jscpd-report.json"
    path.write_text(json.dumps(report))

    with pytest.raises(ValueError, match="range"):
        parse_jscpd_report(path)


def test_parse_jscpd_report_rejects_inconsistent_totals(tmp_path: Path) -> None:
    path = tmp_path / "jscpd-report.json"
    path.write_text(
        json.dumps(
            {
                "duplicates": [],
                "statistics": {
                    "total": {
                        "percentage": 50,
                        "duplicatedLines": 101,
                        "lines": 100,
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="duplicated lines"):
        parse_jscpd_report(path)


@pytest.mark.parametrize(
    ("percentage", "duplicated_lines", "total_lines"),
    [
        (5.0, 0, 100),
        (0.0, 5, 100),
        (99.0, 5, 100),
    ],
)
def test_parse_jscpd_report_rejects_percentage_total_mismatches(
    tmp_path: Path,
    percentage: float,
    duplicated_lines: int,
    total_lines: int,
) -> None:
    path = tmp_path / "jscpd-report.json"
    path.write_text(
        json.dumps(
            {
                "duplicates": [],
                "statistics": {
                    "total": {
                        "percentage": percentage,
                        "duplicatedLines": duplicated_lines,
                        "lines": total_lines,
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="percentage does not match"):
        parse_jscpd_report(path)


def test_parse_jscpd_report_allows_small_rounding_difference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jscpd-report.json"
    path.write_text(
        json.dumps(
            {
                "duplicates": [],
                "statistics": {
                    "total": {
                        "percentage": 0.0,
                        "duplicatedLines": 1,
                        "lines": 100_000,
                    }
                },
            }
        )
    )

    result = parse_jscpd_report(path)
    assert result.duplicated_lines == 1


def test_run_jscpd_scans_real_repository_and_ignores_build_output(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "src" / "a.py").write_text(duplicate_body("first"))
    (tmp_path / "src" / "b.py").write_text(duplicate_body("second"))
    (tmp_path / "build" / "generated.py").write_text(duplicate_body("generated"))

    result = run_jscpd(tmp_path)

    assert result.percentage > 0
    assert result.clones
    assert all("build/" not in clone.first.path for clone in result.clones)
    assert all("build/" not in clone.second.path for clone in result.clones)
