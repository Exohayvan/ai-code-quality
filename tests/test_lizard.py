from __future__ import annotations

from pathlib import Path

import pytest

from ai_code_quality.checks.lizard import parse_lizard_csv, run_lizard


def test_parse_lizard_csv_extracts_function_coordinates(tmp_path: Path) -> None:
    report = tmp_path / "lizard.csv"
    report.write_text(
        '12,7,49,2,14,"parse@3-16@src/parser.py","src/parser.py",'
        '"parse","parse( value , mode )",3,16\n'
    )

    result = parse_lizard_csv(report)

    assert len(result) == 1
    assert result[0].path == "src/parser.py"
    assert result[0].symbol == "parse"
    assert result[0].ccn == 7
    assert result[0].length == 14
    assert result[0].parameter_count == 2
    assert result[0].start_line == 3
    assert result[0].end_line == 16


def test_parse_lizard_csv_rejects_malformed_rows(tmp_path: Path) -> None:
    report = tmp_path / "lizard.csv"
    report.write_text("not,a,real,row\n")

    with pytest.raises(ValueError, match="11 columns"):
        parse_lizard_csv(report)


def test_parse_lizard_csv_normalizes_global_zero_based_range(tmp_path: Path) -> None:
    report = tmp_path / "lizard.csv"
    report.write_text(
        '1098,246,8995,0,3379,"*global*@0-3378@tests/runtests.pl",'
        '"tests/runtests.pl","*global*","*global*",0,3378\n'
    )

    result = parse_lizard_csv(report)

    assert len(result) == 1
    assert result[0].symbol == "*global*"
    assert result[0].start_line == 1
    assert result[0].end_line == 3379
    assert result[0].length == 3379


def test_run_lizard_scans_multiple_languages_and_ignores_build_output(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "src" / "logic.py").write_text(
        """def decide(a, b, c):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    return 0
"""
    )
    (tmp_path / "src" / "logic.js").write_text(
        """function choose(a, b) {
  if (a) return 1;
  if (b) return 2;
  return 0;
}
"""
    )
    (tmp_path / "build" / "generated.py").write_text("def generated():\n    return 1\n")

    result = run_lizard(tmp_path)

    assert {(item.path, item.symbol) for item in result} == {
        ("src/logic.js", "choose"),
        ("src/logic.py", "decide"),
    }
    assert next(item for item in result if item.symbol == "decide").ccn == 4
