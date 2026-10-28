from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_code_quality.compatibility import classify, classify_with_records, parse_records
from ai_code_quality.models import ToolFinding

REPOSITORY = "Exohayvan-Systems/service.oneuptime.server"
SOURCE = b"const declaration = () => null;\n"


def finding(**changes: object) -> ToolFinding:
    original = ToolFinding("oxlint", "unicorn(no-null)", "app/source.cjs", 1, 27,
                           1, 30, "Do not use null", "error")
    return replace(original, **changes)


def record(**changes: object) -> dict[str, object]:
    result = {
        "repository": REPOSITORY, "tool": "oxlint", "rule": "unicorn(no-null)",
        "path": "app/source.cjs", "blob_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "line": 1, "column": 27, "end_line": 1, "end_column": 30,
        "rationale": "Preserve the explicit public null contract",
        "approval_ref": "reviewed policy decision example",
        "regression_ref": "test contract assertion",
    }
    result.update(changes)
    return result


def registry(*records: dict[str, object]):
    return parse_records(json.dumps({"schema_version": 1, "records": list(records)}))


def source(root: Path, content: bytes = SOURCE) -> None:
    path = root / "app/source.cjs"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_exact_occurrence_classification_keeps_raw_finding(tmp_path: Path) -> None:
    source(tmp_path)
    raw = (finding(), finding(line=2, end_line=2))
    accepted = classify(tmp_path, REPOSITORY, raw, registry(record()))
    assert accepted == (raw[0],)
    assert accepted[0] is raw[0]
    assert len(raw) == 2
    assert tuple(item for item in raw if item not in accepted) == (raw[1],)


@pytest.mark.parametrize("changes", [
    {"tool": "semgrep"}, {"rule": "unicorn(prefer-event-target)"},
    {"path": "app/other.cjs"}, {"line": 2}, {"column": 26},
    {"end_line": 2}, {"end_column": 31},
])
def test_changed_finding_remains_blocking(tmp_path: Path, changes: dict) -> None:
    source(tmp_path)
    raw = (finding(**changes),)
    assert classify(tmp_path, REPOSITORY, raw, registry(record())) == ()
    assert raw == (finding(**changes),)


def test_wrong_repository_does_not_use_another_repository_record(tmp_path: Path) -> None:
    source(tmp_path)
    assert classify(tmp_path, "Other/repository", (finding(),), registry(record())) == ()


def test_changed_source_hash_remains_blocking(tmp_path: Path) -> None:
    source(tmp_path, SOURCE + b"// changed bytes require new review\n")
    assert classify(tmp_path, REPOSITORY, (finding(),), registry(record())) == ()


def test_baseline_and_candidate_have_identical_classification(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "base", tmp_path / "head"
    source(baseline)
    source(candidate)
    records = registry(record())
    raw = (finding(),)
    assert classify(baseline, REPOSITORY, raw, records) == raw
    assert classify(candidate, REPOSITORY, raw, records) == raw
    source(candidate, SOURCE + b"// changed\n")
    assert classify(candidate, REPOSITORY, raw, records) == ()
    assert classify(baseline, REPOSITORY, raw, records) == raw


def test_absent_baseline_findings_gain_no_credit_or_source_read(tmp_path: Path) -> None:
    assert classify(tmp_path, REPOSITORY, (), registry(record())) == ()


def test_approved_historical_variants_use_same_registry(tmp_path: Path) -> None:
    other = SOURCE + b"// separately reviewed historical source\n"
    records = registry(record(), record(blob_sha256=hashlib.sha256(other).hexdigest()))
    source(tmp_path, other)
    assert classify(tmp_path, REPOSITORY, (finding(),), records) == (finding(),)


def test_retired_event_emitter_record_and_finding_stay_blocking(tmp_path: Path) -> None:
    source(tmp_path)
    raw = (finding(rule="unicorn(prefer-event-target)"),)
    with pytest.raises(ValueError, match="unsupported tool or rule"):
        registry(record(rule="unicorn(prefer-event-target)"))
    assert classify(tmp_path, REPOSITORY, raw, registry(record())) == ()
    assert len(raw) == 1


@pytest.mark.parametrize("changes", [
    {"unknown": True}, {"repository": "../repo"}, {"repository": True},
    {"tool": "semgrep"}, {"rule": "security(no-eval)"}, {"rule": []},
    {"path": "../source.cjs"}, {"path": "/source.cjs"}, {"path": "./source.cjs"},
    {"path": "app//source.cjs"}, {"path": "app\\source.cjs"}, {"path": "*.cjs"},
    {"path": "app/[ab].cjs"}, {"path": "app/a?.cjs"}, {"path": "C:/source.cjs"},
    {"path": "app/../source.cjs"}, {"path": "app/./source.cjs"},
    {"path": "app/\x00source.cjs"}, {"blob_sha256": "*"},
    {"blob_sha256": "A" * 64}, {"line": 0}, {"column": True},
    {"end_line": -1}, {"end_column": 1}, {"rationale": " "}, {"approval_ref": ""},
    {"regression_ref": ""},
])
def test_malformed_records_fail_closed(changes: dict) -> None:
    with pytest.raises(ValueError):
        registry(record(**changes))


@pytest.mark.parametrize("payload", [
    "broken", "[]", '{"schema_version": 2, "records": []}',
    '{"schema_version": true, "records": []}',
    '{"schema_version": 1, "records": {}, "unexpected": true}',
    '{"schema_version": 1, "records": {}}',
    '{"schema_version": 1, "records": [], "records": []}',
])
def test_malformed_registry_fails_closed(payload: str) -> None:
    with pytest.raises(ValueError):
        parse_records(payload)


def test_missing_record_field_fails_closed() -> None:
    entry = record()
    del entry["approval_ref"]
    with pytest.raises(ValueError):
        registry(entry)


def test_duplicate_records_and_duplicate_findings_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate compatibility record"):
        registry(record(), record())
    source(tmp_path)
    with pytest.raises(ValueError, match="Duplicate scanner finding"):
        classify(tmp_path, REPOSITORY, (finding(), finding()), registry(record()))


def test_symlink_source_is_rejected_even_if_bytes_match(tmp_path: Path) -> None:
    actual = tmp_path / "actual.cjs"
    actual.write_bytes(SOURCE)
    (tmp_path / "app").mkdir()
    (tmp_path / "app/source.cjs").symlink_to(actual)
    with pytest.raises(ValueError, match="symlinks"):
        classify(tmp_path, REPOSITORY, (finding(),), registry(record()))


def test_symlink_parent_is_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "source.cjs").write_bytes(SOURCE)
    (tmp_path / "app").symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        classify(tmp_path, REPOSITORY, (finding(),), registry(record()))


def test_missing_source_for_reported_finding_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repository file"):
        classify(tmp_path, REPOSITORY, (finding(),), registry(record()))


def test_registry_is_immutable_and_revalidated(tmp_path: Path) -> None:
    records = registry(record())
    with pytest.raises(FrozenInstanceError):
        records[0].line = 2
    invalid = replace(records[0], path="../escape")
    with pytest.raises(ValueError):
        classify(tmp_path, REPOSITORY, (), (invalid,))


def test_invalid_repository_identity_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        classify(tmp_path, "../escape", (), registry(record()))


def test_classification_returns_the_exact_matching_record_evidence(tmp_path: Path) -> None:
    prior = SOURCE + b"// separately reviewed older bytes\n"
    approved = registry(record(), record(blob_sha256=hashlib.sha256(prior).hexdigest(),
                                        approval_ref="older approval reference"))
    source(tmp_path, prior)
    raw = (finding(), finding(line=2, end_line=2))
    accepted, evidence = classify_with_records(tmp_path, REPOSITORY, raw, approved)
    assert accepted == (raw[0],)
    assert evidence == (approved[1],)
    assert evidence[0].blob_sha256 == hashlib.sha256(prior).hexdigest()
    assert evidence[0].approval_ref == "older approval reference"
    assert evidence[0].rationale == "Preserve the explicit public null contract"
