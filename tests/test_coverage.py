from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ai_code_quality.checks.coverage import parse_coverage_report, run_coverage


def test_parse_coverage_py_json(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "meta": {"version": "7.0"},
                "files": {
                    "src/app.py": {
                        "summary": {"covered_lines": 8, "num_statements": 10}
                    }
                },
            }
        )
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "coverage.py-json"
    assert parsed.covered_units == 8
    assert parsed.total_units == 10
    assert parsed.files[0].language == "python"


def test_parse_istanbul_summary_json(tmp_path: Path) -> None:
    report = tmp_path / "coverage-summary.json"
    report.write_text(
        json.dumps(
            {
                "total": {"lines": {"total": 10, "covered": 6, "pct": 60}},
                "src/app.ts": {"lines": {"total": 10, "covered": 6, "pct": 60}},
            }
        )
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "istanbul-json-summary"
    assert (parsed.covered_units, parsed.total_units) == (6, 10)
    assert parsed.files[0].language == "typescript"


def test_parse_lcov_info(tmp_path: Path) -> None:
    report = tmp_path / "lcov.info"
    report.write_text("TN:\nSF:src/app.js\nDA:1,1\nDA:2,0\nend_of_record\n")

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "lcov"
    assert (parsed.covered_units, parsed.total_units) == (1, 2)
    assert parsed.files[0].language == "javascript"


def test_parse_cobertura_xml(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        """<?xml version="1.0"?>
<coverage><packages><package><classes>
<class filename="src/app.cs"><lines>
<line number="1" hits="1"/><line number="2" hits="1"/>
<line number="3" hits="1"/><line number="4" hits="0"/>
</lines></class>
</classes></package></packages></coverage>
"""
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "cobertura-xml"
    assert (parsed.covered_units, parsed.total_units) == (3, 4)
    assert parsed.files[0].language == "csharp"


def test_cobertura_merges_multiple_classes_for_one_source_file(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        "<coverage><packages><package><classes>\n"
        '<class filename="src/model.cs"><lines>'
        '<line number="1" hits="1"/><line number="2" hits="0"/>'
        "</lines></class>\n"
        '<class filename="src/model.cs"><lines>'
        '<line number="2" hits="1"/><line number="3" hits="0"/>'
        "</lines></class>\n"
        "</classes></package></packages></coverage>"
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert len(parsed.files) == 1
    assert parsed.files[0].path == "src/model.cs"
    assert (parsed.covered_units, parsed.total_units) == (2, 3)


def test_parse_jacoco_xml(tmp_path: Path) -> None:
    report = tmp_path / "jacoco.xml"
    report.write_text(
        """<?xml version="1.0"?>
<report name="demo"><package name="com/example">
<sourcefile name="App.java"><counter type="LINE" missed="3" covered="7"/></sourcefile>
</package></report>
"""
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "jacoco-xml"
    assert (parsed.covered_units, parsed.total_units) == (7, 10)
    assert parsed.files[0].path == "com/example/App.java"


def test_parse_go_coverprofile(tmp_path: Path) -> None:
    report = tmp_path / "coverage.out"
    report.write_text(
        "mode: atomic\n"
        "pkg/service.go:1.1,3.2 3 1\n"
        "pkg/service.go:5.1,6.2 2 0\n"
    )

    parsed = parse_coverage_report(report, tmp_path)

    assert parsed.format == "go-coverprofile"
    assert (parsed.covered_units, parsed.total_units) == (3, 5)
    assert parsed.files[0].language == "go"


def test_run_coverage_aggregates_multiple_languages_without_double_counting(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n")
    (tmp_path / "src" / "app.ts").write_text("export const ok = true;\n")
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "files": {
                    "src/app.py": {
                        "summary": {"covered_lines": 8, "num_statements": 10}
                    }
                }
            }
        )
    )
    (tmp_path / "coverage-summary.json").write_text(
        json.dumps(
            {
                "total": {"lines": {"total": 10, "covered": 6}},
                "src/app.ts": {"lines": {"total": 10, "covered": 6}},
            }
        )
    )

    result = run_coverage(tmp_path, ("python", "typescript"))

    assert result.percentage == pytest.approx(70.0)
    assert (result.covered_units, result.total_units) == (14, 20)
    assert result.detected_languages == ("python", "typescript")
    assert {item.language for item in result.files} == {"python", "typescript"}
    assert len(result.reports) == 2


def test_supported_source_without_a_report_is_zero_coverage(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('untested')\n")

    result = run_coverage(tmp_path, ("python",))

    assert result.percentage == 0.0
    assert result.covered_units == 0
    assert result.total_units == 0
    assert result.reports == ()


def test_coverage_command_generates_reports_for_the_scanned_worktree(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "generate.py").write_text(
        "from pathlib import Path\n"
        "Path('coverage.json').write_text("
        "'{\"files\": {\"src.py\": {\"summary\": "
        "{\"covered_lines\": 1, \"num_statements\": 1}}}}')\n",
        encoding="utf-8",
    )

    result = run_coverage(
        tmp_path,
        ("python",),
        (sys.executable, "generate.py"),
    )

    assert result.percentage == 100.0


def test_coverage_command_failure_is_an_operational_error(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="exited with code 7"):
        run_coverage(
            tmp_path,
            ("python",),
            (sys.executable, "-c", "raise SystemExit(7)"),
        )


def test_coverage_command_cannot_modify_supported_source(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="modified supported source"):
        run_coverage(
            tmp_path,
            ("python",),
            (
                sys.executable,
                "-c",
                "from pathlib import Path; Path('src.py').write_text('value = 2\\n')",
            ),
        )


def test_coverage_command_requires_a_fresh_report(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "files": {
                    "src.py": {
                        "summary": {"covered_lines": 1, "num_statements": 1}
                    }
                }
            }
        )
    )

    with pytest.raises(RuntimeError, match="fresh supported report"):
        run_coverage(
            tmp_path,
            ("python",),
            (sys.executable, "-c", "pass"),
        )


def test_coverage_rejects_unsafe_source_paths(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "files": {
                    "../outside.py": {
                        "summary": {"covered_lines": 1, "num_statements": 1}
                    }
                }
            }
        )
    )

    with pytest.raises(ValueError, match="unsafe path"):
        parse_coverage_report(report, tmp_path)


def test_coverage_rejects_conflicting_duplicate_file_results(tmp_path: Path) -> None:
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "files": {
                    "app.py": {
                        "summary": {"covered_lines": 1, "num_statements": 2}
                    }
                }
            }
        )
    )
    (tmp_path / "lcov.info").write_text("SF:app.py\nDA:1,1\nDA:2,1\nend_of_record\n")

    with pytest.raises(ValueError, match="Conflicting coverage"):
        run_coverage(tmp_path, ("python",))
