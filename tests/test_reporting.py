from __future__ import annotations

import json
from pathlib import Path

from ai_code_quality.evaluator import Enforcement, evaluate
from ai_code_quality.models import (
    CloneFragment,
    ComplexityFunction,
    DuplicationClone,
    DuplicationResult,
    ScanResult,
)
from ai_code_quality.profiles import get_profile
from ai_code_quality.reporting import (
    build_reports,
    render_annotations,
    render_summary,
    write_reports,
)


def make_scan() -> ScanResult:
    first = CloneFragment("src/a.py", 1, 12)
    second = CloneFragment("src/b.py", 5, 16)
    third = CloneFragment("src/c.py", 9, 20)
    clones = (
        DuplicationClone(first, second, 12, 80, "python"),
        DuplicationClone(second, third, 12, 80, "python"),
    )
    functions = tuple(
        ComplexityFunction(
            path=f"src/module_{index}.py",
            start_line=10 + index,
            end_line=20 + index,
            symbol=f"function_{index}",
            ccn=ccn,
        )
        for index, ccn in enumerate((18, 15, 12, 10, 9, 2))
    )
    return ScanResult(
        duplication=DuplicationResult(12.0, 24, 200, clones),
        functions=functions,
    )


def test_reports_cluster_clones_and_bound_repair_context() -> None:
    scan = make_scan()
    profile = get_profile("strict")
    enforcement = Enforcement.absolute()
    evaluation = evaluate(current=scan, profile=profile, enforcement=enforcement)

    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluation,
        repair_limit=2,
        near_limit_limit=2,
    )

    assert reports.full["verdict"] == "fail"
    duplicate_families = reports.full["checks"]["duplication"]["families"]
    assert len(duplicate_families) == 1
    assert len(duplicate_families[0]["fragments"]) == 3
    assert len(reports.full["checks"]["duplication"]["clones"]) == 2
    assert reports.full["tools"]["jscpd"]["version"] == "5.0.14"
    assert reports.full["tools"]["lizard"]["version"] == "1.23.0"
    assert len(reports.fix_context["repair_batch"]) == 2
    assert reports.fix_context["remaining_findings"] == 2
    assert reports.fix_context["preserve"][0]["check"] == "duplication"
    assert len(reports.fix_context["near_limit"]) == 2


def test_summary_is_compact_but_includes_failures_and_preservation() -> None:
    scan = make_scan()
    profile = get_profile("strict")
    enforcement = Enforcement.absolute()
    evaluation = evaluate(current=scan, profile=profile, enforcement=enforcement)
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluation,
        repair_limit=3,
    )

    summary = render_summary(reports)

    assert "AI Code Quality: FAILED" in summary
    assert "Do not introduce new duplicate families" in summary
    assert "src/module_0.py:10-20" in summary
    assert "Full report:" in summary
    assert len(summary.splitlines()) <= 60


def test_annotations_are_line_specific_and_capped() -> None:
    scan = make_scan()
    profile = get_profile("strict")
    evaluation = evaluate(
        current=scan,
        profile=profile,
        enforcement=Enforcement.absolute(),
    )
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=Enforcement.absolute(),
        evaluation=evaluation,
    )

    annotations = render_annotations(reports, limit=2)

    assert len(annotations) == 2
    assert annotations[0].startswith("::error file=")
    assert "line=" in annotations[0]
    assert "CCN" in annotations[0]


def test_write_reports_produces_full_and_bounded_json(tmp_path: Path) -> None:
    scan = make_scan()
    profile = get_profile("strict")
    enforcement = Enforcement.report_only()
    evaluation = evaluate(current=scan, profile=profile, enforcement=enforcement)
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluation,
    )

    paths = write_reports(reports, tmp_path)

    assert paths.full_report == tmp_path / "report.json"
    assert paths.fix_context == tmp_path / "fix-context.json"
    assert json.loads(paths.full_report.read_text())["schema_version"] == 1
    assert json.loads(paths.fix_context.read_text())["verdict"] == "pass"
    assert "AI Code Quality: REPORT ONLY" in render_summary(reports)


def test_passing_nonzero_duplication_does_not_emit_error_annotations() -> None:
    fragment_a = CloneFragment("src/a.py", 1, 6)
    fragment_b = CloneFragment("src/b.py", 1, 6)
    scan = ScanResult(
        duplication=DuplicationResult(
            5.0,
            5,
            100,
            (DuplicationClone(fragment_a, fragment_b, 6, 50, "python"),),
        ),
        functions=(ComplexityFunction("src/a.py", 1, 6, "small", 2),),
    )
    profile = get_profile("standard")
    enforcement = Enforcement.absolute()
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluate(
            current=scan,
            profile=profile,
            enforcement=enforcement,
        ),
    )

    assert reports.full["checks"]["duplication"]["status"] == "pass"
    assert render_annotations(reports) == []


def test_summary_formats_percentages_compactly() -> None:
    scan = ScanResult(
        duplication=DuplicationResult(1.23456789, 2, 162, ()),
        functions=(),
    )
    profile = get_profile("standard")
    enforcement = Enforcement.absolute()
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluate(
            current=scan,
            profile=profile,
            enforcement=enforcement,
        ),
    )

    summary = render_summary(reports)
    assert "1.23%" in summary
    assert "1.23456789%" not in summary


def test_fix_context_bounds_fragments_inside_one_clone_family() -> None:
    fragments = tuple(
        CloneFragment(f"src/copy_{index}.py", 1, 6) for index in range(51)
    )
    clones = tuple(
        DuplicationClone(fragments[index], fragments[index + 1], 6, 50, "python")
        for index in range(50)
    )
    scan = ScanResult(
        duplication=DuplicationResult(25.0, 306, 1224, clones),
        functions=(),
    )
    profile = get_profile("strict")
    enforcement = Enforcement.absolute()
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluate(
            current=scan,
            profile=profile,
            enforcement=enforcement,
        ),
        repair_limit=1,
    )

    family = reports.fix_context["repair_batch"][0]
    assert len(family["fragments"]) == 10
    assert family["omitted_fragments"] == 41
    assert len(reports.full["checks"]["duplication"]["families"][0]["fragments"]) == 51
    assert len(json.dumps(reports.fix_context)) < 10_000


def test_annotation_properties_escape_commas_and_colons() -> None:
    scan = ScanResult(
        duplication=DuplicationResult(0.0, 0, 10, ()),
        functions=(
            ComplexityFunction("src/a,b:c.py", 1, 12, "complex", 11),
        ),
    )
    profile = get_profile("strict")
    enforcement = Enforcement.absolute()
    reports = build_reports(
        scan=scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluate(
            current=scan,
            profile=profile,
            enforcement=enforcement,
        ),
    )

    annotation = render_annotations(reports)[0]
    assert "file=src/a%2Cb%3Ac.py" in annotation
    assert "file=src/a,b:c.py" not in annotation
