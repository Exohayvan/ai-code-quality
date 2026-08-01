from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ai_code_quality.models import ComplexityFunction, ScanResult
from ai_code_quality.profiles import Profile


class EnforcementKind(StrEnum):
    ABSOLUTE = "absolute"
    REPORT_ONLY = "report-only"
    IMPROVEMENT = "improvement"


@dataclass(frozen=True, slots=True)
class Enforcement:
    kind: EnforcementKind
    percent: float | None = None

    @classmethod
    def absolute(cls) -> Enforcement:
        return cls(EnforcementKind.ABSOLUTE)

    @classmethod
    def report_only(cls) -> Enforcement:
        return cls(EnforcementKind.REPORT_ONLY)

    @classmethod
    def improvement(cls, percent: float) -> Enforcement:
        return cls(EnforcementKind.IMPROVEMENT, percent)


@dataclass(frozen=True, slots=True)
class DuplicationEvaluation:
    quality_passed: bool
    blocking: bool
    skipped: bool
    observed: float | None
    allowed: float | None

    @property
    def passed(self) -> bool:
        return self.skipped or self.quality_passed or not self.blocking


@dataclass(frozen=True, slots=True)
class ComplexityEvaluation:
    quality_passed: bool
    blocking: bool
    skipped: bool
    debt: int
    allowed_debt: int | None
    max_ccn: int | None
    findings: tuple[ComplexityFunction, ...]

    @property
    def passed(self) -> bool:
        return self.skipped or self.quality_passed or not self.blocking


@dataclass(frozen=True, slots=True)
class QualityEvaluation:
    duplication: DuplicationEvaluation
    complexity: ComplexityEvaluation

    @property
    def passed(self) -> bool:
        return self.duplication.passed and self.complexity.passed


def parse_enforcement(raw: str) -> Enforcement:
    value = raw.strip().lower()
    if value == "false":
        return Enforcement.absolute()
    if value == "-1":
        return Enforcement.report_only()
    try:
        percent = float(value)
    except ValueError as exc:
        raise ValueError(
            "require-improvement must be false, -1, 0, or a percentage from 0 to 100"
        ) from exc
    if not math.isfinite(percent) or percent < 0 or percent > 100:
        raise ValueError(
            "require-improvement must be false, -1, 0, or a percentage from 0 to 100"
        )
    return Enforcement.improvement(percent)


def _complexity_debt(functions: tuple[ComplexityFunction, ...], limit: int) -> int:
    return sum(max(0, function.ccn - limit) for function in functions)


def _complexity_findings(
    functions: tuple[ComplexityFunction, ...], limit: int
) -> tuple[ComplexityFunction, ...]:
    return tuple(
        sorted(
            (function for function in functions if function.ccn > limit),
            key=lambda function: (
                -(function.ccn - limit),
                function.path,
                function.start_line,
                function.symbol,
            ),
        )
    )


def evaluate(
    *,
    current: ScanResult,
    profile: Profile,
    enforcement: Enforcement,
    baseline: ScanResult | None = None,
) -> QualityEvaluation:
    if not profile.enabled:
        return QualityEvaluation(
            duplication=DuplicationEvaluation(True, False, True, None, None),
            complexity=ComplexityEvaluation(True, False, True, 0, None, None, ()),
        )

    assert profile.max_duplication_percent is not None
    assert profile.max_ccn is not None

    current_debt = _complexity_debt(current.functions, profile.max_ccn)
    findings = _complexity_findings(current.functions, profile.max_ccn)
    blocking = enforcement.kind is not EnforcementKind.REPORT_ONLY

    if enforcement.kind in {EnforcementKind.ABSOLUTE, EnforcementKind.REPORT_ONLY}:
        allowed_duplication = profile.max_duplication_percent
        allowed_debt = 0
    else:
        if baseline is None:
            raise ValueError("A comparison baseline is required by require-improvement")
        assert enforcement.percent is not None
        factor = 1.0 - enforcement.percent / 100.0
        allowed_duplication = baseline.duplication.percentage * factor
        baseline_debt = _complexity_debt(baseline.functions, profile.max_ccn)
        allowed_debt = math.floor(baseline_debt * factor)

    if allowed_duplication <= 1e-9:
        duplication_quality_passed = current.duplication.duplicated_lines == 0
    else:
        duplication_quality_passed = (
            current.duplication.percentage <= allowed_duplication + 1e-9
        )
    complexity_quality_passed = current_debt <= allowed_debt

    return QualityEvaluation(
        duplication=DuplicationEvaluation(
            quality_passed=duplication_quality_passed,
            blocking=blocking,
            skipped=False,
            observed=current.duplication.percentage,
            allowed=allowed_duplication,
        ),
        complexity=ComplexityEvaluation(
            quality_passed=complexity_quality_passed,
            blocking=blocking,
            skipped=False,
            debt=current_debt,
            allowed_debt=allowed_debt,
            max_ccn=profile.max_ccn,
            findings=findings,
        ),
    )
