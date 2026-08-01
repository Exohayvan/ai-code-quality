from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_code_quality.checks.common import DEFAULT_EXCLUDED_DIRECTORIES
from ai_code_quality.checks.jscpd import JSCPD_VERSION
from ai_code_quality.checks.lizard import LIZARD_VERSION
from ai_code_quality.checks.markdownlint import MARKDOWNLINT_VERSION
from ai_code_quality.checks.semgrep import SEMGREP_VERSION
from ai_code_quality.checks.yamllint import YAMLLINT_VERSION
from ai_code_quality.evaluator import Enforcement, EnforcementKind, QualityEvaluation
from ai_code_quality.install_typos import TYPOS_VERSION
from ai_code_quality.models import (
    CloneFragment,
    ComplexityFunction,
    DuplicationClone,
    ScanResult,
    ToolFinding,
)
from ai_code_quality.profiles import Profile


@dataclass(frozen=True, slots=True)
class Reports:
    full: dict[str, Any]
    fix_context: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReportPaths:
    full_report: Path
    fix_context: Path


def _fragment_dict(fragment: CloneFragment) -> dict[str, object]:
    return {
        "path": fragment.path,
        "start_line": fragment.start_line,
        "end_line": fragment.end_line,
    }


def _clone_dict(clone: DuplicationClone) -> dict[str, object]:
    return {
        "first": _fragment_dict(clone.first),
        "second": _fragment_dict(clone.second),
        "lines": clone.lines,
        "tokens": clone.tokens,
        "language": clone.language,
    }


def _fragment_key(fragment: CloneFragment) -> tuple[str, int, int]:
    return (fragment.path, fragment.start_line, fragment.end_line)


def _clone_families(clones: tuple[DuplicationClone, ...]) -> list[dict[str, Any]]:
    fragments: dict[tuple[str, int, int], CloneFragment] = {}
    parent: dict[tuple[str, int, int], tuple[str, int, int]] = {}
    clone_by_edge: list[tuple[tuple[str, int, int], tuple[str, int, int], DuplicationClone]] = []

    def find(item: tuple[str, int, int]) -> tuple[str, int, int]:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != item:
            following = parent[item]
            parent[item] = root
            item = following
        return root

    def union(left: tuple[str, int, int], right: tuple[str, int, int]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for clone in clones:
        left = _fragment_key(clone.first)
        right = _fragment_key(clone.second)
        for key, fragment in ((left, clone.first), (right, clone.second)):
            fragments[key] = fragment
            parent.setdefault(key, key)
        union(left, right)
        clone_by_edge.append((left, right, clone))

    grouped_fragments: dict[tuple[str, int, int], set[tuple[str, int, int]]] = {}
    grouped_clones: dict[tuple[str, int, int], list[DuplicationClone]] = {}
    for key in fragments:
        grouped_fragments.setdefault(find(key), set()).add(key)
    for left, _right, clone in clone_by_edge:
        grouped_clones.setdefault(find(left), []).append(clone)

    families: list[dict[str, Any]] = []
    for root in sorted(grouped_fragments):
        keys = sorted(grouped_fragments[root])
        members = grouped_clones.get(root, [])
        identity = json.dumps(keys, separators=(",", ":"), ensure_ascii=True)
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        languages = sorted({clone.language for clone in members})
        families.append(
            {
                "id": f"duplication:{fingerprint}",
                "check": "duplication",
                "kind": "clone-family",
                "languages": languages,
                "lines": max((clone.lines for clone in members), default=0),
                "fragments": [_fragment_dict(fragments[key]) for key in keys],
            }
        )
    return sorted(
        families,
        key=lambda family: (
            -int(family["lines"]),
            str(family["id"]),
        ),
    )


def _bounded_family(family: dict[str, Any], *, fragment_limit: int = 10) -> dict[str, Any]:
    fragments = family["fragments"]
    return {
        **family,
        "fragments": fragments[:fragment_limit],
        "total_fragments": len(fragments),
        "omitted_fragments": max(0, len(fragments) - fragment_limit),
    }


def _function_dict(function: ComplexityFunction) -> dict[str, object]:
    return {
        "path": function.path,
        "start_line": function.start_line,
        "end_line": function.end_line,
        "symbol": function.symbol,
        "ccn": function.ccn,
        "length": function.length,
        "parameter_count": function.parameter_count,
    }


def _complexity_repair_item(function: ComplexityFunction, limit: int) -> dict[str, object]:
    item = _function_dict(function)
    return {
        "id": (f"complexity:{function.path}:{function.symbol}:{function.start_line}"),
        "check": "complexity",
        "kind": "complex-function",
        **item,
        "maximum": limit,
        "excess_debt": function.ccn - limit,
    }


def _function_metric_repair_item(
    function: ComplexityFunction, *, check: str, maximum: int, observed: int
) -> dict[str, object]:
    return {
        "id": f"{check}:{function.path}:{function.symbol}:{function.start_line}",
        "check": check,
        "kind": "function-metric",
        **_function_dict(function),
        "maximum": maximum,
        "observed": observed,
        "excess_debt": observed - maximum,
    }


def _tool_finding_dict(finding: ToolFinding) -> dict[str, object]:
    return {
        "tool": finding.tool,
        "rule": finding.rule,
        "path": finding.path,
        "line": finding.line,
        "column": finding.column,
        "end_line": finding.end_line,
        "end_column": finding.end_column,
        "message": finding.message,
        "severity": finding.severity,
        "suggestions": list(finding.suggestions),
        "location": "path" if finding.path_context else "source",
    }


def _tool_repair_item(finding: ToolFinding) -> dict[str, object]:
    return {
        "id": (f"{finding.tool}:{finding.path}:{finding.rule}:{finding.line}:{finding.column}"),
        "check": finding.tool,
        "kind": "tool-finding",
        **_tool_finding_dict(finding),
    }


def _finding_check(evaluation: Any) -> dict[str, object]:
    return {
        "status": "skipped"
        if evaluation.skipped
        else "pass"
        if evaluation.quality_passed
        else "fail",
        "blocking": evaluation.blocking,
        "count": evaluation.count,
        "immediate_count": evaluation.immediate_count,
        "allowed_count": evaluation.allowed_count,
        "findings": [_tool_finding_dict(finding) for finding in evaluation.findings],
    }


def build_reports(
    *,
    scan: ScanResult,
    profile: Profile,
    enforcement: Enforcement,
    evaluation: QualityEvaluation,
    baseline: ScanResult | None = None,
    repair_limit: int = 15,
    near_limit_limit: int = 10,
) -> Reports:
    if repair_limit < 0 or near_limit_limit < 0:
        raise ValueError("Report limits cannot be negative")
    families = _clone_families(scan.duplication.clones)
    max_observed_ccn = max((function.ccn for function in scan.functions), default=0)

    full: dict[str, Any] = {
        "schema_version": 2,
        "verdict": "pass" if evaluation.passed else "fail",
        "quality_verdict": (
            "pass"
            if evaluation.duplication.quality_passed
            and evaluation.complexity.quality_passed
            and evaluation.function_length.quality_passed
            and evaluation.arguments.quality_passed
            and evaluation.semgrep.quality_passed
            and evaluation.yamllint.quality_passed
            and evaluation.markdownlint.quality_passed
            and evaluation.typos.quality_passed
            else "fail"
        ),
        "profile": profile.name,
        "enforcement": {
            "mode": enforcement.kind.value,
            "required_percent": enforcement.percent,
        },
        "tools": {
            "jscpd": {
                "version": JSCPD_VERSION,
                "minimum_lines": 5,
                "minimum_tokens": 50,
                "comments_ignored": True,
            },
            "lizard": {
                "version": LIZARD_VERSION,
                "metrics": [
                    "cyclomatic-complexity-per-function",
                    "function-length",
                    "parameter-count",
                ],
            },
            "semgrep": {"version": SEMGREP_VERSION, "policy": profile.semgrep_policy},
            "yamllint": {"version": YAMLLINT_VERSION, "policy": profile.yamllint_policy},
            "markdownlint": {
                "version": MARKDOWNLINT_VERSION,
                "policy": profile.markdownlint_policy,
            },
            "typos": {"version": TYPOS_VERSION, "enabled": profile.typos_enabled},
            "excluded_directories": list(DEFAULT_EXCLUDED_DIRECTORIES),
        },
        "checks": {
            "duplication": {
                "status": "skipped"
                if evaluation.duplication.skipped
                else "pass"
                if evaluation.duplication.quality_passed
                else "fail",
                "blocking": evaluation.duplication.blocking,
                "observed_percent": evaluation.duplication.observed,
                "allowed_percent": evaluation.duplication.allowed,
                "duplicated_lines": scan.duplication.duplicated_lines,
                "total_lines": scan.duplication.total_lines,
                "families": families,
                "clones": [_clone_dict(clone) for clone in scan.duplication.clones],
            },
            "complexity": {
                "status": "skipped"
                if evaluation.complexity.skipped
                else "pass"
                if evaluation.complexity.quality_passed
                else "fail",
                "blocking": evaluation.complexity.blocking,
                "maximum_observed_ccn": max_observed_ccn,
                "maximum_allowed_ccn": evaluation.complexity.max_ccn,
                "debt": evaluation.complexity.debt,
                "allowed_debt": evaluation.complexity.allowed_debt,
                "findings": [
                    _complexity_repair_item(function, profile.max_ccn)
                    for function in evaluation.complexity.findings
                ]
                if profile.max_ccn is not None
                else [],
                "functions": [_function_dict(function) for function in scan.functions],
            },
            "function_length": {
                "status": "skipped"
                if evaluation.function_length.skipped
                else "pass"
                if evaluation.function_length.quality_passed
                else "fail",
                "blocking": evaluation.function_length.blocking,
                "maximum": evaluation.function_length.maximum,
                "debt": evaluation.function_length.debt,
                "allowed_debt": evaluation.function_length.allowed_debt,
                "findings": [
                    _function_dict(function) for function in evaluation.function_length.findings
                ],
            },
            "arguments": {
                "status": "skipped"
                if evaluation.arguments.skipped
                else "pass"
                if evaluation.arguments.quality_passed
                else "fail",
                "blocking": evaluation.arguments.blocking,
                "maximum": evaluation.arguments.maximum,
                "debt": evaluation.arguments.debt,
                "allowed_debt": evaluation.arguments.allowed_debt,
                "findings": [
                    _function_dict(function) for function in evaluation.arguments.findings
                ],
            },
            "semgrep": _finding_check(evaluation.semgrep),
            "yamllint": _finding_check(evaluation.yamllint),
            "markdownlint": _finding_check(evaluation.markdownlint),
            "typos": _finding_check(evaluation.typos),
        },
        "baseline": None,
    }
    if baseline is not None and profile.max_ccn is not None:
        assert profile.max_function_length is not None
        assert profile.max_parameters is not None
        full["baseline"] = {
            "duplication_percent": baseline.duplication.percentage,
            "complexity_debt": sum(
                max(0, function.ccn - profile.max_ccn) for function in baseline.functions
            ),
            "function_length_debt": sum(
                max(0, function.length - profile.max_function_length)
                for function in baseline.functions
            ),
            "argument_debt": sum(
                max(0, function.parameter_count - profile.max_parameters)
                for function in baseline.functions
            ),
            "semgrep_count": sum(finding.severity != "error" for finding in baseline.semgrep),
            "yamllint_count": len(baseline.yamllint),
            "markdownlint_count": len(baseline.markdownlint),
            "typos_count": len(baseline.typos),
        }

    repair_items: list[dict[str, object]] = []
    if not evaluation.complexity.quality_passed and profile.max_ccn is not None:
        repair_items.extend(
            _complexity_repair_item(function, profile.max_ccn)
            for function in evaluation.complexity.findings
        )
    if not evaluation.function_length.quality_passed and profile.max_function_length is not None:
        repair_items.extend(
            _function_metric_repair_item(
                function,
                check="function_length",
                maximum=profile.max_function_length,
                observed=function.length,
            )
            for function in evaluation.function_length.findings
        )
    if not evaluation.arguments.quality_passed and profile.max_parameters is not None:
        repair_items.extend(
            _function_metric_repair_item(
                function,
                check="arguments",
                maximum=profile.max_parameters,
                observed=function.parameter_count,
            )
            for function in evaluation.arguments.findings
        )
    for finding_evaluation in (
        evaluation.semgrep,
        evaluation.yamllint,
        evaluation.markdownlint,
        evaluation.typos,
    ):
        if not finding_evaluation.quality_passed:
            repair_items.extend(
                _tool_repair_item(finding) for finding in finding_evaluation.findings
            )
    if not evaluation.duplication.quality_passed:
        repair_items.extend(_bounded_family(family) for family in families)

    preserve: list[dict[str, object]] = []
    if not evaluation.duplication.skipped:
        preserve.append(
            {
                "check": "duplication",
                "observed_percent": scan.duplication.percentage,
                "allowed_percent": evaluation.duplication.allowed,
                "instruction": (
                    "Do not introduce new duplicate families or increase "
                    "duplication while repairing other findings."
                ),
            }
        )
    if not evaluation.complexity.skipped and profile.max_ccn is not None:
        preserve.append(
            {
                "check": "complexity",
                "maximum_ccn": profile.max_ccn,
                "current_debt": evaluation.complexity.debt,
                "instruction": (
                    "Do not increase the CCN of currently passing functions "
                    "or increase total complexity debt."
                ),
            }
        )
    for check, metric, maximum, metric_evaluation in (
        (
            "function_length",
            "function length",
            profile.max_function_length,
            evaluation.function_length,
        ),
        ("arguments", "parameter count", profile.max_parameters, evaluation.arguments),
    ):
        if not metric_evaluation.skipped and maximum is not None:
            preserve.append(
                {
                    "check": check,
                    "maximum": maximum,
                    "current_debt": metric_evaluation.debt,
                    "instruction": (
                        f"Do not increase {metric} for passing functions or increase "
                        f"total {check} debt."
                    ),
                }
            )
    for check, finding_evaluation in (
        ("semgrep", evaluation.semgrep),
        ("yamllint", evaluation.yamllint),
        ("markdownlint", evaluation.markdownlint),
        ("typos", evaluation.typos),
    ):
        if not finding_evaluation.skipped:
            preserve.append(
                {
                    "check": check,
                    "current_count": finding_evaluation.count,
                    "allowed_count": finding_evaluation.allowed_count,
                    "immediate_count": finding_evaluation.immediate_count,
                    "instruction": f"Do not introduce new {check} findings.",
                }
            )

    near_limit: list[dict[str, object]] = []
    if profile.max_ccn is not None:
        threshold = max(1, profile.max_ccn - 2)
        candidates = sorted(
            (
                function
                for function in scan.functions
                if threshold <= function.ccn <= profile.max_ccn
            ),
            key=lambda function: (-function.ccn, function.path, function.start_line),
        )
        near_limit = [_function_dict(function) for function in candidates[:near_limit_limit]]

    fix_context: dict[str, Any] = {
        "schema_version": 2,
        "verdict": "pass" if evaluation.passed else "fail",
        "profile": profile.name,
        "enforcement": full["enforcement"],
        "preserve": preserve,
        "near_limit": near_limit,
        "repair_batch": repair_items[:repair_limit],
        "remaining_findings": max(0, len(repair_items) - repair_limit),
        "full_report": ".ai-code-quality/report.json",
    }
    return Reports(full=full, fix_context=fix_context)


def render_summary(reports: Reports) -> str:
    full = reports.full
    fix = reports.fix_context
    checks = full["checks"]
    duplication = checks["duplication"]
    complexity = checks["complexity"]
    function_length = checks["function_length"]
    arguments = checks["arguments"]
    if full["enforcement"]["mode"] == EnforcementKind.REPORT_ONLY.value:
        verdict = "REPORT ONLY"
    else:
        verdict = "PASSED" if full["verdict"] == "pass" else "FAILED"

    duplication_observed = duplication["observed_percent"]
    duplication_allowed = duplication["allowed_percent"]
    if duplication_observed is None or duplication_allowed is None:
        duplication_result = "Disabled"
        duplication_required = "Disabled"
    else:
        duplication_result = f"{float(duplication_observed):.2f}%"
        duplication_required = f"<= {float(duplication_allowed):.2f}%"
    if complexity["status"] == "skipped":
        complexity_result = "Disabled"
        complexity_required = "Disabled"
    else:
        complexity_result = f"debt {complexity['debt']}"
        complexity_required = f"<= debt {complexity['allowed_debt']}"

    def debt_values(check: dict[str, object]) -> tuple[str, str]:
        if check["status"] == "skipped":
            return "Disabled", "Disabled"
        return f"debt {check['debt']}", f"<= debt {check['allowed_debt']}"

    def finding_values(check: dict[str, object]) -> tuple[str, str]:
        if check["status"] == "skipped":
            return "Disabled", "Disabled"
        result = f"{check['count']} ratcheted"
        required = f"<= {check['allowed_count']} ratcheted"
        if check["immediate_count"]:
            result += f" + {check['immediate_count']} immediate"
        return result, required

    length_result, length_required = debt_values(function_length)
    arguments_result, arguments_required = debt_values(arguments)
    lines = [
        f"# AI Code Quality: {verdict}",
        "",
        f"Profile: `{full['profile']}`",
        f"Enforcement: `{full['enforcement']['mode']}`",
        "",
        "| Check | Result | Required | Status |",
        "| --- | ---: | ---: | --- |",
        (
            f"| Duplication | {duplication_result} | "
            f"{duplication_required} | "
            f"{str(duplication['status']).upper()} |"
        ),
        (
            f"| Complexity | {complexity_result} | "
            f"{complexity_required} | "
            f"{str(complexity['status']).upper()} |"
        ),
        (
            f"| Function length | {length_result} | {length_required} | "
            f"{str(function_length['status']).upper()} |"
        ),
        (
            f"| Arguments | {arguments_result} | {arguments_required} | "
            f"{str(arguments['status']).upper()} |"
        ),
        *(
            (
                f"| {label} | {finding_values(checks[key])[0]} | "
                f"{finding_values(checks[key])[1]} | "
                f"{str(checks[key]['status']).upper()} |"
            )
            for key, label in (
                ("semgrep", "Semgrep"),
                ("yamllint", "YAML lint"),
                ("markdownlint", "Markdown lint"),
                ("typos", "Typos"),
            )
        ),
        "",
        "## Preserve",
    ]
    for item in fix["preserve"]:
        lines.append(f"- {item['instruction']}")
    near_limit = fix["near_limit"]
    if near_limit:
        lines.append("- Near-limit passing functions:")
        for item in near_limit:
            lines.append(
                f"  - `{item['path']}:{item['start_line']}-{item['end_line']}` "
                f"`{item['symbol']}` at CCN {item['ccn']}"
            )

    lines.extend(("", "## Fix first"))
    repairs = fix["repair_batch"]
    if not repairs:
        lines.append("No blocking repair items in this batch.")
    for index, item in enumerate(repairs, start=1):
        if item["check"] == "complexity":
            lines.append(
                f"{index}. `{item['path']}:{item['start_line']}-{item['end_line']}` "
                f"`{item['symbol']}`, CCN {item['ccn']}, allowed {item['maximum']}"
            )
        elif item["kind"] == "function-metric":
            lines.append(
                f"{index}. `{item['path']}:{item['start_line']}-{item['end_line']}` "
                f"`{item['symbol']}`, {item['check']} {item['observed']}, "
                f"allowed {item['maximum']}"
            )
        elif item["kind"] == "tool-finding":
            if item["location"] == "path":
                lines.append(
                    f"{index}. `{item['path']}` path `{item['rule']}`: {item['message']}"
                )
            else:
                lines.append(
                    f"{index}. `{item['path']}:{item['line']}` "
                    f"`{item['rule']}`: {item['message']}"
                )
        else:
            fragments = item["fragments"]
            first = fragments[0]
            lines.append(
                f"{index}. Duplicate family `{item['id']}` with "
                f"{item.get('total_fragments', len(fragments))} fragments, "
                f"starting at `{first['path']}:{first['start_line']}-{first['end_line']}`"
            )
    if fix["remaining_findings"]:
        lines.append(f"Remaining findings not shown: {fix['remaining_findings']}")
    lines.extend(
        (
            "",
            "Full report: `.ai-code-quality/report.json`",
            "AI repair context: `.ai-code-quality/fix-context.json`",
        )
    )
    return "\n".join(lines) + "\n"


def _escape_command_value(value: object) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_command_property(value: object) -> str:
    return _escape_command_value(value).replace(":", "%3A").replace(",", "%2C")


def render_annotations(reports: Reports, *, limit: int = 40) -> list[str]:
    if limit < 0:
        raise ValueError("Annotation limit cannot be negative")
    full = reports.full
    severity = (
        "warning" if full["enforcement"]["mode"] == EnforcementKind.REPORT_ONLY.value else "error"
    )
    annotations: list[str] = []
    complexity = full["checks"]["complexity"]
    max_ccn = complexity["maximum_allowed_ccn"]
    if complexity["status"] == "fail":
        for item in complexity["findings"]:
            if len(annotations) >= limit:
                break
            annotations.append(
                f"::{severity} file={_escape_command_property(item['path'])},"
                f"line={item['start_line']},endLine={item['end_line']},title=Complexity::"
                f"{_escape_command_value(item['symbol'])} has CCN {item['ccn']}; "
                f"the {full['profile']} limit is {max_ccn}."
            )
    for key, title, observed_field in (
        ("function_length", "Function length", "length"),
        ("arguments", "Arguments", "parameter_count"),
    ):
        check = full["checks"][key]
        if check["status"] == "fail":
            for item in check["findings"]:
                if len(annotations) >= limit:
                    return annotations
                annotations.append(
                    f"::{severity} file={_escape_command_property(item['path'])},"
                    f"line={item['start_line']},endLine={item['end_line']},"
                    f"title={_escape_command_property(title)}::"
                    f"{_escape_command_value(item['symbol'])} has {observed_field} "
                    f"{item[observed_field]}; the {full['profile']} limit is "
                    f"{check['maximum']}."
                )
    for key, title in (
        ("semgrep", "Semgrep"),
        ("yamllint", "YAML lint"),
        ("markdownlint", "Markdown lint"),
        ("typos", "Typos"),
    ):
        check = full["checks"][key]
        if check["status"] == "fail":
            for item in check["findings"]:
                if len(annotations) >= limit:
                    return annotations
                if item["location"] == "path":
                    continue
                annotations.append(
                    f"::{severity} file={_escape_command_property(item['path'])},"
                    f"line={item['line']},col={item['column']},"
                    f"endLine={item['end_line']},endColumn={item['end_column']},"
                    f"title={_escape_command_property(title)}::"
                    f"{_escape_command_value(item['rule'])}: "
                    f"{_escape_command_value(item['message'])}"
                )
    duplication = full["checks"]["duplication"]
    if duplication["status"] == "fail":
        for family in duplication["families"]:
            for fragment in family["fragments"]:
                if len(annotations) >= limit:
                    return annotations
                annotations.append(
                    f"::{severity} file={_escape_command_property(fragment['path'])},"
                    f"line={fragment['start_line']},endLine={fragment['end_line']},title=Duplication::"
                    f"Duplicate family {family['id']} has {len(family['fragments'])} fragments."
                )
    return annotations


def write_reports(reports: Reports, output_directory: Path) -> ReportPaths:
    output_directory.mkdir(parents=True, exist_ok=True)
    full_report = output_directory / "report.json"
    fix_context = output_directory / "fix-context.json"
    full_report.write_text(
        json.dumps(reports.full, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fix_context.write_text(
        json.dumps(reports.fix_context, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return ReportPaths(full_report=full_report, fix_context=fix_context)
