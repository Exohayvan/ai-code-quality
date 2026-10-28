from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from importlib.resources import files
from pathlib import Path

from ai_code_quality.baseline import (
    baseline_worktree,
    repository_root_and_relative,
    resolve_baseline,
)
from ai_code_quality.checks.coverage import run_coverage
from ai_code_quality.checks.jscpd import run_jscpd
from ai_code_quality.checks.languages import source_files
from ai_code_quality.checks.lint import run_lint
from ai_code_quality.checks.lizard import run_lizard
from ai_code_quality.checks.markdownlint import run_markdownlint
from ai_code_quality.checks.opaque_go import classify as classify_opaque_go
from ai_code_quality.checks.semgrep import run_semgrep
from ai_code_quality.checks.typos import run_typos
from ai_code_quality.checks.yamllint import run_yamllint
from ai_code_quality.compatibility import classify_with_records, parse_records
from ai_code_quality.evaluator import (
    CompatibilityComparisonError,
    CoverageEvidenceError,
    EnforcementKind,
    evaluate,
    parse_enforcement,
)
from ai_code_quality.models import DuplicationResult, ScanResult
from ai_code_quality.profiles import Profile, get_profile
from ai_code_quality.reporting import (
    build_reports,
    compatibility_record_dict,
    render_annotations,
    render_summary,
    write_reports,
)


def scan_repository(
    repository: Path,
    profile: Profile,
    coverage_command: tuple[str, ...] | None = None,
) -> ScanResult:
    inventory = source_files(repository)
    languages = tuple(sorted({language for _path, language in inventory}))
    coverage_enabled = profile.minimum_coverage_percent is not None and bool(languages)
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="quality-check") as executor:
        duplication_future = executor.submit(run_jscpd, repository)
        complexity_future = executor.submit(run_lizard, repository)
        semgrep_future = (
            executor.submit(run_semgrep, repository, profile.semgrep_policy)
            if profile.semgrep_policy is not None
            else None
        )
        yamllint_future = (
            executor.submit(run_yamllint, repository, profile.yamllint_policy)
            if profile.yamllint_policy is not None
            else None
        )
        markdownlint_future = (
            executor.submit(run_markdownlint, repository, profile.markdownlint_policy)
            if profile.markdownlint_policy is not None
            else None
        )
        typos_future = executor.submit(run_typos, repository) if profile.typos_enabled else None
        lint_future = (
            executor.submit(run_lint, repository, profile.lint_policy, languages)
            if profile.lint_policy is not None and languages
            else None
        )
        coverage_future = (
            executor.submit(run_coverage, repository, languages)
            if coverage_enabled and coverage_command is None
            else None
        )
        duplication = duplication_future.result()
        functions = complexity_future.result()
        semgrep = semgrep_future.result() if semgrep_future is not None else ()
        yamllint = yamllint_future.result() if yamllint_future is not None else ()
        markdownlint = markdownlint_future.result() if markdownlint_future is not None else ()
        typos = typos_future.result() if typos_future is not None else ()
        lint = lint_future.result() if lint_future is not None else ()
        coverage = coverage_future.result() if coverage_future is not None else None
    if coverage_enabled and coverage_command is not None:
        coverage = run_coverage(repository, languages, coverage_command)
    return ScanResult(
        duplication=duplication,
        functions=functions,
        semgrep=semgrep,
        yamllint=yamllint,
        markdownlint=markdownlint,
        typos=typos,
        typos_opaque=tuple((finding, record) for finding in typos
                           if (record := classify_opaque_go(finding, repository)) is not None),
        lint=lint,
        coverage=coverage,
        source_inventory=inventory,
    )


def _empty_scan() -> ScanResult:
    return ScanResult(
        duplication=DuplicationResult(0.0, 0, 0, ()),
        functions=(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-code-quality",
        description="Run repository-wide duplication and complexity quality gates.",
    )
    parser.add_argument("--path", default=".", help="Repository path to analyze")
    parser.add_argument("--level", default="standard", help="Quality level")
    parser.add_argument(
        "--require-improvement",
        default="false",
        help="false, -1, 0, or required improvement percentage",
    )
    parser.add_argument(
        "--baseline-ref",
        default="",
        help="Explicit Git ref used for baseline comparison",
    )
    parser.add_argument(
        "--coverage-command",
        default="",
        help="Command that generates coverage reports in current and baseline worktrees",
    )
    parser.add_argument(
        "--output",
        default=".ai-code-quality",
        help="Report directory, relative to the analyzed repository",
    )
    parser.add_argument("--repair-limit", type=int, default=15)
    parser.add_argument("--annotation-limit", type=int, default=40)
    return parser


def _append_github_file(variable: str, content: str) -> None:
    destination = os.environ.get(variable)
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8") as stream:
        stream.write(content)
        if not content.endswith("\n"):
            stream.write("\n")


def _github_outputs(values: dict[str, object]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            text = str(value)
            if "\n" in text or "\r" in text:
                raise ValueError(f"GitHub output {key!r} cannot contain a newline")
            stream.write(f"{key}={text}\n")


def run(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    repository = Path(options.path).resolve()
    if not repository.is_dir():
        raise ValueError(f"Repository path is not a directory: {repository}")
    if options.repair_limit < 0 or options.annotation_limit < 0:
        raise ValueError("Report limits cannot be negative")

    profile = get_profile(options.level)
    enforcement = parse_enforcement(options.require_improvement)
    coverage_command = tuple(shlex.split(options.coverage_command)) or None
    baseline_sha = ""
    baseline_scan: ScanResult | None = None
    # Action-owned packaged records, never candidate-controlled configuration.
    records = parse_records(files("ai_code_quality").joinpath(
        "compatibility_registry.json"
    ).read_text(encoding="utf-8"))
    repository_identity = os.environ.get("GITHUB_REPOSITORY", "")

    def classify_scan(root: Path, scan: ScanResult) -> ScanResult:
        if not repository_identity:
            return scan
        accepted, matched_records = classify_with_records(
            root, repository_identity, scan.lint, records
        )
        return replace(scan, lint_compatible=accepted, lint_compatibility_records=matched_records)

    if profile.enabled:
        current_scan = classify_scan(
            repository, scan_repository(repository, profile, coverage_command)
        )
        if enforcement.kind is EnforcementKind.IMPROVEMENT:
            git_root, relative_target = repository_root_and_relative(repository)
            baseline_sha = resolve_baseline(
                git_root,
                explicit_ref=options.baseline_ref or None,
            )
            with baseline_worktree(git_root, baseline_sha) as worktree:
                baseline_target = worktree / relative_target
                if not baseline_target.is_dir():
                    raise ValueError("The analyzed path does not exist at the comparison baseline")
                baseline_scan = classify_scan(
                    baseline_target, scan_repository(baseline_target, profile, coverage_command)
                )
            if (
                coverage_command is None
                and current_scan.coverage is not None
                and current_scan.coverage.reports
                and baseline_scan.coverage is not None
                and not baseline_scan.coverage.reports
            ):
                raise ValueError(
                    "Coverage reports exist only in the current worktree; set "
                    "coverage-command so the baseline report can be generated"
                )
    else:
        current_scan = _empty_scan()

    try:
        evaluation = evaluate(
            current=current_scan,
            baseline=baseline_scan,
            profile=profile,
            enforcement=enforcement,
        )
    except (CompatibilityComparisonError, CoverageEvidenceError) as exc:
        output = Path(options.output)
        if not output.is_absolute():
            output = repository / output
        output.mkdir(parents=True, exist_ok=True)
        evidence = {
            "verdict": "error",
            "error": str(exc),
            "baseline_sha": baseline_sha or None,
            "current_source_inventory": current_scan.source_inventory,
            "baseline_source_inventory": (baseline_scan.source_inventory
                                          if baseline_scan is not None else None),
            "current_coverage": (asdict(current_scan.coverage)
                                 if current_scan.coverage is not None else None),
            "baseline_coverage": (asdict(baseline_scan.coverage)
                                  if baseline_scan is not None
                                  and baseline_scan.coverage is not None else None),
            "current_raw_lint": [asdict(item) for item in current_scan.lint],
            "baseline_raw_lint": [asdict(item) for item in baseline_scan.lint]
            if baseline_scan is not None else [],
            "current_compatible_lint": [asdict(item) for item in current_scan.lint_compatible],
            "current_compatibility_records": [
                compatibility_record_dict(item) for item in current_scan.lint_compatibility_records
            ],
            "baseline_compatibility_records": [
                compatibility_record_dict(item) for item in baseline_scan.lint_compatibility_records
            ] if baseline_scan is not None else [],
            "baseline_compatible_lint": [asdict(item) for item in baseline_scan.lint_compatible]
            if baseline_scan is not None else [],
        }
        (output / "report.json").write_text(json.dumps(evidence, indent=2) + "\n")
        raise

    reports = build_reports(
        scan=current_scan,
        baseline=baseline_scan,
        profile=profile,
        enforcement=enforcement,
        evaluation=evaluation,
        repair_limit=options.repair_limit,
    )
    output = Path(options.output)
    if not output.is_absolute():
        output = repository / output
    if baseline_sha:
        reports.full["baseline_sha"] = baseline_sha
    paths = write_reports(reports, output)
    summary = render_summary(reports)
    print(summary, end="")
    _append_github_file("GITHUB_STEP_SUMMARY", summary)
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        for annotation in render_annotations(reports, limit=options.annotation_limit):
            print(annotation)

    maximum_ccn = max((function.ccn for function in current_scan.functions), default=0)
    _github_outputs(
        {
            "result": "pass" if evaluation.passed else "fail",
            "duplication-percent": current_scan.duplication.percentage,
            "maximum-ccn": maximum_ccn,
            "complexity-debt": evaluation.complexity.debt,
            "function-length-debt": evaluation.function_length.debt,
            "argument-debt": evaluation.arguments.debt,
            "semgrep-findings": evaluation.semgrep.count,
            "yamllint-findings": evaluation.yamllint.count,
            "markdownlint-findings": evaluation.markdownlint.count,
            "typo-findings": evaluation.typos.count,
            "lint-findings": evaluation.lint.count,
            "lint-raw-findings": len(current_scan.lint),
            "lint-compatible-findings": len(current_scan.lint_compatible),
            "coverage-percent": (
                current_scan.coverage.percentage if current_scan.coverage is not None else ""
            ),
            "report-path": paths.full_report,
            "fix-context-path": paths.fix_context,
            "baseline-sha": baseline_sha,
        }
    )
    return 0 if evaluation.passed else 1


def main() -> None:
    try:
        status = run()
    except (ValueError, RuntimeError) as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
            print(f"::error title=AI Code Quality::{message}", file=sys.stderr)
        else:
            print(f"AI Code Quality error: {exc}", file=sys.stderr)
        status = 2
    raise SystemExit(status)
