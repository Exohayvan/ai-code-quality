from dataclasses import replace
from pathlib import Path

import pytest

from ai_code_quality.checks.opaque_go import classify
from ai_code_quality.evaluator import blocking_typos, evaluate, parse_enforcement
from ai_code_quality.models import CoverageResult, DuplicationResult, ScanResult, ToolFinding
from ai_code_quality.profiles import get_profile
from ai_code_quality.reporting import build_reports, render_summary


def classified(root: Path):
    line = '+\tgolang.org/x/exp v0.0.0-20251113190631-e25ba8c21ef6'
    header = '--- a/go.mod\n+++ b/go.mod\n@@ -0,0 +1 @@\n'
    (root / 'dependency.patch').write_text(header + line + '\n')
    start = line.index('ba8') + 1
    raw = ToolFinding('typos', 'typo', 'dependency.patch', 4, start, 4,
                      start + 1, "Possible typo 'ba'", 'warning')
    record = classify(raw, root)
    assert record is not None
    return ScanResult(DuplicationResult(0, 0, 1, ()), (), typos=(raw,),
                      typos_opaque=((raw, record),),
                      coverage=CoverageResult(100, 1, 1, (), (), ('python',)))


def test_raw_report_and_symmetric_ratchet(tmp_path):
    scan = classified(tmp_path)
    profile, enforcement = get_profile('strict'), parse_enforcement('2')
    result = evaluate(current=scan, baseline=scan, profile=profile, enforcement=enforcement)
    assert result.passed
    report = build_reports(scan=scan, baseline=scan, profile=profile,
                           enforcement=enforcement, evaluation=result, repair_limit=15)
    check = report.full['checks']['typos']
    assert (check['raw_count'], check['opaque_count'], check['count']) == (1, 1, 0)
    assert len(check['raw_findings']) == len(check['opaque_records']) == 1
    assert report.full['baseline']['typos_blocking_count'] == 0
    assert report.full['baseline']['typos_raw_findings'] == check['raw_findings']
    assert '1 raw, 1 opaque revision diagnostics, 0 blocking' in render_summary(report)


def test_prose_still_blocks_and_does_not_gain_credit(tmp_path):
    scan = classified(tmp_path)
    prose = replace(scan.typos[0], path='README.md', message="Possible typo 'teh'")
    current = replace(scan, typos=(*scan.typos, prose))
    result = evaluate(current=current, baseline=current, profile=get_profile('strict'),
                      enforcement=parse_enforcement('2'))
    assert not result.passed
    assert result.typos.count == 1 and result.typos.allowed_count == 0
    assert blocking_typos(current) == (prose,)


def test_duplicate_or_unrelated_classification_fails(tmp_path):
    scan = classified(tmp_path)
    with pytest.raises(ValueError, match='classification'):
        blocking_typos(replace(scan, typos_opaque=scan.typos_opaque * 2))
    with pytest.raises(ValueError, match='evidence'):
        blocking_typos(replace(scan, typos_opaque=((scan.typos[0],
                      replace(scan.typos_opaque[0][1], path='other.patch')),)))


def test_changed_source_stops_lexical_classification(tmp_path):
    scan = classified(tmp_path)
    (tmp_path / 'dependency.patch').write_text('prose e25ba8c21ef6\n')
    assert classify(scan.typos[0], tmp_path) is None
