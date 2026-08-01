from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ai_code_quality.baseline import (
    baseline_worktree,
    repository_root_and_relative,
    resolve_baseline,
)
from ai_code_quality.checks.jscpd import run_jscpd
from ai_code_quality.checks.lizard import run_lizard
from ai_code_quality.evaluator import EnforcementKind, evaluate, parse_enforcement
from ai_code_quality.models import DuplicationResult, ScanResult
from ai_code_quality.profiles import get_profile
from ai_code_quality.reporting import (
    build_reports,
    render_annotations,
    render_summary,
    write_reports,
)


def scan_repository(repository: Path) -> ScanResult:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="quality-check") as executor:
        duplication_future = executor.submit(run_jscpd, repository)
        complexity_future = executor.submit(run_lizard, repository)
        duplication = duplication_future.result()
        functions = complexity_future.result()
    return ScanResult(duplication=duplication, functions=functions)


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
    baseline_sha = ""
    baseline_scan: ScanResult | None = None

    if profile.enabled:
        current_scan = scan_repository(repository)
        if enforcement.kind is EnforcementKind.IMPROVEMENT:
            git_root, relative_target = repository_root_and_relative(repository)
            baseline_sha = resolve_baseline(
                git_root,
                explicit_ref=options.baseline_ref or None,
            )
            with baseline_worktree(git_root, baseline_sha) as worktree:
                baseline_target = worktree / relative_target
                if not baseline_target.is_dir():
                    raise ValueError(
                        "The analyzed path does not exist at the comparison baseline"
                    )
                baseline_scan = scan_repository(baseline_target)
    else:
        current_scan = _empty_scan()

    evaluation = evaluate(
        current=current_scan,
        baseline=baseline_scan,
        profile=profile,
        enforcement=enforcement,
    )
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
