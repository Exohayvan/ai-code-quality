"""Conservative lexical classification, never dependency authenticity validation."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_code_quality.checks.common import normalize_scanner_path
from ai_code_quality.models import ToolFinding

_NUMBER = r"(?:0|[1-9][0-9]*)"
_BASE = rf"v{_NUMBER}"
_VERSION = re.compile(
    rf"(?:{_BASE}\.0\.0-|{_BASE}\.{_NUMBER}\.[1-9][0-9]*-0\."
    rf"|{_BASE}\.{_NUMBER}\.{_NUMBER}-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*\.0\.)"
    r"(?P<timestamp>[0-9]{14})-(?P<revision>[0-9a-f]{12})(?:\+incompatible)?"
)
_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_MODULE = rf"{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})+(?:/[A-Za-z0-9_~-][A-Za-z0-9_~.-]*)+"
_RECORD = re.compile(
    rf"[ \t]*(?P<directive>require[ \t]+)?(?P<module>{_MODULE})[ \t]+(?P<version>\S+)(?P<tail>.*)"
)
_HUNK = re.compile(r"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?")
_CHECKSUM = re.compile(r" h1:[A-Za-z0-9+/]{43}=")


@dataclass(frozen=True, slots=True)
class OpaqueGoRevision:
    path: str
    line: int
    start_column: int
    end_column: int
    revision: str
    reason: str
    source_sha256: str


def _consume_hunk(lines: list[str], index: int) -> tuple[int, list[int], tuple[int, int]]:
    match = _HUNK.fullmatch(lines[index])
    if match is None:
        raise ValueError("Invalid hunk")
    old_start, old_count, new_start, new_count = match.groups()
    old, new = int(old_count or 1), int(new_count or 1)
    ends = (int(old_start) + (old or 1), int(new_start) + (new or 1))
    if not (old or new) or (old and int(old_start) == 0) or (new and int(new_start) == 0):
        raise ValueError("Invalid hunk start")
    candidates: list[int] = []
    index += 1
    while old or new:
        if index >= len(lines) or not lines[index]:
            raise ValueError("Truncated hunk")
        marker = lines[index][0]
        if marker not in " +-":
            raise ValueError("Invalid hunk body")
        old -= int(marker in " -")
        new -= int(marker in " +")
        if min(old, new) < 0:
            raise ValueError("Hunk count mismatch")
        if marker in " +":
            candidates.append(index)
        index += 1
        if index < len(lines) and lines[index] == r"\ No newline at end of file":
            index += 1
    return index, candidates, ends


def _patch_records(lines: list[str]) -> dict[int, str]:
    records: dict[int, str] = {}
    targets: set[str] = set()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- a/") or index + 1 >= len(lines):
            raise ValueError("Expected paired file headers")
        target = lines[index][6:]
        if lines[index + 1] != f"+++ b/{target}":
            raise ValueError("Mismatched file headers")
        normalized = normalize_scanner_path(target)
        if normalized != target or normalized in targets:
            raise ValueError("Duplicate patch target")
        targets.add(normalized)
        index += 2
        previous = (0, 0)
        count = 0
        while index < len(lines) and lines[index].startswith("@@ "):
            header = _HUNK.fullmatch(lines[index])
            if header is None:
                raise ValueError("Invalid or overlapping hunks")
            old_start = int(header.group(1)) + int(header.group(2) == "0")
            new_start = int(header.group(3)) + int(header.group(4) == "0")
            old_gap = old_start - previous[0]
            new_gap = new_start - previous[1]
            if min(old_gap, new_gap) < 0 or old_gap != new_gap:
                raise ValueError("Inconsistent unchanged hunk gap")
            index, candidates, previous = _consume_hunk(lines, index)
            if Path(target).name in {"go.mod", "go.sum"}:
                records.update((line, Path(target).name) for line in candidates)
            count += 1
        if count == 0:
            raise ValueError("Empty patch section")
    return records


def _revision_span(line: str, target: str) -> tuple[int, int] | None:
    record = _RECORD.fullmatch(line[1:])
    if record is None or any(
        part in {".", ".."} or part.endswith(".") for part in record["module"].split("/")
    ):
        return None
    version = record["version"]
    if target == "go.sum":
        if record["directive"] is not None or not _CHECKSUM.fullmatch(record["tail"]):
            return None
        version = version.removesuffix("/go.mod")
    elif record["tail"] not in {"", " // indirect"}:
        return None
    match = _VERSION.fullmatch(version)
    if match is None:
        return None
    prerelease = version.split("-", 1)[1].split(match["timestamp"], 1)[0]
    if any(part.isdigit() and len(part) > 1 and part.startswith("0")
           for part in prerelease.rstrip(".").split(".")):
        return None
    try:
        datetime.strptime(match["timestamp"], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    start = 1 + record.start("version") + match.start("revision")
    return start, start + len(match["revision"])


def classify(finding: ToolFinding, repository: Path) -> OpaqueGoRevision | None:
    """Return evidence for an opaque revision substring; retain the raw finding."""
    if finding.tool != "typos" or finding.rule != "typo" or finding.path_context:
        return None
    try:
        path = normalize_scanner_path(finding.path)
        source = (repository / path).resolve(strict=True)
        source.relative_to(repository.resolve(strict=True))
        if source.suffix not in {".patch", ".diff"} or not source.is_file():
            return None
        data = source.read_bytes()
        lines = data.decode("utf-8").splitlines()
        records = _patch_records(lines)
        index = finding.line - 1
        if index not in records or finding.end_line != finding.line:
            return None
        line = lines[index]
        span = _revision_span(line, records[index])
        start, end = finding.column - 1, finding.end_column
        if span is None or not (span[0] <= start < end <= span[1]):
            return None
        if finding.message != f"Possible typo {line[start:end]!r}":
            return None
    except (OSError, UnicodeError, ValueError):
        return None
    return OpaqueGoRevision(
        path=path, line=finding.line, start_column=span[0] + 1, end_column=span[1],
        revision=line[span[0]:span[1]], reason="opaque-go-pseudo-version-revision",
        source_sha256=hashlib.sha256(data).hexdigest(),
    )
