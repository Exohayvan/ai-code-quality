from __future__ import annotations

import pytest

from ai_code_quality.evaluator import Enforcement, evaluate, parse_enforcement
from ai_code_quality.models import (
    ComplexityFunction,
    CoverageResult,
    DuplicationResult,
    ScanResult,
    ToolFinding,
)
from ai_code_quality.profiles import get_profile


def scan(
    *,
    duplication: float,
    ccns: tuple[int, ...],
    duplicated_lines: int | None = None,
    lengths: tuple[int, ...] | None = None,
    parameters: tuple[int, ...] | None = None,
    semgrep: tuple[ToolFinding, ...] = (),
    yamllint: tuple[ToolFinding, ...] = (),
    markdownlint: tuple[ToolFinding, ...] = (),
    typos: tuple[ToolFinding, ...] = (),
    lint: tuple[ToolFinding, ...] = (),
    coverage: float | None = None,
) -> ScanResult:
    lengths = lengths or (0,) * len(ccns)
    parameters = parameters or (0,) * len(ccns)
    functions = tuple(
        ComplexityFunction(
            path=f"src/function_{index}.py",
            start_line=index * 10 + 1,
            end_line=index * 10 + 5,
            symbol=f"function_{index}",
            ccn=ccn,
            length=lengths[index],
            parameter_count=parameters[index],
        )
        for index, ccn in enumerate(ccns)
    )
    return ScanResult(
        duplication=DuplicationResult(
            percentage=duplication,
            duplicated_lines=(round(duplication) if duplicated_lines is None else duplicated_lines),
            total_lines=100,
            clones=(),
        ),
        functions=functions,
        semgrep=semgrep,
        yamllint=yamllint,
        markdownlint=markdownlint,
        typos=typos,
        lint=lint,
        coverage=(
            None
            if coverage is None
            else CoverageResult(
                percentage=coverage,
                covered_units=round(coverage),
                total_units=100,
                files=(),
                reports=(),
                detected_languages=("python",),
            )
        ),
    )


def finding(tool: str, *, severity: str = "warning", line: int = 1) -> ToolFinding:
    return ToolFinding(
        tool=tool,
        rule=f"{tool}-rule",
        path=f"{tool}.txt",
        line=line,
        column=1,
        end_line=line,
        end_column=2,
        message=f"{tool} finding",
        severity=severity,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("false", Enforcement.absolute()),
        (" FALSE ", Enforcement.absolute()),
        ("-1", Enforcement.report_only()),
        ("0", Enforcement.improvement(0.0)),
        ("2", Enforcement.improvement(2.0)),
        ("2.5", Enforcement.improvement(2.5)),
    ],
)
def test_parse_enforcement(raw: str, expected: Enforcement) -> None:
    assert parse_enforcement(raw) == expected


@pytest.mark.parametrize("raw", ["true", "-2", "101", "banana", ""])
def test_parse_enforcement_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_enforcement(raw)


def test_absolute_mode_enforces_profile_limits() -> None:
    evaluation = evaluate(
        current=scan(duplication=10.01, ccns=(15, 16)),
        profile=get_profile("standard"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.passed is False
    assert evaluation.duplication.passed is False
    assert evaluation.duplication.allowed == 10.0
    assert evaluation.complexity.passed is False
    assert evaluation.complexity.debt == 1
    assert [finding.symbol for finding in evaluation.complexity.findings] == ["function_1"]


def test_absolute_limits_are_inclusive() -> None:
    evaluation = evaluate(
        current=scan(duplication=10.0, ccns=(15,)),
        profile=get_profile("standard"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.passed is True


def test_lizard_length_and_argument_debts_are_independent() -> None:
    evaluation = evaluate(
        current=scan(
            duplication=0.0,
            ccns=(15, 15),
            lengths=(100, 101),
            parameters=(8, 7),
        ),
        profile=get_profile("standard"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.function_length.debt == 1
    assert evaluation.function_length.allowed_debt == 0
    assert evaluation.function_length.passed is False
    assert evaluation.arguments.debt == 1
    assert evaluation.arguments.allowed_debt == 0
    assert evaluation.arguments.passed is False


def test_external_findings_ratchet_independently_and_semgrep_errors_block() -> None:
    baseline = scan(
        duplication=0.0,
        ccns=(1,),
        semgrep=(finding("semgrep"), finding("semgrep", line=2)),
        yamllint=(finding("yamllint"), finding("yamllint", line=2)),
        markdownlint=(finding("markdownlint"), finding("markdownlint", line=2)),
    )
    current = scan(
        duplication=0.0,
        ccns=(1,),
        semgrep=(finding("semgrep"), finding("semgrep", severity="error", line=2)),
        yamllint=(finding("yamllint"),),
        markdownlint=(finding("markdownlint"), finding("markdownlint", line=2)),
    )

    evaluation = evaluate(
        current=current,
        baseline=baseline,
        profile=get_profile("standard"),
        enforcement=Enforcement.improvement(2.0),
    )

    assert evaluation.semgrep.count == 1
    assert evaluation.semgrep.immediate_count == 1
    assert evaluation.semgrep.allowed_count == 1
    assert evaluation.semgrep.passed is False
    assert evaluation.yamllint.count == 1
    assert evaluation.yamllint.allowed_count == 1
    assert evaluation.yamllint.passed is True
    assert evaluation.markdownlint.count == 2
    assert evaluation.markdownlint.allowed_count == 1
    assert evaluation.markdownlint.passed is False
    assert evaluation.typos.count == 0
    assert evaluation.typos.allowed_count == 0
    assert evaluation.typos.passed is True


def test_disabled_external_tools_are_skipped() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, ccns=(1,)),
        profile=get_profile("minimal"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.semgrep.skipped is True
    assert evaluation.yamllint.skipped is True
    assert evaluation.markdownlint.skipped is True
    assert evaluation.typos.skipped is False


def test_lint_findings_use_existing_count_ratcheting() -> None:
    baseline = scan(
        duplication=0.0,
        ccns=(1,),
        lint=tuple(finding("ruff", line=line) for line in range(1, 11)),
        coverage=80.0,
    )
    current = scan(
        duplication=0.0,
        ccns=(1,),
        lint=tuple(finding("ruff", line=line) for line in range(1, 10)),
        coverage=80.0,
    )

    evaluation = evaluate(
        current=current,
        baseline=baseline,
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(10.0),
    )

    assert evaluation.lint.count == 9
    assert evaluation.lint.allowed_count == 9
    assert evaluation.lint.passed is True


def test_absolute_lint_enforcement_requires_a_clean_profile() -> None:
    evaluation = evaluate(
        current=scan(
            duplication=0.0,
            ccns=(1,),
            lint=(finding("ruff"),),
            coverage=80.0,
        ),
        profile=get_profile("strict"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.lint.allowed_count == 0
    assert evaluation.lint.passed is False


def test_coverage_absolute_enforcement_uses_profile_target() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, ccns=(1,), coverage=79.99),
        profile=get_profile("strict"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.coverage.target == 80.0
    assert evaluation.coverage.required == 80.0
    assert evaluation.coverage.observed == 79.99
    assert evaluation.coverage.passed is False


def test_coverage_improvement_closes_the_profile_gap() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, ccns=(1,), coverage=57.5),
        baseline=scan(duplication=0.0, ccns=(1,), coverage=50.0),
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(25.0),
    )

    assert evaluation.coverage.target == 80.0
    assert evaluation.coverage.debt == pytest.approx(22.5)
    assert evaluation.coverage.allowed_debt == pytest.approx(22.5)
    assert evaluation.coverage.required == pytest.approx(57.5)
    assert evaluation.coverage.passed is True


def test_coverage_at_target_must_remain_at_target_in_improvement_mode() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, ccns=(1,), coverage=79.99),
        baseline=scan(duplication=0.0, ccns=(1,), coverage=90.0),
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(2.0),
    )

    assert evaluation.coverage.allowed_debt == 0.0
    assert evaluation.coverage.required == 80.0
    assert evaluation.coverage.passed is False


def test_coverage_is_skipped_when_no_supported_source_was_detected() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, ccns=()),
        profile=get_profile("strict"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.coverage.skipped is True


def test_zero_duplication_limit_rejects_rounded_zero_with_duplicate_lines() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.0, duplicated_lines=1, ccns=(10,)),
        profile=get_profile("strict"),
        enforcement=Enforcement.absolute(),
    )

    assert evaluation.duplication.passed is False


def test_report_only_records_findings_without_blocking() -> None:
    evaluation = evaluate(
        current=scan(duplication=75.0, ccns=(99,)),
        profile=get_profile("strict"),
        enforcement=Enforcement.report_only(),
    )

    assert evaluation.passed is True
    assert evaluation.duplication.quality_passed is False
    assert evaluation.complexity.quality_passed is False


def test_zero_improvement_requires_each_metric_not_to_regress() -> None:
    evaluation = evaluate(
        current=scan(duplication=4.9, ccns=(14, 11)),
        baseline=scan(duplication=5.0, ccns=(13, 12)),
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(0.0),
    )

    assert evaluation.passed is True
    assert evaluation.duplication.allowed == 5.0
    assert evaluation.complexity.debt == 5
    assert evaluation.complexity.allowed_debt == 5


def test_positive_improvement_applies_independently_to_both_metrics() -> None:
    evaluation = evaluate(
        current=scan(duplication=9.7, ccns=(18, 12)),
        baseline=scan(duplication=10.0, ccns=(18, 13)),
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(2.0),
    )

    assert evaluation.duplication.allowed == pytest.approx(9.8)
    assert evaluation.duplication.passed is True
    assert evaluation.complexity.allowed_debt == 10
    assert evaluation.complexity.debt == 10
    assert evaluation.complexity.passed is True


def test_positive_improvement_keeps_clean_metrics_clean() -> None:
    evaluation = evaluate(
        current=scan(duplication=0.01, duplicated_lines=1, ccns=(10,)),
        baseline=scan(duplication=0.0, ccns=(10,)),
        profile=get_profile("strict"),
        enforcement=Enforcement.improvement(2.0),
    )

    assert evaluation.passed is False
    assert evaluation.duplication.allowed == 0.0
    assert evaluation.complexity.allowed_debt == 0
    assert evaluation.function_length.allowed_debt == 0
    assert evaluation.arguments.allowed_debt == 0


def test_improvement_mode_requires_a_baseline() -> None:
    with pytest.raises(ValueError, match="baseline"):
        evaluate(
            current=scan(duplication=0.0, ccns=()),
            profile=get_profile("strict"),
            enforcement=Enforcement.improvement(2.0),
        )


def test_none_disables_checks_without_a_baseline() -> None:
    evaluation = evaluate(
        current=scan(duplication=100.0, ccns=(100,)),
        profile=get_profile("none"),
        enforcement=Enforcement.improvement(2.0),
    )

    assert evaluation.passed is True
    assert evaluation.duplication.skipped is True
    assert evaluation.complexity.skipped is True
