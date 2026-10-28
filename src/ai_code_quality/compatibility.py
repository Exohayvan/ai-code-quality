"""Exact, action-owned compatibility records; raw scanner findings stay intact."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ai_code_quality.models import ToolFinding

_RULES = frozenset({"unicorn(no-null)", "unicorn(prefer-event-target)"})
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIELDS = frozenset({
    "repository", "tool", "rule", "path", "blob_sha256", "line", "column",
    "end_line", "end_column", "rationale", "approval_ref", "regression_ref",
})


@dataclass(frozen=True, slots=True)
class CompatibilityRecord:
    repository: str
    tool: str
    rule: str
    path: str
    blob_sha256: str
    line: int
    column: int
    end_line: int
    end_column: int
    rationale: str
    approval_ref: str
    regression_ref: str


def _repository(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _REPOSITORY.fullmatch(value) is not None
        and not any(part in {".", ".."} for part in value.split("/"))
    )


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\\*?[]\x00"):
        raise ValueError("Compatibility path must be an exact relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Unsafe compatibility path")
    if path.as_posix() != value or ":" in value:
        raise ValueError("Compatibility path is not normalized")
    return value


def _position(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Invalid compatibility source position")
    return value


def _record(value: Any) -> CompatibilityRecord:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid compatibility record fields")
    repository = value["repository"]
    if not _repository(repository):
        raise ValueError("Invalid compatibility repository")
    if (
        value["tool"] != "oxlint"
        or not isinstance(value["rule"], str)
        or value["rule"] not in _RULES
    ):
        raise ValueError("Compatibility record uses an unsupported tool or rule")
    digest = value["blob_sha256"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("Invalid compatibility source digest")
    _path(value["path"])
    for key in ("line", "column", "end_line", "end_column"):
        _position(value[key])
    if (value["end_line"], value["end_column"]) < (value["line"], value["column"]):
        raise ValueError("Reversed compatibility source span")
    for key in ("rationale", "approval_ref", "regression_ref"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError("Compatibility record requires rationale and approval reference")
    return CompatibilityRecord(**value)


def _identity(value: ToolFinding | CompatibilityRecord) -> tuple[str | int, ...]:
    return (value.tool, value.rule, value.path, value.line, value.column,
            value.end_line, value.end_column)


def _validated(records: tuple[CompatibilityRecord, ...]) -> tuple[CompatibilityRecord, ...]:
    checked = []
    seen = set()
    for record in records:
        if not isinstance(record, CompatibilityRecord):
            raise ValueError("Unvalidated compatibility record")
        record = _record(asdict(record))
        key = (record.repository, record.blob_sha256, *_identity(record))
        if key in seen:
            raise ValueError("Duplicate compatibility record")
        seen.add(key)
        checked.append(record)
    return tuple(checked)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate compatibility JSON key")
        result[key] = value
    return result


def parse_records(payload: str) -> tuple[CompatibilityRecord, ...]:
    """Validate a registry supplied by the pinned tool, never candidate configuration."""
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid compatibility registry JSON") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise ValueError("Invalid compatibility registry fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported compatibility registry version")
    if not isinstance(value["records"], list):
        raise ValueError("Compatibility records must be a list")
    return _validated(tuple(_record(item) for item in value["records"]))


def _digest(repository: Path, path: str) -> str:
    root = repository.resolve()
    source = root
    for part in PurePosixPath(path).parts:
        source /= part
        if source.is_symlink():
            raise ValueError("Compatibility source cannot traverse symlinks")
    try:
        if not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError("Compatibility source is not a repository file")
        return hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("Unable to read compatibility source") from exc


def classify_with_records(
    repository: Path,
    repository_identity: str,
    findings: tuple[ToolFinding, ...],
    records: tuple[CompatibilityRecord, ...],
) -> tuple[tuple[ToolFinding, ...], tuple[CompatibilityRecord, ...]]:
    """Return exact approved occurrences without editing or discarding raw findings.

    Both candidate and baseline must call this function with the same registry.
    A changed source hash simply does not match: its raw finding remains blocking.
    An absent baseline finding receives neither a match nor invented baseline credit.
    """
    if not _repository(repository_identity):
        raise ValueError("Invalid scanned repository identity")
    approved = {
        (record.blob_sha256, *_identity(record)): record
        for record in _validated(records) if record.repository == repository_identity
    }
    potential = {key[1:] for key in approved}
    seen = set()
    digests: dict[str, str] = {}
    accepted = []
    accepted_records = []
    for finding in findings:
        key = _identity(finding)
        if key in seen:
            raise ValueError("Duplicate scanner finding cannot receive compatibility treatment")
        seen.add(key)
        if key not in potential:
            continue
        _path(finding.path)
        if finding.path not in digests:
            digests[finding.path] = _digest(repository, finding.path)
        matched = approved.get((digests[finding.path], *key))
        if matched is not None:
            accepted.append(finding)
            accepted_records.append(matched)
    return tuple(accepted), tuple(accepted_records)


def classify(
    repository: Path,
    repository_identity: str,
    findings: tuple[ToolFinding, ...],
    records: tuple[CompatibilityRecord, ...],
) -> tuple[ToolFinding, ...]:
    """Compatibility wrapper for callers needing only the accepted raw findings."""
    accepted, _records = classify_with_records(repository, repository_identity, findings, records)
    return accepted
