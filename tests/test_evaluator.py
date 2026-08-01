from __future__ import annotations

import pytest

from ai_code_quality.evaluator import Enforcement, evaluate, parse_enforcement
from ai_code_quality.models import ComplexityFunction, DuplicationResult, ScanResult
from ai_code_quality.profiles import get_profile


def scan(
    *, duplication: float, ccns: tuple[int, ...], duplicated_lines: int | None = None
) -> ScanResult:
    functions = tuple(
        ComplexityFunction(
            path=f"src/function_{index}.py",
            start_line=index * 10 + 1,
            end_line=index * 10 + 5,
            symbol=f"function_{index}",
            ccn=ccn,
        )
        for index, ccn in enumerate(ccns)
    )
    return ScanResult(
        duplication=DuplicationResult(
            percentage=duplication,
            duplicated_lines=(
                round(duplication) if duplicated_lines is None else duplicated_lines
            ),
            total_lines=100,
            clones=(),
        ),
        functions=functions,
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
