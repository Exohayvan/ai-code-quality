from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Final

from ai_code_quality.checks.common import (
    COVERAGE_REPORT_EXACT_NAMES,
    COVERAGE_REPORT_XML_PREFIXES,
    normalize_scanner_path,
)
from ai_code_quality.checks.languages import language_for_path, source_files
from ai_code_quality.models import CoverageFile, CoverageReport, CoverageResult

_MAX_REPORT_BYTES: Final = 50 * 1024 * 1024
_MAX_REPORTS: Final = 64
_DISCOVERY_EXCLUDES: Final = frozenset(
    {".git", ".venv", ".ai-code-quality", "node_modules", "vendor", ".generated"}
)
_GO_LINE: Final = re.compile(r"^(.+):\d+\.\d+,\d+\.\d+\s+(\d+)\s+(\d+)$")


def _source_snapshot(repository: Path) -> tuple[tuple[str, str], ...]:
    snapshot: list[tuple[str, str]] = []
    for relative, _language in source_files(repository):
        digest = hashlib.sha256()
        try:
            with (repository / relative).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise RuntimeError(f"Unable to snapshot coverage source: {relative}") from exc
        snapshot.append((relative, digest.hexdigest()))
    return tuple(snapshot)


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Invalid coverage {label}")
    return value


def _source_path(raw: str, repository: Path) -> str:
    if not raw or "\x00" in raw:
        raise ValueError("Coverage report returned unsafe path")
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            raw = candidate.resolve().relative_to(repository.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Coverage report returned unsafe path: {raw!r}") from exc
    try:
        return normalize_scanner_path(raw)
    except ValueError as exc:
        raise ValueError(f"Coverage report returned unsafe path: {raw!r}") from exc


def _coverage_file(
    raw_path: str, covered: Any, total: Any, repository: Path
) -> CoverageFile:
    path = _source_path(raw_path, repository)
    covered_units = _integer(covered, "covered count")
    total_units = _integer(total, "total count")
    if covered_units > total_units:
        raise ValueError("Coverage covered count exceeds total count")
    language = language_for_path(path)
    if language is None:
        raise ValueError(f"Coverage source has an unsupported language: {path}")
    return CoverageFile(path, language, covered_units, total_units)


def _report(
    path: Path,
    repository: Path,
    format_name: str,
    files: list[CoverageFile],
) -> CoverageReport:
    ordered = tuple(sorted(files, key=lambda item: (item.path, item.language)))
    covered = sum(item.covered_units for item in ordered)
    total = sum(item.total_units for item in ordered)
    relative = path.resolve().relative_to(repository.resolve()).as_posix()
    return CoverageReport(
        path=relative,
        format=format_name,
        covered_units=covered,
        total_units=total,
        languages=tuple(sorted({item.language for item in ordered})),
        files=ordered,
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label}")
    return value


def _parse_json(path: Path, repository: Path) -> CoverageReport:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON coverage report: {path}") from exc
    root = _mapping(payload, "coverage JSON")
    files: list[CoverageFile] = []
    if isinstance(root.get("files"), dict):
        for raw_path, raw_data in root["files"].items():
            if not isinstance(raw_path, str):
                raise ValueError("Invalid coverage.py source path")
            data = _mapping(raw_data, "coverage.py file")
            summary = _mapping(data.get("summary"), "coverage.py summary")
            files.append(
                _coverage_file(
                    raw_path,
                    summary.get("covered_lines"),
                    summary.get("num_statements"),
                    repository,
                )
            )
        return _report(path, repository, "coverage.py-json", files)

    for raw_path, raw_data in root.items():
        if raw_path == "total":
            continue
        if not isinstance(raw_path, str):
            raise ValueError("Invalid Istanbul source path")
        data = _mapping(raw_data, "Istanbul file")
        lines = _mapping(data.get("lines"), "Istanbul lines")
        files.append(
            _coverage_file(
                raw_path, lines.get("covered"), lines.get("total"), repository
            )
        )
    if not files:
        raise ValueError("Coverage JSON contains no per-file coverage")
    return _report(path, repository, "istanbul-json-summary", files)


def _parse_lcov(path: Path, repository: Path) -> CoverageReport:
    records: dict[str, dict[int, int]] = defaultdict(dict)
    current: str | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid LCOV report: {path}") from exc
    for raw in lines:
        if raw.startswith("SF:"):
            current = _source_path(raw[3:], repository)
        elif raw.startswith("DA:"):
            if current is None:
                raise ValueError("LCOV DA record appears before SF")
            pieces = raw[3:].split(",")
            if len(pieces) < 2:
                raise ValueError("Invalid LCOV DA record")
            try:
                line_number = int(pieces[0])
                count = int(pieces[1])
            except ValueError as exc:
                raise ValueError("Invalid LCOV DA count") from exc
            if line_number < 1 or count < 0:
                raise ValueError("Invalid LCOV DA count")
            records[current][line_number] = max(records[current].get(line_number, 0), count)
        elif raw == "end_of_record":
            current = None
    files = [
        _coverage_file(
            source,
            sum(count > 0 for count in counts.values()),
            len(counts),
            repository,
        )
        for source, counts in records.items()
    ]
    if not files:
        raise ValueError("LCOV report contains no line coverage")
    return _report(path, repository, "lcov", files)


def _parse_cobertura(root: ET.Element, path: Path, repository: Path) -> CoverageReport:
    grouped: dict[str, dict[int, int]] = defaultdict(dict)
    for class_node in root.findall(".//class"):
        raw_path = class_node.get("filename")
        if raw_path is None:
            raise ValueError("Cobertura class is missing filename")
        source = _source_path(raw_path, repository)
        line_hits = grouped[source]
        for line in class_node.findall("./lines/line"):
            try:
                number = int(line.attrib["number"])
                hits = int(line.attrib["hits"])
            except (KeyError, ValueError) as exc:
                raise ValueError("Invalid Cobertura line") from exc
            if number < 1 or hits < 0:
                raise ValueError("Invalid Cobertura line")
            line_hits[number] = max(line_hits.get(number, 0), hits)
    files = [
        _coverage_file(
            source,
            sum(hits > 0 for hits in line_hits.values()),
            len(line_hits),
            repository,
        )
        for source, line_hits in grouped.items()
    ]
    if not files:
        raise ValueError("Cobertura report contains no line coverage")
    return _report(path, repository, "cobertura-xml", files)


def _parse_jacoco(root: ET.Element, path: Path, repository: Path) -> CoverageReport:
    files: list[CoverageFile] = []
    for package in root.findall("./package"):
        package_name = package.get("name", "")
        for source in package.findall("./sourcefile"):
            source_name = source.get("name")
            if source_name is None:
                raise ValueError("JaCoCo sourcefile is missing name")
            counter = next(
                (item for item in source.findall("./counter") if item.get("type") == "LINE"),
                None,
            )
            if counter is None:
                continue
            try:
                missed = int(counter.attrib["missed"])
                covered = int(counter.attrib["covered"])
            except (KeyError, ValueError) as exc:
                raise ValueError("Invalid JaCoCo line counter") from exc
            raw_path = f"{package_name}/{source_name}" if package_name else source_name
            files.append(_coverage_file(raw_path, covered, covered + missed, repository))
    if not files:
        raise ValueError("JaCoCo report contains no line coverage")
    return _report(path, repository, "jacoco-xml", files)


def _parse_xml(path: Path, repository: Path) -> CoverageReport:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Invalid XML coverage report: {path}") from exc
    if root.tag == "coverage":
        return _parse_cobertura(root, path, repository)
    if root.tag == "report":
        return _parse_jacoco(root, path, repository)
    raise ValueError(f"Unsupported XML coverage format: {root.tag}")


def _parse_go(path: Path, repository: Path) -> CoverageReport:
    grouped: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid Go coverage report: {path}") from exc
    if not lines or not lines[0].startswith("mode:"):
        raise ValueError("Invalid Go coverage mode")
    for raw in lines[1:]:
        if not raw.strip():
            continue
        match = _GO_LINE.fullmatch(raw)
        if match is None:
            raise ValueError("Invalid Go coverage record")
        raw_path, statements_raw, count_raw = match.groups()
        statements = int(statements_raw)
        count = int(count_raw)
        if statements < 0 or count < 0:
            raise ValueError("Invalid Go coverage count")
        source = _source_path(raw_path, repository)
        covered, total = grouped[source]
        grouped[source] = (covered + (statements if count > 0 else 0), total + statements)
    files = [
        _coverage_file(source, covered, total, repository)
        for source, (covered, total) in grouped.items()
    ]
    if not files:
        raise ValueError("Go coverage report contains no statement coverage")
    return _report(path, repository, "go-coverprofile", files)


def parse_coverage_report(path: Path, repository: Path) -> CoverageReport:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Unable to read coverage report: {path}") from exc
    if size > _MAX_REPORT_BYTES:
        raise ValueError(f"Coverage report exceeds {_MAX_REPORT_BYTES} bytes: {path}")
    name = path.name.lower()
    if name in {"coverage.json", "coverage-summary.json"}:
        return _parse_json(path, repository)
    if name in {"lcov.info", "coverage.lcov"}:
        return _parse_lcov(path, repository)
    if name in {"coverage.out", "cover.out"}:
        return _parse_go(path, repository)
    if name.endswith(".xml"):
        return _parse_xml(path, repository)
    raise ValueError(f"Unsupported coverage report: {path}")


def _candidate_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in COVERAGE_REPORT_EXACT_NAMES or (
        lowered.endswith(".xml")
        and lowered.startswith(COVERAGE_REPORT_XML_PREFIXES)
    )


def discover_coverage_reports(repository: Path) -> tuple[Path, ...]:
    root = repository.resolve()
    reports: list[Path] = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in _DISCOVERY_EXCLUDES)
        for name in sorted(files):
            if not _candidate_name(name):
                continue
            reports.append(Path(directory) / name)
            if len(reports) > _MAX_REPORTS:
                raise ValueError(f"More than {_MAX_REPORTS} coverage reports were discovered")
    return tuple(reports)


def run_coverage(
    repository: Path,
    languages: tuple[str, ...],
    command: tuple[str, ...] | None = None,
) -> CoverageResult:
    if not languages:
        return CoverageResult(0.0, 0, 0, (), (), ())
    command_started_ns: int | None = None
    if command:
        before = _source_snapshot(repository)
        command_started_ns = time.time_ns()
        try:
            subprocess.run(
                command,
                cwd=repository,
                check=True,
                timeout=1800,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Coverage command was not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Coverage command timed out after 1800 seconds") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Coverage command exited with code {exc.returncode}"
            ) from exc
        if _source_snapshot(repository) != before:
            raise RuntimeError("Coverage command modified supported source files")
    report_paths = discover_coverage_reports(repository)
    if command_started_ns is not None and not any(
        path.stat().st_mtime_ns >= command_started_ns for path in report_paths
    ):
        raise RuntimeError("Coverage command did not generate a fresh supported report")
    reports = tuple(parse_coverage_report(path, repository) for path in report_paths)
    files_by_path: dict[str, CoverageFile] = {}
    for report in reports:
        for item in report.files:
            existing = files_by_path.get(item.path)
            if existing is not None and existing != item:
                raise ValueError(f"Conflicting coverage for source file: {item.path}")
            files_by_path[item.path] = item
    files = tuple(sorted(files_by_path.values(), key=lambda item: item.path))
    covered = sum(item.covered_units for item in files)
    total = sum(item.total_units for item in files)
    percentage = 0.0 if total == 0 else covered / total * 100.0
    if not math.isfinite(percentage) or not 0.0 <= percentage <= 100.0:
        raise ValueError("Invalid aggregate coverage percentage")
    return CoverageResult(
        percentage=percentage,
        covered_units=covered,
        total_units=total,
        files=files,
        reports=reports,
        detected_languages=tuple(sorted(languages)),
    )
