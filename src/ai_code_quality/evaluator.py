from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ai_code_quality.checks.languages import language_for_path
from ai_code_quality.models import ComplexityFunction, ScanResult, ToolFinding
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
class FunctionMetricEvaluation:
    quality_passed: bool
    blocking: bool
    skipped: bool
    debt: int
    allowed_debt: int | None
    maximum: int | None
    findings: tuple[ComplexityFunction, ...]

    @property
    def passed(self) -> bool:
        return self.skipped or self.quality_passed or not self.blocking


@dataclass(frozen=True, slots=True)
class FindingEvaluation:
    quality_passed: bool
    blocking: bool
    skipped: bool
    count: int
    immediate_count: int
    allowed_count: int | None
    findings: tuple[ToolFinding, ...]

    @property
    def passed(self) -> bool:
        return self.skipped or self.quality_passed or not self.blocking


@dataclass(frozen=True, slots=True)
class CoverageEvaluation:
    quality_passed: bool
    blocking: bool
    skipped: bool
    observed: float | None
    required: float | None
    target: float | None
    debt: float
    allowed_debt: float | None
    applicability: str = "comparable-or-absolute"

    @property
    def passed(self) -> bool:
        return self.skipped or self.quality_passed or not self.blocking


@dataclass(frozen=True, slots=True)
class QualityEvaluation:
    duplication: DuplicationEvaluation
    complexity: ComplexityEvaluation
    function_length: FunctionMetricEvaluation
    arguments: FunctionMetricEvaluation
    semgrep: FindingEvaluation
    yamllint: FindingEvaluation
    markdownlint: FindingEvaluation
    typos: FindingEvaluation
    lint: FindingEvaluation
    coverage: CoverageEvaluation

    @property
    def passed(self) -> bool:
        return (
            self.duplication.passed
            and self.complexity.passed
            and self.function_length.passed
            and self.arguments.passed
            and self.semgrep.passed
            and self.yamllint.passed
            and self.markdownlint.passed
            and self.typos.passed
            and self.lint.passed
            and self.coverage.passed
        )


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
        raise ValueError("require-improvement must be false, -1, 0, or a percentage from 0 to 100")
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


def _function_metric_debt(
    functions: tuple[ComplexityFunction, ...],
    limit: int,
    value: Callable[[ComplexityFunction], int],
) -> int:
    return sum(max(0, value(function) - limit) for function in functions)


def _function_metric_findings(
    functions: tuple[ComplexityFunction, ...],
    limit: int,
    value: Callable[[ComplexityFunction], int],
) -> tuple[ComplexityFunction, ...]:
    return tuple(
        sorted(
            (function for function in functions if value(function) > limit),
            key=lambda function: (
                -(value(function) - limit),
                function.path,
                function.start_line,
                function.symbol,
            ),
        )
    )


def blocking_typos(scan: ScanResult) -> tuple[ToolFinding, ...]:
    accepted = [finding for finding, _record in scan.typos_opaque]
    if len(set(accepted)) != len(accepted) or not set(accepted).issubset(scan.typos):
        raise ValueError("Invalid opaque typo classification")
    for finding, record in scan.typos_opaque:
        if (finding.path_context or finding.tool != "typos" or finding.rule != "typo"
                or finding.path != record.path or finding.line != record.line
                or finding.end_line != record.line
                or not (record.start_column <= finding.column
                        <= finding.end_column <= record.end_column)
                or record.reason != "opaque-go-pseudo-version-revision"):
            raise ValueError("Invalid opaque typo evidence")
    return tuple(finding for finding in scan.typos if finding not in accepted)


def blocking_lint(scan: ScanResult) -> tuple[ToolFinding, ...]:
    """Keep raw findings intact; reject invalid compatibility classification."""
    accepted = set(scan.lint_compatible)
    if len(accepted) != len(scan.lint_compatible) or not accepted.issubset(scan.lint):
        raise ValueError("Invalid compatibility classification")
    return tuple(finding for finding in scan.lint if finding not in accepted)


def _compatibility_signature(scan: ScanResult) -> set[tuple[str | int, ...]]:
    return {
        (item.tool, item.rule, item.path, item.line, item.column,
         item.end_line, item.end_column)
        for item in scan.lint_compatible
    }


class CompatibilityComparisonError(ValueError):
    """A ratchet has no approved symmetric compatibility correspondence."""


def _validate_compatibility_comparison(current: ScanResult, baseline: ScanResult) -> None:
    if _compatibility_signature(current) != _compatibility_signature(baseline):
        raise CompatibilityComparisonError(
            "Compatibility classifications differ between candidate and baseline; "
            "reviewed historical correspondence is required before ratcheting"
        )


def _evaluate_findings(
    *,
    current: tuple[ToolFinding, ...],
    baseline: tuple[ToolFinding, ...] | None,
    enabled: bool,
    enforcement: Enforcement,
    errors_immediate: bool = False,
) -> FindingEvaluation:
    if not enabled:
        return FindingEvaluation(True, False, True, 0, 0, None, ())
    immediate_count = (
        sum(finding.severity == "error" for finding in current) if errors_immediate else 0
    )
    count = len(current) - immediate_count
    if enforcement.kind in {EnforcementKind.ABSOLUTE, EnforcementKind.REPORT_ONLY}:
        allowed_count = 0
    else:
        if baseline is None:
            raise ValueError("A comparison baseline is required by require-improvement")
        baseline_count = (
            sum(finding.severity != "error" for finding in baseline)
            if errors_immediate
            else len(baseline)
        )
        assert enforcement.percent is not None
        allowed_count = math.floor(baseline_count * (1.0 - enforcement.percent / 100.0))
    quality_passed = immediate_count == 0 and count <= allowed_count
    return FindingEvaluation(
        quality_passed=quality_passed,
        blocking=enforcement.kind is not EnforcementKind.REPORT_ONLY,
        skipped=False,
        count=count,
        immediate_count=immediate_count,
        allowed_count=allowed_count,
        findings=current,
    )


class CoverageEvidenceError(ValueError):
    """Coverage applicability or measurement lacks fail-closed evidence."""


def _validate_coverage_evidence(scan: ScanResult) -> None:
    inventory = scan.source_inventory
    if inventory is None:
        return
    if not isinstance(inventory, tuple) or any(
        not isinstance(item, tuple) or len(item) != 2
        or not all(isinstance(value, str) and value for value in item)
        for item in inventory
    ):
        raise CoverageEvidenceError("Invalid completed source inventory")
    if len({path for path, _language in inventory}) != len(inventory):
        raise CoverageEvidenceError("Duplicate source inventory path")
    if any(language_for_path(path) != language for path, language in inventory):
        raise CoverageEvidenceError("Source inventory language mismatch")
    languages = tuple(sorted({language for _path, language in inventory}))
    if not inventory:
        if scan.coverage is not None:
            raise CoverageEvidenceError("No-source discovery conflicts with measured coverage")
        return
    if scan.coverage is None:
        raise CoverageEvidenceError("Supported source requires coverage evidence")
    if not scan.coverage.reports and (
        scan.coverage.files or scan.coverage.total_units or scan.coverage.covered_units
        or scan.coverage.percentage
    ):
        raise CoverageEvidenceError("Unreported coverage cannot claim measured credit")
    if scan.coverage.detected_languages != languages:
        raise CoverageEvidenceError("Coverage languages do not match completed source discovery")


def _validate_new_measurement(scan: ScanResult) -> None:
    coverage = scan.coverage
    if coverage is None or not coverage.reports or not coverage.files or not scan.source_inventory:
        raise CoverageEvidenceError("Newly applicable coverage requires valid measured reports")
    inventory = dict(scan.source_inventory)
    if any(inventory.get(item.path) != item.language for item in coverage.files):
        raise CoverageEvidenceError(
            "Measured coverage does not match discovered source paths/languages"
        )
    if {item.language for item in coverage.files} != set(inventory.values()):
        raise CoverageEvidenceError("Measured coverage omits a discovered language")
    if set(coverage.files) != {item for report in coverage.reports for item in report.files}:
        raise CoverageEvidenceError("Coverage aggregate and report evidence disagree")
    covered = sum(item.covered_units for item in coverage.files)
    total = sum(item.total_units for item in coverage.files)
    expected = covered / total * 100 if total else 0
    if (coverage.covered_units != covered or coverage.total_units != total
            or not math.isfinite(coverage.percentage)
            or not math.isclose(coverage.percentage, expected, abs_tol=1e-9)):
        raise CoverageEvidenceError("Coverage aggregate does not match measured counts")


def _coverage_applicability(current: ScanResult, baseline: ScanResult | None) -> str:
    _validate_coverage_evidence(current)
    if baseline is None:
        if current.source_inventory == ():
            return "not-applicable-no-supported-source"
        return "absolute"
    _validate_coverage_evidence(baseline)
    if (baseline.source_inventory and baseline.coverage is not None
            and not baseline.coverage.reports):
        raise CoverageEvidenceError("Supported-source baseline is missing measured coverage")
    if (current.source_inventory is None) != (baseline.source_inventory is None):
        raise CoverageEvidenceError("Candidate source discovery evidence is missing")
    if current.source_inventory == () and baseline.source_inventory:
        raise CoverageEvidenceError("Removing supported source requires explicit coverage review")
    if current.coverage is not None and baseline.coverage is None:
        if baseline.source_inventory != ():
            raise CoverageEvidenceError("Coverage baseline applicability is unknown or missing")
        if (not current.coverage.reports or not current.coverage.files):
            raise CoverageEvidenceError("Newly applicable coverage requires valid measured reports")
        if current.source_inventory is None or not current.source_inventory:
            raise CoverageEvidenceError(
                "Newly applicable coverage requires completed source discovery"
            )
        _validate_new_measurement(current)
        return "newly-applicable-absolute"
    return "comparable"


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
            function_length=FunctionMetricEvaluation(True, False, True, 0, None, None, ()),
            arguments=FunctionMetricEvaluation(True, False, True, 0, None, None, ()),
            semgrep=FindingEvaluation(True, False, True, 0, 0, None, ()),
            yamllint=FindingEvaluation(True, False, True, 0, 0, None, ()),
            markdownlint=FindingEvaluation(True, False, True, 0, 0, None, ()),
            typos=FindingEvaluation(True, False, True, 0, 0, None, ()),
            lint=FindingEvaluation(True, False, True, 0, 0, None, ()),
            coverage=CoverageEvaluation(True, False, True, None, None, None, 0.0, None),
        )

    assert profile.max_duplication_percent is not None
    assert profile.max_ccn is not None
    assert profile.max_function_length is not None
    assert profile.max_parameters is not None

    current_debt = _complexity_debt(current.functions, profile.max_ccn)
    findings = _complexity_findings(current.functions, profile.max_ccn)
    current_length_debt = _function_metric_debt(
        current.functions, profile.max_function_length, lambda function: function.length
    )
    length_findings = _function_metric_findings(
        current.functions, profile.max_function_length, lambda function: function.length
    )
    current_argument_debt = _function_metric_debt(
        current.functions, profile.max_parameters, lambda function: function.parameter_count
    )
    argument_findings = _function_metric_findings(
        current.functions, profile.max_parameters, lambda function: function.parameter_count
    )
    blocking = enforcement.kind is not EnforcementKind.REPORT_ONLY

    if enforcement.kind in {EnforcementKind.ABSOLUTE, EnforcementKind.REPORT_ONLY}:
        allowed_duplication = profile.max_duplication_percent
        allowed_debt = 0
        allowed_length_debt = 0
        allowed_argument_debt = 0
    else:
        if baseline is None:
            raise ValueError("A comparison baseline is required by require-improvement")
        assert enforcement.percent is not None
        factor = 1.0 - enforcement.percent / 100.0
        allowed_duplication = baseline.duplication.percentage * factor
        baseline_debt = _complexity_debt(baseline.functions, profile.max_ccn)
        allowed_debt = math.floor(baseline_debt * factor)
        baseline_length_debt = _function_metric_debt(
            baseline.functions, profile.max_function_length, lambda function: function.length
        )
        allowed_length_debt = math.floor(baseline_length_debt * factor)
        baseline_argument_debt = _function_metric_debt(
            baseline.functions,
            profile.max_parameters,
            lambda function: function.parameter_count,
        )
        allowed_argument_debt = math.floor(baseline_argument_debt * factor)

    if allowed_duplication <= 1e-9:
        duplication_quality_passed = current.duplication.duplicated_lines == 0
    else:
        duplication_quality_passed = current.duplication.percentage <= allowed_duplication + 1e-9
    complexity_quality_passed = current_debt <= allowed_debt
    function_length_quality_passed = current_length_debt <= allowed_length_debt
    arguments_quality_passed = current_argument_debt <= allowed_argument_debt
    semgrep_evaluation = _evaluate_findings(
        current=current.semgrep,
        baseline=baseline.semgrep if baseline is not None else None,
        enabled=profile.semgrep_policy is not None,
        enforcement=enforcement,
        errors_immediate=True,
    )
    yamllint_evaluation = _evaluate_findings(
        current=current.yamllint,
        baseline=baseline.yamllint if baseline is not None else None,
        enabled=profile.yamllint_policy is not None,
        enforcement=enforcement,
    )
    markdownlint_evaluation = _evaluate_findings(
        current=current.markdownlint,
        baseline=baseline.markdownlint if baseline is not None else None,
        enabled=profile.markdownlint_policy is not None,
        enforcement=enforcement,
    )
    typos_evaluation = _evaluate_findings(
        current=blocking_typos(current),
        baseline=blocking_typos(baseline) if baseline is not None else None,
        enabled=profile.typos_enabled,
        enforcement=enforcement,
    )
    if enforcement.kind is EnforcementKind.IMPROVEMENT and baseline is not None:
        _validate_compatibility_comparison(current, baseline)
    lint_evaluation = _evaluate_findings(
        current=blocking_lint(current),
        baseline=blocking_lint(baseline) if baseline is not None else None,
        enabled=profile.lint_policy is not None,
        enforcement=enforcement,
    )

    coverage_target = profile.minimum_coverage_percent
    applicability = (
        _coverage_applicability(current, baseline)
        if coverage_target is not None else "disabled"
    )
    if coverage_target is None or current.coverage is None:
        coverage_evaluation = CoverageEvaluation(
            True, False, True, None, None, coverage_target, 0.0, None, applicability
        )
    else:
        observed_coverage = current.coverage.percentage
        coverage_debt = max(0.0, coverage_target - observed_coverage)
        if (enforcement.kind in {EnforcementKind.ABSOLUTE, EnforcementKind.REPORT_ONLY}
                or applicability == "newly-applicable-absolute"):
            allowed_coverage_debt = 0.0
        else:
            if baseline is None or baseline.coverage is None:
                raise ValueError(
                    "A comparison coverage baseline is required by require-improvement"
                )
            assert enforcement.percent is not None
            baseline_coverage_debt = max(
                0.0, coverage_target - baseline.coverage.percentage
            )
            allowed_coverage_debt = baseline_coverage_debt * (
                1.0 - enforcement.percent / 100.0
            )
        required_coverage = coverage_target - allowed_coverage_debt
        coverage_evaluation = CoverageEvaluation(
            quality_passed=coverage_debt <= allowed_coverage_debt + 1e-9,
            blocking=blocking,
            skipped=False,
            observed=observed_coverage,
            required=required_coverage,
            target=coverage_target,
            debt=coverage_debt,
            allowed_debt=allowed_coverage_debt,
            applicability=applicability,
        )

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
        function_length=FunctionMetricEvaluation(
            quality_passed=function_length_quality_passed,
            blocking=blocking,
            skipped=False,
            debt=current_length_debt,
            allowed_debt=allowed_length_debt,
            maximum=profile.max_function_length,
            findings=length_findings,
        ),
        arguments=FunctionMetricEvaluation(
            quality_passed=arguments_quality_passed,
            blocking=blocking,
            skipped=False,
            debt=current_argument_debt,
            allowed_debt=allowed_argument_debt,
            maximum=profile.max_parameters,
            findings=argument_findings,
        ),
        semgrep=semgrep_evaluation,
        yamllint=yamllint_evaluation,
        markdownlint=markdownlint_evaluation,
        typos=typos_evaluation,
        lint=lint_evaluation,
        coverage=coverage_evaluation,
    )
