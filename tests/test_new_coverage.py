from dataclasses import replace

import pytest

from ai_code_quality.evaluator import Enforcement, evaluate
from ai_code_quality.models import (
    CoverageFile,
    CoverageReport,
    CoverageResult,
    DuplicationResult,
    ScanResult,
    ToolFinding,
)
from ai_code_quality.profiles import get_profile
from ai_code_quality.reporting import build_reports


def measured(covered=80, total=100):
    item = CoverageFile('src/main.go', 'go', covered, total)
    report = CoverageReport('coverage.out', 'go-coverprofile', covered, total, ('go',), (item,))
    result = CoverageResult(covered / total * 100 if total else 0, covered, total,
                            (item,), (report,), ('go',))
    return ScanResult(DuplicationResult(0, 0, 0, ()), (), coverage=result,
                      source_inventory=(('src/main.go', 'go'),))


def absent():
    return ScanResult(DuplicationResult(0, 0, 0, ()), (), source_inventory=())


def assess(candidate, baseline):
    return evaluate(current=candidate, baseline=baseline, profile=get_profile('strict'),
                    enforcement=Enforcement.improvement(2))


@pytest.mark.parametrize(('covered', 'passed'), [(80, True), (79, False), (0, False)])
def test_new_coverage_has_absolute_floor(covered, passed):
    result = assess(measured(covered), absent())
    assert result.coverage.required == 80
    assert result.coverage.allowed_debt == 0
    assert result.coverage.passed is passed
    assert result.coverage.applicability == 'newly-applicable-absolute'


def test_new_coverage_does_not_skip_other_ratchets():
    finding = ToolFinding('typos', 'typo', 'README.md', 1, 1, 1, 2, 'typo', 'warning')
    baseline = replace(absent(), typos=(finding,))
    candidate = replace(measured(), typos=(finding,))
    result = assess(candidate, baseline)
    assert result.coverage.passed
    assert not result.typos.passed
    assert result.typos.allowed_count == 0


@pytest.mark.parametrize('total', [0, 100])
def test_measured_zero_is_not_no_source(total):
    result = assess(measured(0, total), measured(0, total))
    assert result.coverage.applicability == 'comparable'
    assert result.coverage.required == pytest.approx(1.6)
    assert not result.coverage.passed


@pytest.mark.parametrize('baseline', [
    replace(absent(), source_inventory=None),
    replace(absent(), source_inventory=(('src/main.go', 'go'),)),
    replace(measured(), coverage=replace(measured().coverage, reports=())),
])
def test_unknown_or_missing_baseline_cannot_claim_no_source(baseline):
    with pytest.raises(ValueError):
        assess(measured(), baseline)


@pytest.mark.parametrize('candidate', [
    replace(measured(), coverage=None),
    replace(measured(), source_inventory=None),
    replace(measured(), coverage=replace(measured().coverage, reports=())),
    replace(measured(), coverage=replace(measured().coverage, files=())),
    replace(measured(), source_inventory=(('src/main.go', 'python'),)),
    replace(measured(), source_inventory=(('src/main.go', 'go'), ('src/main.go', 'go'))),
])
def test_new_candidate_requires_consistent_measured_evidence(candidate):
    with pytest.raises(ValueError):
        assess(candidate, absent())


def test_source_removal_cannot_turn_coverage_green():
    with pytest.raises(ValueError, match='Removing supported source'):
        assess(absent(), measured())


def test_report_records_why_coverage_is_newly_applicable():
    candidate, baseline = measured(), absent()
    reports = build_reports(scan=candidate, baseline=baseline,
                            evaluation=assess(candidate, baseline),
                            profile=get_profile('strict'), enforcement=Enforcement.improvement(2),
                            repair_limit=15)
    coverage = reports.full['checks']['coverage']
    assert coverage['applicability'] == 'newly-applicable-absolute'
    assert 'without invented baseline credit' in coverage['reason']
    assert coverage['source_discovery']['eligible_source_count'] == 1
    assert reports.full['baseline']['source_discovery']['eligible_source_count'] == 0
    assert reports.full['baseline']['coverage_percent'] is None


def test_shell_baseline_and_new_go_are_scanned_independently(tmp_path, monkeypatch):
    import json
    from contextlib import contextmanager

    from ai_code_quality import cli

    candidate, baseline = tmp_path / 'candidate', tmp_path / 'baseline'
    candidate.mkdir()
    baseline.mkdir()
    (baseline / 'entrypoint.sh').write_text('echo shell-only\n')
    (candidate / 'main.go').write_text('package main\nfunc main() {}\n')
    (candidate / 'coverage.out').write_text('mode: set\nmain.go:1.1,2.1 5 1\n')
    monkeypatch.setattr(cli, 'run_jscpd', lambda root: DuplicationResult(0, 0, 0, ()))
    monkeypatch.setattr(cli, 'run_lizard', lambda root: ())
    for name in ('run_semgrep', 'run_yamllint', 'run_markdownlint', 'run_typos', 'run_lint'):
        monkeypatch.setattr(cli, name, lambda *args: ())
    monkeypatch.setattr(cli, 'repository_root_and_relative',
                        lambda root: (candidate, type(root)('.')))
    monkeypatch.setattr(cli, 'resolve_baseline', lambda *args, **kwargs: 'b' * 40)

    @contextmanager
    def worktree(*args):
        yield baseline

    monkeypatch.setattr(cli, 'baseline_worktree', worktree)
    assert cli.run(['--path', str(candidate), '--level', 'strict',
                    '--require-improvement', '2']) == 0
    report = json.loads((candidate / '.ai-code-quality/report.json').read_text())
    assert report['baseline_sha'] == 'b' * 40
    assert report['baseline']['source_discovery']['files'] == []
    assert report['baseline']['coverage_percent'] is None
    assert report['checks']['coverage']['required_percent'] == 80
    assert report['checks']['coverage']['observed_percent'] == 100
    (candidate / 'coverage.out').unlink()
    with pytest.raises(ValueError, match='valid measured reports'):
        cli.run(['--path', str(candidate), '--level', 'strict', '--require-improvement', '2'])
    error = json.loads((candidate / '.ai-code-quality/report.json').read_text())
    assert error['verdict'] == 'error'
    assert error['baseline_sha'] == 'b' * 40
    assert error['baseline_source_inventory'] == []
    assert error['current_source_inventory'] == [['main.go', 'go']]
    assert error['current_coverage']['reports'] == []
    assert error['baseline_coverage'] is None


def test_discovery_traversal_error_is_not_empty_source(tmp_path, monkeypatch):
    from ai_code_quality.checks import languages

    def broken_walk(root, *, followlinks, onerror):
        onerror(PermissionError('unreadable source directory'))
        return ()

    monkeypatch.setattr(languages.os, 'walk', broken_walk)
    with pytest.raises(RuntimeError, match='discovery failed'):
        languages.source_files(tmp_path)


@pytest.mark.parametrize(('path', 'language'), [('other.py', 'python'), ('absent.go', 'go')])
def test_new_report_must_cover_discovered_source(path, language):
    candidate = measured()
    item = CoverageFile(path, language, 100, 100)
    report = CoverageReport('coverage.out', 'go-coverprofile', 100, 100, (language,), (item,))
    bad = replace(candidate, coverage=replace(candidate.coverage, files=(item,), reports=(report,),
                                             percentage=100, covered_units=100))
    with pytest.raises(ValueError, match='discovered source'):
        assess(bad, absent())


def test_new_report_cannot_omit_discovered_language():
    candidate = replace(measured(), source_inventory=(('src/main.go', 'go'), ('app.py', 'python')),
                        coverage=replace(measured().coverage, detected_languages=('go', 'python')))
    with pytest.raises(ValueError, match='omits a discovered language'):
        assess(candidate, absent())


def test_new_report_cannot_invent_percentage():
    candidate = replace(measured(0), coverage=replace(measured(0).coverage, percentage=100))
    with pytest.raises(ValueError, match='measured counts'):
        assess(candidate, absent())


def test_unknown_baseline_inventory_cannot_justify_skipping_candidate():
    with pytest.raises(ValueError, match='discovery evidence'):
        assess(absent(), replace(absent(), source_inventory=None))


def test_expanded_newt_scope_below_floor_remains_blocking():
    result = assess(measured(1127, 1591), absent())
    assert result.coverage.required == 80
    assert not result.coverage.passed
