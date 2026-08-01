from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_code_quality.checks.common import DEFAULT_EXCLUDED_DIRECTORIES
from ai_code_quality.checks.jscpd import JSCPD_VERSION
from ai_code_quality.checks.lizard import LIZARD_VERSION
from ai_code_quality.evaluator import Enforcement, EnforcementKind, QualityEvaluation
from ai_code_quality.models import (
    CloneFragment,
    ComplexityFunction,
    DuplicationClone,
    ScanResult,
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
    clone_by_edge: list[
        tuple[tuple[str, int, int], tuple[str, int, int], DuplicationClone]
    ] = []

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


def _bounded_family(
    family: dict[str, Any], *, fragment_limit: int = 10
) -> dict[str, Any]:
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
    }


def _complexity_repair_item(
    function: ComplexityFunction, limit: int
) -> dict[str, object]:
    item = _function_dict(function)
    return {
        "id": (
            f"complexity:{function.path}:{function.symbol}:{function.start_line}"
        ),
        "check": "complexity",
        "kind": "complex-function",
        **item,
        "maximum": limit,
        "excess_debt": function.ccn - limit,
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
        "schema_version": 1,
        "verdict": "pass" if evaluation.passed else "fail",
        "quality_verdict": (
            "pass"
            if evaluation.duplication.quality_passed
            and evaluation.complexity.quality_passed
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
                "metric": "cyclomatic-complexity-per-function",
            },
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
        },
        "baseline": None,
    }
    if baseline is not None and profile.max_ccn is not None:
        full["baseline"] = {
            "duplication_percent": baseline.duplication.percentage,
            "complexity_debt": sum(
                max(0, function.ccn - profile.max_ccn) for function in baseline.functions
            ),
        }

    repair_items: list[dict[str, object]] = []
    if not evaluation.complexity.quality_passed and profile.max_ccn is not None:
        repair_items.extend(
            _complexity_repair_item(function, profile.max_ccn)
            for function in evaluation.complexity.findings
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
        "schema_version": 1,
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
        "warning"
        if full["enforcement"]["mode"] == EnforcementKind.REPORT_ONLY.value
        else "error"
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
