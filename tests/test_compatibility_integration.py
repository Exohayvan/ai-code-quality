from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, replace
from importlib.resources import files
from pathlib import Path

import pytest

import ai_code_quality.cli as cli
from ai_code_quality.compatibility import classify, classify_with_records, parse_records
from ai_code_quality.evaluator import blocking_lint, evaluate, parse_enforcement
from ai_code_quality.models import CoverageResult, DuplicationResult, ScanResult, ToolFinding
from ai_code_quality.profiles import get_profile
from ai_code_quality.reporting import build_reports, render_summary

REPOSITORY = "Exohayvan-Systems/service.oneuptime.server"
SOURCE = b"const value = null;\n"


def raw_findings() -> tuple[ToolFinding, ...]:
    compatible = ToolFinding("oxlint", "unicorn(no-null)", "app/source.cjs", 1, 15,
                             1, 18, "Do not use null", "error")
    ordinary = replace(compatible, rule="eslint(no-ternary)", message="Use a clear conditional")
    return compatible, ordinary


def records():
    return parse_records(json.dumps({"schema_version": 1, "records": [{
        "repository": REPOSITORY, "tool": "oxlint", "rule": "unicorn(no-null)",
        "path": "app/source.cjs", "blob_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "line": 1, "column": 15, "end_line": 1, "end_column": 18,
        "rationale": "Explicit baseline contract", "approval_ref": "test approval fixture",
        "regression_ref": "test contract assertion",
    }]}))


def write_source(root: Path, content: bytes = SOURCE) -> None:
    path = root / "app/source.cjs"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def scan(raw: tuple[ToolFinding, ...], accepted: tuple[ToolFinding, ...] = ()) -> ScanResult:
    return ScanResult(
        duplication=DuplicationResult(0.0, 0, 1, ()), functions=(), lint=raw,
        lint_compatible=accepted,
        coverage=CoverageResult(100.0, 1, 1, (), (), ("javascript",)),
    )


def report(current: ScanResult, baseline: ScanResult | None = None, mode: str = "false"):
    profile, enforcement = get_profile("strict"), parse_enforcement(mode)
    result = evaluate(current=current, profile=profile, enforcement=enforcement, baseline=baseline)
    return result, build_reports(
        scan=current, baseline=baseline, profile=profile, enforcement=enforcement,
        evaluation=result, repair_limit=15,
    )


def test_report_preserves_all_raw_findings_and_approved_subset(tmp_path: Path) -> None:
    write_source(tmp_path)
    raw = raw_findings()
    current = scan(raw, classify(tmp_path, REPOSITORY, raw, records()))
    result, reports = report(current)
    lint = reports.full["checks"]["lint"]
    assert not result.passed
    assert lint["raw_count"] == 2
    assert lint["compatible_count"] == 1
    assert lint["count"] == 1
    assert [item["rule"] for item in lint["raw_findings"]] == [item.rule for item in raw]
    assert [item["rule"] for item in lint["compatible_findings"]] == [raw[0].rule]
    assert blocking_lint(current) == (raw[1],)
    assert "2 raw, 1 approved compatibility, 1 blocking" in render_summary(reports)


def test_same_compatibility_on_both_sides_is_not_improvement_credit(tmp_path: Path) -> None:
    write_source(tmp_path)
    raw = raw_findings()
    current = scan(raw, classify(tmp_path, REPOSITORY, raw, records()))
    result, reports = report(current, current, "2")
    assert not result.passed
    assert result.lint.count == 1
    assert result.lint.allowed_count == 0
    assert reports.full["baseline"]["lint_count"] == 2
    assert reports.full["baseline"]["lint_compatible_count"] == 1
    assert reports.full["baseline"]["lint_blocking_count"] == 1
    assert len(reports.full["baseline"]["lint_raw_findings"]) == 2


@pytest.mark.parametrize("current_accepts", [True, False])
def test_asymmetric_compatibility_cannot_create_a_false_ratchet_pass(current_accepts: bool) -> None:
    raw = raw_findings()
    treated = scan(raw, (raw[0],))
    untreated = scan(raw)
    current, baseline = (treated, untreated) if current_accepts else (untreated, treated)
    with pytest.raises(ValueError, match="classifications differ"):
        report(current, baseline, "2")


def test_changed_source_stays_visible_and_blocking(tmp_path: Path) -> None:
    write_source(tmp_path, SOURCE + b"// drift requires another reviewed fingerprint\n")
    raw = raw_findings()
    current = scan(raw, classify(tmp_path, REPOSITORY, raw, records()))
    result, reports = report(current)
    assert current.lint == raw
    assert current.lint_compatible == ()
    assert result.lint.count == 2
    assert reports.full["checks"]["lint"]["raw_count"] == 2
    assert reports.full["checks"]["lint"]["compatible_count"] == 0
    assert not result.passed


@pytest.mark.parametrize("accepted", ["unrelated", "duplicate"])
def test_invalid_accepted_subset_cannot_remove_raw_findings(accepted: str) -> None:
    raw = raw_findings()
    values = (replace(raw[0], path="other.cjs"),) if accepted == "unrelated" else (raw[0], raw[0])
    with pytest.raises(ValueError, match="Invalid compatibility classification"):
        report(scan(raw, values))


def test_packaged_registry_contains_only_the_seven_authorized_occurrences() -> None:
    packaged = parse_records(files("ai_code_quality").joinpath(
        "compatibility_registry.json"
    ).read_text(encoding="utf-8"))
    actual = {(item.rule, item.path, item.line, item.column, item.end_line, item.end_column)
              for item in packaged}
    assert actual == {
        ("unicorn(no-null)", "app/declaration.cjs", 22, 41, 22, 44),
        ("unicorn(no-null)", "app/public-status.cjs", 31, 51, 31, 54),
        ("unicorn(no-null)", "app/public-status.cjs", 38, 66, 38, 69),
        ("unicorn(no-null)", "app/public-status.cjs", 45, 10, 45, 13),
        ("unicorn(no-null)", "app/test/public-contracts.test.cjs", 9, 37, 9, 40),
        ("unicorn(no-null)", "app/test/public-contracts.test.cjs", 19, 46, 19, 49),
        ("unicorn(prefer-event-target)", "app/test/host/startup.test.cjs", 25, 18, 25, 29),
    }
    assert len(packaged) == 7
    assert {item.repository for item in packaged} == {REPOSITORY}
    assert {item.tool for item in packaged} == {"oxlint"}
    assert {item.path: item.blob_sha256 for item in packaged} == {
        "app/declaration.cjs": "ebdd03c2682ab555e1b3bc7e64cf14d8acfbe5aa41741dd5c62af3f319d79aba",
        "app/public-status.cjs": "f17324b719aef8ee19fec7b159a0b6bfe153545b4cb09c1772281b117999474e",
        "app/test/public-contracts.test.cjs":
            "675ac5a0fbbf6a498bb52e03e1a53ab29744a00f464860b176614b71503e4802",
        "app/test/host/startup.test.cjs":
            "a6227dce963f6e84effa8461f003d1a09eb1f6f9498544d6c1548f1b876e1d6b",
    }


def clear_github_output(monkeypatch) -> None:
    for name in ("GITHUB_ACTIONS", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)


def test_cli_classifies_baseline_inside_its_live_worktree(tmp_path: Path, monkeypatch) -> None:
    candidate, baseline = tmp_path / "candidate", tmp_path / "baseline"
    write_source(candidate)
    write_source(baseline)
    clear_github_output(monkeypatch)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(cli, "parse_records", lambda payload: records())
    monkeypatch.setattr(cli, "scan_repository", lambda *args: scan(raw_findings()))
    monkeypatch.setattr(cli, "repository_root_and_relative", lambda root: (candidate, Path(".")))
    monkeypatch.setattr(cli, "resolve_baseline", lambda *args, **kwargs: "a" * 40)
    live = {"baseline": False}
    calls = []

    @contextmanager
    def worktree(*args):
        live["baseline"] = True
        try:
            yield baseline
        finally:
            live["baseline"] = False
            (baseline / "app/source.cjs").unlink()

    def inspect(root, identity, findings, approved):
        if root == baseline:
            assert live["baseline"]
        calls.append((root, identity, approved))
        return classify_with_records(root, identity, findings, approved)

    monkeypatch.setattr(cli, "baseline_worktree", worktree)
    monkeypatch.setattr(cli, "classify_with_records", inspect)
    assert cli.run(["--path", str(candidate), "--level", "strict",
                    "--require-improvement", "2"]) == 1
    assert [item[0] for item in calls] == [candidate, baseline]
    assert all(item[1] == REPOSITORY for item in calls)
    assert calls[0][2] is calls[1][2]
    result = json.loads((candidate / ".ai-code-quality/report.json").read_text())
    assert result["checks"]["lint"]["compatible_count"] == 1
    assert result["baseline"]["lint_compatible_count"] == 1
    current_records = result["checks"]["lint"]["compatibility_records"]
    baseline_records = result["baseline"]["lint_compatibility_records"]
    assert current_records == baseline_records
    assert len(current_records) == 1
    approved = records()[0]
    expected_id = hashlib.sha256(json.dumps(
        asdict(approved), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    assert current_records[0] == {"record_id": expected_id, **asdict(approved)}


def test_cli_without_repository_identity_does_not_approve_anything(tmp_path: Path, monkeypatch):
    write_source(tmp_path)
    clear_github_output(monkeypatch)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(cli, "parse_records", lambda payload: records())
    monkeypatch.setattr(cli, "scan_repository", lambda *args: scan(raw_findings()))
    monkeypatch.setattr(
        cli, "classify_with_records", lambda *args: pytest.fail("identity required")
    )
    assert cli.run(["--path", str(tmp_path), "--level", "strict"]) == 1
    result = json.loads((tmp_path / ".ai-code-quality/report.json").read_text())
    assert result["checks"]["lint"]["raw_count"] == 2
    assert result["checks"]["lint"]["count"] == 2
    assert result["checks"]["lint"]["compatible_count"] == 0


def test_cli_comparison_error_retains_both_raw_scans(tmp_path: Path, monkeypatch) -> None:
    candidate, baseline = tmp_path / "candidate", tmp_path / "baseline"
    write_source(candidate)
    write_source(baseline, SOURCE + b"// unknown historical bytes\n")
    clear_github_output(monkeypatch)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(cli, "parse_records", lambda payload: records())
    monkeypatch.setattr(cli, "scan_repository", lambda *args: scan(raw_findings()))
    monkeypatch.setattr(cli, "repository_root_and_relative", lambda root: (candidate, Path(".")))
    monkeypatch.setattr(cli, "resolve_baseline", lambda *args, **kwargs: "b" * 40)

    @contextmanager
    def worktree(*args):
        yield baseline

    monkeypatch.setattr(cli, "baseline_worktree", worktree)
    with pytest.raises(ValueError, match="classifications differ"):
        cli.run(["--path", str(candidate), "--level", "strict", "--require-improvement", "2"])
    evidence = json.loads((candidate / ".ai-code-quality/report.json").read_text())
    assert evidence["verdict"] == "error"
    assert "classifications differ" in evidence["error"]
    assert evidence["current_raw_lint"] == json.loads(json.dumps(
        [asdict(item) for item in raw_findings()]
    ))
    assert evidence["baseline_raw_lint"] == evidence["current_raw_lint"]
    assert len(evidence["current_compatible_lint"]) == 1
    assert evidence["baseline_compatible_lint"] == []
    record = asdict(records()[0])
    record_id = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert evidence["current_compatibility_records"] == [{"record_id": record_id, **record}]
    assert evidence["baseline_compatibility_records"] == []
