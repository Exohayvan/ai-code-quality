from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_profiles.py"
RUN_IDENTITY = {
    "action_sha": "f" * 40,
    "run_id": "12345",
    "run_attempt": "1",
    "runner_os": "Linux",
    "runner_arch": "X64",
}


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_profiles", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _single_cell() -> dict[str, str]:
    return {
        "cell_id": "python-repo-none",
        "language": "python",
        "repository": "owner/repo",
        "sha": "a" * 40,
        "profile": "none",
        "role": "stable",
        **RUN_IDENTITY,
    }


def _single_selection(cell: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "profiles": ["none"],
        "expected_cells": 1,
        "seed": "test",
        "matrix": {"include": [cell]},
        **RUN_IDENTITY,
    }


def test_manifest_defines_twelve_languages_and_three_repositories_per_run() -> None:
    manifest = json.loads(
        (ROOT / ".github" / "benchmarks" / "repositories.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 1
    assert manifest["profiles"] == [
        "none",
        "minimal",
        "basic",
        "standard",
        "strict",
        "hardened",
        "maximum",
    ]
    assert len(manifest["languages"]) == 12
    assert len({entry["id"] for entry in manifest["languages"]}) == 12
    for language in manifest["languages"]:
        assert len(language["stable"]) == 2
        assert len(language["rotating"]) >= 3
        repositories = language["stable"] + language["rotating"]
        assert len({item["repository"] for item in repositories}) == len(repositories)
        assert all(item["repository"].count("/") == 1 for item in repositories)


def test_benchmark_profile_contract_matches_canonical_profiles() -> None:
    module = _load_module()
    from ai_code_quality.profiles import LEVELS

    expected = {
        name: {
            "duplication": profile.max_duplication_percent,
            "complexity": profile.max_ccn,
            "function_length": profile.max_function_length,
            "arguments": profile.max_parameters,
            "policy": profile.semgrep_policy,
            "typos": profile.typos_enabled,
            "lint": profile.lint_policy,
            "coverage": profile.minimum_coverage_percent,
        }
        for name, profile in LEVELS.items()
    }
    assert expected == module.PROFILE_CONTRACT


def test_matrix_is_deterministic_complete_and_pinned() -> None:
    module = _load_module()
    manifest = module.load_manifest(ROOT / ".github" / "benchmarks" / "repositories.json")

    def resolver(repository: str) -> tuple[str, str]:
        return "main", hashlib.sha1(repository.encode()).hexdigest()

    first = module.build_selection(manifest, "run-42", RUN_IDENTITY, resolver)
    second = module.build_selection(manifest, "run-42", RUN_IDENTITY, resolver)

    assert first == second
    assert len(first["matrix"]["include"]) == 12 * 3 * 7
    assert first["expected_cells"] == 252
    cells = first["matrix"]["include"]
    assert len({cell["cell_id"] for cell in cells}) == 252
    assert all(len(cell["sha"]) == 40 for cell in cells)
    assert all(cell["role"] in {"stable", "rotating"} for cell in cells)
    for language in manifest["languages"]:
        selected = first["selected_repositories"][language["id"]]
        assert [item["role"] for item in selected] == ["stable", "stable", "rotating"]


def test_nearest_rank_percentiles_are_deterministic() -> None:
    module = _load_module()

    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert module.nearest_rank(values, 0.50) == 3.0
    assert module.nearest_rank(values, 0.95) == 100.0
    with pytest.raises(ValueError, match="at least one"):
        module.nearest_rank([], 0.50)


def test_aggregation_writes_valid_aggregate_and_profile_shields(tmp_path: Path) -> None:
    module = _load_module()
    profiles = ["none", "strict"]
    cells = []
    results = tmp_path / "results"
    badges = tmp_path / "badges"
    results.mkdir()
    for profile, durations in {"none": [1.0, 2.0, 3.0], "strict": [8.0, 9.0, 10.0]}.items():
        for index, duration in enumerate(durations):
            cell_id = f"python-repo-{index}-{profile}"
            cell = {
                "cell_id": cell_id,
                "language": "python",
                "repository": f"owner/repo-{index}",
                "sha": f"{index + 1:040x}",
                "profile": profile,
                "role": "stable" if index < 2 else "rotating",
                **RUN_IDENTITY,
            }
            cells.append(cell)
            _write_result(
                results,
                cell,
                "quality-fail" if profile == "strict" and index == 1 else "pass",
                duration,
            )
    selection = {
        "schema_version": 1,
        "profiles": profiles,
        "expected_cells": len(cells),
        **RUN_IDENTITY,
        "seed": "test",
        "matrix": {"include": cells},
    }

    summary = module.aggregate_results(selection, results, badges)

    assert summary["comparable_samples"] == 6
    assert summary["aggregate"]["p50_seconds"] == 3.0
    assert summary["aggregate"]["p95_seconds"] == 10.0
    assert summary["profiles"]["none"]["p50_seconds"] == 2.0
    assert summary["profiles"]["strict"]["p95_seconds"] == 10.0
    assert summary["per_million_tokens"]["samples"] == 3
    assert summary["per_million_tokens"]["p50_seconds"] == 9.0
    assert summary["per_million_tokens_by_profile"]["strict"]["p95_seconds"] == 10.0
    assert "none" not in summary["per_million_tokens_by_profile"]
    for path in badges.glob("runtime-*.json"):
        shield = json.loads(path.read_text(encoding="utf-8"))
        assert shield["schemaVersion"] == 1
        assert set(shield) == {"schemaVersion", "label", "message", "color"}
    assert (badges / "runtime-p50.json").is_file()
    assert (badges / "runtime-p95.json").is_file()
    assert (badges / "runtime-none-p50.json").is_file()
    assert (badges / "runtime-strict-p95.json").is_file()
    assert (badges / "runtime-per-million-tokens-p95.json").is_file()
    assert (badges / "runtime-strict-per-million-tokens-p50.json").is_file()
    assert not (badges / "runtime-none-per-million-tokens-p50.json").exists()
    assert json.loads((badges / "benchmark-lineage.json").read_text())["run_id"] == "12345"


def test_aggregation_fails_closed_on_missing_or_duplicate_cells(tmp_path: Path) -> None:
    module = _load_module()
    cell = _single_cell()
    selection = _single_selection(cell)
    results = tmp_path / "results"
    results.mkdir()

    with pytest.raises(ValueError, match="Missing benchmark result"):
        module.aggregate_results(selection, results, tmp_path / "badges")

    first = _write_result(results / "one", cell, "pass", 1.0)
    second = _write_result(results / "two", cell, "pass", 1.0)
    assert first != second
    with pytest.raises(ValueError, match="Duplicate benchmark result"):
        module.aggregate_results(selection, results, tmp_path / "badges")


def test_aggregation_refuses_to_publish_partial_infrastructure_results(tmp_path: Path) -> None:
    module = _load_module()
    cell = _single_cell()
    selection = _single_selection(cell)
    results = tmp_path / "results"
    results.mkdir()
    _write_result(results, cell, "infrastructure-fail", 1.0)

    with pytest.raises(ValueError, match="Infrastructure failed for 1"):
        module.aggregate_results(selection, results, tmp_path / "badges")
    assert not (tmp_path / "badges").exists()


def _fixture_status(name: str, disabled: set[str], failed: set[str]) -> str:
    if name in disabled:
        return "skipped"
    return "fail" if name in failed else "pass"


def _fixture_duplication(profile: str, limits, disabled: set[str], failed: set[str]) -> dict:
    return {
        "status": _fixture_status("duplication", disabled, failed),
        "blocking": "duplication" not in disabled,
        "observed_percent": None if profile == "none" else 0.0,
        "allowed_percent": limits.max_duplication_percent,
        "duplicated_lines": 0,
        "total_lines": 0 if profile == "none" else 100,
        "total_tokens": 0 if profile == "none" else 1_000_000,
        "families": [],
        "clones": [],
    }


def _fixture_complexity(limits, disabled: set[str], failed: set[str]) -> dict:
    check_failed = "complexity" in failed
    functions = []
    findings = []
    maximum_observed = 0
    if check_failed:
        function = {
            "path": "src/example.py",
            "start_line": 1,
            "end_line": 1,
            "symbol": "example",
            "ccn": limits.max_ccn + 1,
            "length": 1,
            "parameter_count": 0,
        }
        functions = [function]
        findings = [
            {
                "id": "complexity:src/example.py:example:1",
                "check": "complexity",
                "kind": "complex-function",
                **function,
                "maximum": limits.max_ccn,
                "excess_debt": 1,
            }
        ]
        maximum_observed = limits.max_ccn + 1
    return {
        "status": _fixture_status("complexity", disabled, failed),
        "blocking": "complexity" not in disabled,
        "maximum_observed_ccn": maximum_observed,
        "maximum_allowed_ccn": limits.max_ccn,
        "debt": 1 if check_failed else 0,
        "allowed_debt": None if limits.max_ccn is None else 0,
        "functions": functions,
        "findings": findings,
    }


def _fixture_debt_check(
    name: str, maximum: int | None, disabled: set[str], failed: set[str]
) -> dict:
    return {
        "status": _fixture_status(name, disabled, failed),
        "blocking": name not in disabled,
        "maximum": maximum,
        "debt": 0,
        "allowed_debt": None if maximum is None else 0,
        "findings": [],
    }


def _fixture_finding_checks(disabled: set[str], failed: set[str]) -> dict[str, object]:
    return {
        name: {
            "status": _fixture_status(name, disabled, failed),
            "blocking": name not in disabled,
            "count": 0,
            "allowed_count": None if name in disabled else 0,
            "immediate_count": 0,
            "findings": [],
        }
        for name in ("semgrep", "yamllint", "markdownlint", "typos", "lint")
    }


def _fixture_checks(profile: str, verdict: str) -> dict[str, object]:
    from ai_code_quality.profiles import LEVELS

    limits = LEVELS[profile]
    disabled = (
        {
            "duplication",
            "complexity",
            "function_length",
            "arguments",
            "semgrep",
            "yamllint",
            "markdownlint",
            "typos",
            "lint",
            "coverage",
        }
        if profile == "none"
        else ({"semgrep", "yamllint", "markdownlint"} if profile == "minimal" else set())
    )
    failed = {"complexity"} if verdict == "fail" else set()
    return {
        "duplication": _fixture_duplication(profile, limits, disabled, failed),
        "complexity": _fixture_complexity(limits, disabled, failed),
        "function_length": _fixture_debt_check(
            "function_length", limits.max_function_length, disabled, failed
        ),
        "arguments": _fixture_debt_check("arguments", limits.max_parameters, disabled, failed),
        **_fixture_finding_checks(disabled, failed),
        "coverage": {
            "status": _fixture_status("coverage", disabled, failed),
            "blocking": "coverage" not in disabled,
            "observed_percent": None if profile == "none" else 100.0,
            "required_percent": limits.minimum_coverage_percent,
            "target_percent": limits.minimum_coverage_percent,
            "debt": 0.0,
            "allowed_debt": None if profile == "none" else 0.0,
            "covered_units": 0 if profile == "none" else 10,
            "total_units": 0 if profile == "none" else 10,
            "detected_languages": [] if profile == "none" else ["python"],
            "files": []
            if profile == "none"
            else [
                {
                    "path": "src/example.py",
                    "language": "python",
                    "covered_units": 10,
                    "total_units": 10,
                }
            ],
            "reports": []
            if profile == "none"
            else [
                {
                    "path": "coverage.json",
                    "format": "coverage.py-json",
                    "covered_units": 10,
                    "total_units": 10,
                    "languages": ["python"],
                    "files": [
                        {
                            "path": "src/example.py",
                            "language": "python",
                            "covered_units": 10,
                            "total_units": 10,
                        }
                    ],
                }
            ],
        },
    }


def _fixture_tools(profile: str) -> dict[str, object]:
    from ai_code_quality.profiles import LEVELS

    policy = None if profile in {"none", "minimal"} else profile
    return {
        "excluded_directories": [".git", "node_modules"],
        "jscpd": {
            "version": "5.0.14",
            "minimum_lines": 5,
            "minimum_tokens": 50,
            "comments_ignored": True,
        },
        "lizard": {"version": "1.23.0", "metrics": ["cyclomatic-complexity"]},
        "semgrep": {"version": "1.172.0", "policy": policy},
        "yamllint": {"version": "1.38.0", "policy": policy},
        "markdownlint": {"version": "0.49.1", "policy": policy},
        "typos": {"version": "1.48.0", "enabled": profile != "none"},
        "general_lint": {
            "policy": LEVELS[profile].lint_policy,
            "ruff_version": "0.16.1",
            "oxlint_version": "1.77.0",
            "semgrep_version": "1.172.0",
        },
        "coverage": {
            "target_percent": LEVELS[profile].minimum_coverage_percent,
            "accepted_formats": [
                "coverage.py-json",
                "istanbul-json-summary",
                "lcov",
                "cobertura-xml",
                "jacoco-xml",
                "go-coverprofile",
            ],
        },
    }


def _write_report(path: Path, verdict: str = "pass", profile: str = "strict") -> str:
    report = {
        "schema_version": 3,
        "verdict": verdict,
        "quality_verdict": verdict,
        "profile": profile,
        "enforcement": {"mode": "absolute", "required_percent": None},
        "tools": _fixture_tools(profile),
        "checks": _fixture_checks(profile, verdict),
        "baseline": None,
    }
    content = json.dumps(report, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _write_result(
    root: Path,
    cell: dict[str, str],
    status: str,
    duration: float,
    attempt: str | None = None,
) -> Path:
    run_attempt = attempt or cell["run_attempt"]
    directory = root / f"attempt-{run_attempt}-{cell['cell_id']}"
    directory.mkdir(parents=True, exist_ok=True)
    comparable = status in {"pass", "quality-fail"}
    digest = None
    if comparable:
        verdict = "pass" if status == "pass" else "fail"
        digest = _write_report(directory / "report.json", verdict, cell["profile"])
    record = {
        **cell,
        "run_attempt": run_attempt,
        "schema_version": 1,
        "status": status,
        "duration_seconds": duration,
        "comparable": comparable,
        "total_tokens": 0 if cell["profile"] == "none" else 1_000_000,
        "report_sha256": digest,
    }
    path = directory / "result.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_report_classification_requires_complete_matching_policy_evidence(tmp_path: Path) -> None:
    module = _load_module()
    report = tmp_path / "report.json"

    _write_report(report)
    assert module._classify_report("success", "pass", str(report), "strict") == (
        "pass",
        1_000_000,
    )
    assert module._classify_report("failure", "fail", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )

    _write_report(report, verdict="fail")
    assert module._classify_report("failure", "fail", str(report), "strict") == (
        "quality-fail",
        1_000_000,
    )
    assert module._classify_report("failure", "", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )

    report.write_text("{", encoding="utf-8")
    assert module._classify_report("success", "pass", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )

    _write_report(report)
    malformed = json.loads(report.read_text())
    del malformed["checks"]["complexity"]["debt"]
    report.write_text(json.dumps(malformed))
    assert module._classify_report("success", "pass", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )


def test_report_classification_rejects_metrics_that_contradict_pass_status(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict")
    contradictory = json.loads(report.read_text())
    contradictory["checks"]["duplication"].update(
        observed_percent=99.0,
        allowed_percent=1.0,
        duplicated_lines=99,
        total_lines=100,
    )
    contradictory["checks"]["complexity"].update(
        maximum_observed_ccn=999,
        maximum_allowed_ccn=10,
        debt=999,
        allowed_debt=0,
    )
    contradictory["checks"]["semgrep"].update(
        count=99,
        immediate_count=0,
        allowed_count=0,
    )
    report.write_text(json.dumps(contradictory))

    assert module._classify_report("success", "pass", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )


@pytest.mark.parametrize(
    ("check_name", "changes"),
    [
        (
            "duplication",
            {"observed_percent": 1.0, "duplicated_lines": 1, "total_lines": 100},
        ),
        (
            "complexity",
            {"maximum_observed_ccn": 11, "debt": 1, "findings": [{}]},
        ),
        ("function_length", {"maximum": 76, "debt": 1, "findings": [{}]}),
        ("arguments", {"maximum": 7, "debt": 1, "findings": [{}]}),
        ("semgrep", {"count": 1, "findings": [{}]}),
        ("yamllint", {"count": 1, "findings": [{}]}),
        ("markdownlint", {"count": 1, "findings": [{}]}),
        ("typos", {"count": 1, "findings": [{}]}),
        ("lint", {"count": 1, "findings": [{}]}),
        ("coverage", {"observed_percent": 0.0, "covered_units": 0}),
    ],
)
def test_report_classification_rejects_forged_pass_for_each_check(
    tmp_path: Path, check_name: str, changes: dict[str, object]
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict")
    contradictory = json.loads(report.read_text())
    contradictory["checks"][check_name].update(changes)
    report.write_text(json.dumps(contradictory))

    assert module._classify_report("success", "pass", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )


def test_report_classification_rejects_disabled_metrics_and_wrong_profile_limits(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="none")
    disabled = json.loads(report.read_text())
    disabled["checks"]["duplication"]["total_tokens"] = 1
    report.write_text(json.dumps(disabled))
    assert module._classify_report("success", "pass", str(report), "none")[0] == (
        "infrastructure-fail"
    )

    _write_report(report, profile="strict")
    wrong_limit = json.loads(report.read_text())
    wrong_limit["checks"]["complexity"]["maximum_allowed_ccn"] = 11
    report.write_text(json.dumps(wrong_limit))
    assert module._classify_report("success", "pass", str(report), "strict")[0] == (
        "infrastructure-fail"
    )


def test_report_classification_rejects_function_evidence_that_contradicts_aggregates(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict")
    contradictory = json.loads(report.read_text())
    contradictory["checks"]["complexity"]["functions"] = [
        {
            "path": "src/example.py",
            "start_line": 1,
            "end_line": 500,
            "symbol": "oversized",
            "ccn": 999,
            "length": 500,
            "parameter_count": 99,
        }
    ]
    report.write_text(json.dumps(contradictory))

    assert module._classify_report("success", "pass", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )


def test_report_classification_rejects_malformed_tool_finding_evidence(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict", verdict="fail")
    malformed = json.loads(report.read_text())
    malformed["checks"]["semgrep"].update(status="fail", count=1, immediate_count=0, findings=[{}])
    report.write_text(json.dumps(malformed))

    assert module._classify_report("failure", "fail", str(report), "strict") == (
        "infrastructure-fail",
        0,
    )


def _tool_finding(tool: str, *, severity: str = "warning", path: str = "src/a.py") -> dict:
    return {
        "tool": tool,
        "rule": "rule",
        "path": path,
        "line": 1,
        "column": 1,
        "end_line": 1,
        "end_column": 1,
        "message": "finding",
        "severity": severity,
        "suggestions": [],
        "location": "source",
    }


def test_report_classification_rejects_clone_evidence_that_contradicts_zero_aggregate(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict")
    malformed = json.loads(report.read_text())
    first = {"path": "src/a.py", "start_line": 1, "end_line": 8}
    second = {"path": "src/b.py", "start_line": 1, "end_line": 8}
    malformed["checks"]["duplication"]["clones"] = [
        {"first": first, "second": second, "lines": 8, "tokens": 16, "language": "python"}
    ]
    malformed["checks"]["duplication"]["families"] = [
        {
            "id": "duplication:0000000000000000",
            "check": "duplication",
            "kind": "clone-family",
            "languages": ["python"],
            "lines": 8,
            "fragments": [first, second],
        }
    ]
    report.write_text(json.dumps(malformed))

    assert module._classify_report("success", "pass", str(report), "strict")[0] == (
        "infrastructure-fail"
    )


def test_duplication_evidence_requires_derived_families_and_positive_tokens() -> None:
    module = _load_module()
    first = {"path": "src/a.py", "start_line": 1, "end_line": 8}
    second = {"path": "src/b.py", "start_line": 1, "end_line": 8}
    clone = {
        "first": first,
        "second": second,
        "lines": 8,
        "tokens": 16,
        "language": "python",
    }
    check = {
        "duplicated_lines": 8,
        "total_tokens": 100,
        "clones": [clone],
        "families": module._expected_clone_families([clone]),
    }
    assert module._valid_duplication_evidence(check)

    unequal_spans = copy.deepcopy(check)
    unequal_spans["clones"][0]["second"]["end_line"] = 10
    unequal_spans["families"] = module._expected_clone_families(unequal_spans["clones"])
    assert module._valid_duplication_evidence(unequal_spans)

    forged_family = copy.deepcopy(check)
    forged_family["families"][0]["id"] = "duplication:0000000000000000"
    assert not module._valid_duplication_evidence(forged_family)

    zero_tokens = copy.deepcopy(check)
    zero_tokens["clones"][0]["tokens"] = 0
    assert not module._valid_duplication_evidence(zero_tokens)

    excessive_tokens = copy.deepcopy(check)
    excessive_tokens["clones"][0]["tokens"] = 101
    assert not module._valid_duplication_evidence(excessive_tokens)

    wrong_lines = copy.deepcopy(check)
    wrong_lines["clones"][0]["lines"] = 7
    wrong_lines["duplicated_lines"] = 7
    wrong_lines["families"] = module._expected_clone_families(wrong_lines["clones"])
    assert not module._valid_duplication_evidence(wrong_lines)


def test_duplication_evidence_accepts_derived_self_clone_family() -> None:
    module = _load_module()
    fragment = {"path": "src/generated.c", "start_line": 10, "end_line": 18}
    clone = {
        "first": fragment,
        "second": fragment,
        "lines": 9,
        "tokens": 256,
        "language": "c",
    }
    check = {
        "duplicated_lines": 9,
        "total_tokens": 256,
        "clones": [clone],
        "families": module._expected_clone_families([clone]),
    }

    assert module._valid_duplication_evidence(check)


def test_clone_family_reconstruction_indexes_each_clone_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    clones = [
        {
            "first": {"path": f"src/first-{index}.ts", "start_line": 1, "end_line": 8},
            "second": {"path": f"src/second-{index}.ts", "start_line": 1, "end_line": 8},
            "lines": 8,
            "tokens": 16,
            "language": "typescript",
        }
        for index in range(200)
    ]
    fragment_key = module._fragment_key
    minimum = min
    calls = 0
    minimum_calls = 0

    def counted_fragment_key(fragment: dict) -> tuple[str, int, int]:
        nonlocal calls
        calls += 1
        return fragment_key(fragment)

    def counted_minimum(values):
        nonlocal minimum_calls
        minimum_calls += 1
        return minimum(values)

    monkeypatch.setattr(module, "_fragment_key", counted_fragment_key)
    monkeypatch.setattr(module, "min", counted_minimum, raising=False)

    families = module._expected_clone_families(clones)

    assert len(families) == len(clones)
    assert calls <= 3 * len(clones)
    assert minimum_calls <= 1


def test_report_classification_recomputes_semgrep_immediate_count(tmp_path: Path) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict", verdict="fail")
    malformed = json.loads(report.read_text())
    malformed["checks"]["semgrep"].update(
        status="fail",
        count=1,
        immediate_count=0,
        findings=[_tool_finding("semgrep", severity="error")],
    )
    report.write_text(json.dumps(malformed))

    assert module._classify_report("failure", "fail", str(report), "strict")[0] == (
        "infrastructure-fail"
    )


def test_report_classification_rejects_unsafe_nested_paths(tmp_path: Path) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict", verdict="fail")
    malformed = json.loads(report.read_text())
    malformed["checks"]["semgrep"].update(
        status="fail",
        count=1,
        immediate_count=0,
        findings=[_tool_finding("semgrep", path="../../outside.py")],
    )
    report.write_text(json.dumps(malformed))

    assert module._classify_report("failure", "fail", str(report), "strict")[0] == (
        "infrastructure-fail"
    )


def test_report_classification_rejects_nonpositive_tool_end_coordinate(
    tmp_path: Path,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    _write_report(report, profile="strict", verdict="fail")
    malformed = json.loads(report.read_text())
    finding = _tool_finding("semgrep")
    finding.update(end_line=2, end_column=0)
    malformed["checks"]["semgrep"].update(
        status="fail", count=1, immediate_count=0, findings=[finding]
    )
    report.write_text(json.dumps(malformed))

    assert module._classify_report("failure", "fail", str(report), "strict")[0] == (
        "infrastructure-fail"
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/a.py", True),
        ("../../outside.py", False),
        ("/absolute.py", False),
        ("C:/outside.py", False),
        ("src\\a.py", False),
        ("./src/a.py", False),
        ("src/../outside.py", False),
    ],
)
def test_nested_report_paths_are_canonical_and_relative(path: str, expected: bool) -> None:
    module = _load_module()
    assert module._safe_report_path(path) is expected


def test_aggregation_revalidates_hash_bound_report_evidence(tmp_path: Path) -> None:
    module = _load_module()
    cell = _single_cell()
    selection = _single_selection(cell)
    result = _write_result(tmp_path / "results", cell, "pass", 1.0)
    result.with_name("report.json").write_text("{}")

    with pytest.raises(ValueError, match="digest mismatch"):
        module.aggregate_results(selection, tmp_path / "results", tmp_path / "badges")


def test_partial_rerun_uses_latest_attempt_per_cell(tmp_path: Path) -> None:
    module = _load_module()
    cell = {**_single_cell(), "cell_id": "python-repo-strict", "profile": "strict"}
    selection = _single_selection(cell)
    selection["profiles"] = ["strict"]
    results = tmp_path / "results"
    _write_result(results, cell, "pass", 1.0, attempt="1")
    _write_result(results, cell, "pass", 2.0, attempt="2")

    summary = module.aggregate_results(selection, results, tmp_path / "badges", current_attempt=2)

    assert summary["aggregate"]["p50_seconds"] == 2.0
    assert summary["run_attempt"] == "2"


def test_publication_guard_rejects_stale_lineage(tmp_path: Path) -> None:
    module = _load_module()
    candidate = tmp_path / "candidate.json"
    published = tmp_path / "published.json"
    base = {"schema_version": 1, "action_sha": "f" * 40, "run_attempt": "1"}
    candidate.write_text(json.dumps({**base, "run_id": "100"}))
    published.write_text(json.dumps({**base, "run_id": "101"}))

    with pytest.raises(ValueError, match="Stale benchmark lineage"):
        module._command_guard_publication(SimpleNamespace(candidate=candidate, published=published))

    candidate.write_text(json.dumps({**base, "run_id": "101", "run_attempt": "2"}))
    assert (
        module._command_guard_publication(SimpleNamespace(candidate=candidate, published=published))
        == 0
    )


def test_aggregation_binds_run_and_runner_identity(tmp_path: Path) -> None:
    module = _load_module()
    cell = _single_cell()
    selection = _single_selection(cell)
    results = tmp_path / "results"
    results.mkdir()
    _write_result(results, cell, "pass", 1.0, attempt="2")

    with pytest.raises(ValueError, match="Future run attempt"):
        module.aggregate_results(selection, results, tmp_path / "badges")
