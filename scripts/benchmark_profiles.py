from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

PROFILES: Final[tuple[str, ...]] = (
    "none",
    "minimal",
    "basic",
    "standard",
    "strict",
    "hardened",
    "maximum",
)
PROFILE_CONTRACT: Final[dict[str, dict[str, object]]] = {
    "none": {
        "duplication": None,
        "complexity": None,
        "function_length": None,
        "arguments": None,
        "policy": None,
        "typos": False,
    },
    "minimal": {
        "duplication": 20.0,
        "complexity": 30,
        "function_length": 200,
        "arguments": 10,
        "policy": None,
        "typos": True,
    },
    "basic": {
        "duplication": 15.0,
        "complexity": 20,
        "function_length": 150,
        "arguments": 9,
        "policy": "basic",
        "typos": True,
    },
    "standard": {
        "duplication": 10.0,
        "complexity": 15,
        "function_length": 100,
        "arguments": 7,
        "policy": "standard",
        "typos": True,
    },
    "strict": {
        "duplication": 0.0,
        "complexity": 10,
        "function_length": 75,
        "arguments": 6,
        "policy": "strict",
        "typos": True,
    },
    "hardened": {
        "duplication": 0.0,
        "complexity": 8,
        "function_length": 60,
        "arguments": 5,
        "policy": "hardened",
        "typos": True,
    },
    "maximum": {
        "duplication": 0.0,
        "complexity": 5,
        "function_length": 40,
        "arguments": 4,
        "policy": "maximum",
        "typos": True,
    },
}
REPOSITORY_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
RUN_NUMBER_PATTERN: Final = re.compile(r"^[1-9][0-9]*$")
RUNNER_VALUE_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]+$")
COMPARABLE_STATUSES: Final = frozenset({"pass", "quality-fail"})
ALL_STATUSES: Final = COMPARABLE_STATUSES | frozenset({"infrastructure-fail"})
RUN_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "action_sha",
    "run_id",
    "run_attempt",
    "runner_os",
    "runner_arch",
)
EXPECTED_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "cell_id",
    "language",
    "repository",
    "role",
    "sha",
    "profile",
    "action_sha",
    "run_id",
    "runner_os",
    "runner_arch",
)
IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "cell_id",
    "language",
    "repository",
    "role",
    "sha",
    "profile",
    *RUN_IDENTITY_FIELDS,
)
REPORT_CHECKS: Final = frozenset(
    {
        "duplication",
        "complexity",
        "function_length",
        "arguments",
        "semgrep",
        "yamllint",
        "markdownlint",
        "typos",
    }
)
REPORT_TOOLS: Final = frozenset(
    {"excluded_directories", "jscpd", "lizard", "semgrep", "yamllint", "markdownlint", "typos"}
)
REPORT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "verdict",
        "quality_verdict",
        "profile",
        "enforcement",
        "tools",
        "checks",
        "baseline",
    }
)
CHECK_FIELDS: Final[dict[str, frozenset[str]]] = {
    "duplication": frozenset(
        {
            "status",
            "blocking",
            "observed_percent",
            "allowed_percent",
            "duplicated_lines",
            "total_lines",
            "total_tokens",
            "families",
            "clones",
        }
    ),
    "complexity": frozenset(
        {
            "status",
            "blocking",
            "maximum_observed_ccn",
            "maximum_allowed_ccn",
            "debt",
            "allowed_debt",
            "functions",
            "findings",
        }
    ),
    "function_length": frozenset(
        {"status", "blocking", "maximum", "debt", "allowed_debt", "findings"}
    ),
    "arguments": frozenset({"status", "blocking", "maximum", "debt", "allowed_debt", "findings"}),
    **{
        name: frozenset(
            {
                "status",
                "blocking",
                "count",
                "allowed_count",
                "immediate_count",
                "findings",
            }
        )
        for name in ("semgrep", "yamllint", "markdownlint", "typos")
    },
}
TOOL_FIELDS: Final = {
    "jscpd": frozenset({"version", "minimum_lines", "minimum_tokens", "comments_ignored"}),
    "lizard": frozenset({"version", "metrics"}),
    "semgrep": frozenset({"version", "policy"}),
    "yamllint": frozenset({"version", "policy"}),
    "markdownlint": frozenset({"version", "policy"}),
    "typos": frozenset({"version", "enabled"}),
}
CHECK_TYPES: Final = {
    "duplication": {
        "status": "status",
        "blocking": "bool",
        "observed_percent": "optional-number",
        "allowed_percent": "optional-number",
        "duplicated_lines": "integer",
        "total_lines": "integer",
        "total_tokens": "integer",
        "families": "list",
        "clones": "list",
    },
    "complexity": {
        "status": "status",
        "blocking": "bool",
        "maximum_observed_ccn": "integer",
        "maximum_allowed_ccn": "optional-integer",
        "debt": "integer",
        "allowed_debt": "optional-integer",
        "functions": "list",
        "findings": "list",
    },
    **{
        name: {
            "status": "status",
            "blocking": "bool",
            "maximum": "optional-integer",
            "debt": "integer",
            "allowed_debt": "optional-integer",
            "findings": "list",
        }
        for name in ("function_length", "arguments")
    },
    **{
        name: {
            "status": "status",
            "blocking": "bool",
            "count": "integer",
            "allowed_count": "optional-integer",
            "immediate_count": "integer",
            "findings": "list",
        }
        for name in ("semgrep", "yamllint", "markdownlint", "typos")
    },
}
TOOL_TYPES: Final = {
    "jscpd": {
        "version": "string",
        "minimum_lines": "integer",
        "minimum_tokens": "integer",
        "comments_ignored": "bool",
    },
    "lizard": {"version": "string", "metrics": "list"},
    **{
        name: {"version": "string", "policy": "optional-string"}
        for name in ("semgrep", "yamllint", "markdownlint")
    },
    "typos": {"version": "string", "enabled": "bool"},
}
FUNCTION_TYPES: Final = {
    "path": "string",
    "start_line": "integer",
    "end_line": "integer",
    "symbol": "string",
    "ccn": "integer",
    "length": "integer",
    "parameter_count": "integer",
}
TOOL_FINDING_TYPES: Final = {
    "tool": "string",
    "rule": "string",
    "path": "string",
    "line": "integer",
    "column": "integer",
    "end_line": "integer",
    "end_column": "integer",
    "message": "string",
    "severity": "string",
    "suggestions": "list",
    "location": "string",
}
FRAGMENT_TYPES: Final = {"path": "string", "start_line": "integer", "end_line": "integer"}
CLONE_FIELDS: Final = frozenset({"first", "second", "lines", "tokens", "language"})
FAMILY_FIELDS: Final = frozenset({"id", "check", "kind", "languages", "lines", "fragments"})
TOOL_SEVERITIES: Final = {
    "semgrep": frozenset({"error", "warning", "info"}),
    "yamllint": frozenset({"error", "warning"}),
    "markdownlint": frozenset({"error", "warning"}),
    "typos": frozenset({"warning"}),
}
Resolver = Callable[[str], tuple[str, str]]


def _object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"Expected an object at {location}")
    return value


def _list(value: object, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"Expected an array at {location}")
    return value


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected a non-empty string at {location}")
    return value


def _repository(value: object, location: str) -> str:
    item = _object(value, location)
    repository = _string(item.get("repository"), f"{location}.repository")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(f"Invalid GitHub repository at {location}: {repository!r}")
    if set(item) != {"repository"}:
        raise ValueError(f"Unexpected repository fields at {location}")
    return repository


def _manifest_profiles(root: dict[str, Any]) -> list[str]:
    profiles = tuple(
        _string(item, "profiles[]") for item in _list(root.get("profiles"), "profiles")
    )
    if profiles != PROFILES:
        raise ValueError(f"Benchmark profiles must exactly match {', '.join(PROFILES)}")
    return list(profiles)


def _manifest_language(raw_language: object, index: int) -> dict[str, Any]:
    location = f"languages[{index}]"
    language = _object(raw_language, location)
    if set(language) != {"id", "stable", "rotating"}:
        raise ValueError(f"Unexpected language fields at {location}")
    language_id = _string(language.get("id"), f"{location}.id")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", language_id):
        raise ValueError(f"Invalid language id: {language_id!r}")
    stable = [
        _repository(item, f"{location}.stable[{item_index}]")
        for item_index, item in enumerate(_list(language.get("stable"), f"{location}.stable"))
    ]
    rotating = [
        _repository(item, f"{location}.rotating[{item_index}]")
        for item_index, item in enumerate(_list(language.get("rotating"), f"{location}.rotating"))
    ]
    if len(stable) != 2:
        raise ValueError(f"{language_id} must define exactly two stable repositories")
    if len(rotating) < 3:
        raise ValueError(f"{language_id} must define at least three rotating repositories")
    return {"id": language_id, "stable": stable, "rotating": rotating}


def _validate_unique_manifest_entries(languages: list[dict[str, Any]]) -> None:
    language_ids = [language["id"] for language in languages]
    if len(set(language_ids)) != len(language_ids):
        raise ValueError("Benchmark manifest contains a duplicate language id")
    repositories = [
        repository.lower()
        for language in languages
        for repository in [*language["stable"], *language["rotating"]]
    ]
    if len(set(repositories)) != len(repositories):
        raise ValueError("Benchmark manifest contains a duplicate repository")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read benchmark manifest: {exc}") from exc
    root = _object(raw, "manifest")
    if set(root) != {"schema_version", "profiles", "languages"}:
        raise ValueError("Benchmark manifest has unexpected fields")
    if root.get("schema_version") != 1:
        raise ValueError("Unsupported benchmark manifest schema")
    profiles = _manifest_profiles(root)
    languages = [
        _manifest_language(raw_language, index)
        for index, raw_language in enumerate(_list(root.get("languages"), "languages"))
    ]
    _validate_unique_manifest_entries(languages)
    if len(languages) != 12:
        raise ValueError("Benchmark manifest must define exactly 12 language families")
    return {"schema_version": 1, "profiles": profiles, "languages": languages}


def _rotating_index(seed: str, language: str, count: int) -> int:
    digest = hashlib.sha256(f"{seed}:{language}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


def _cell_id(language: str, repository: str, profile: str) -> str:
    slug = repository.lower().replace("/", "-").replace("_", "-").replace(".", "-")
    value = re.sub(r"-+", "-", f"{language}-{slug}-{profile}").strip("-")
    if len(value) > 120:
        suffix = hashlib.sha256(value.encode()).hexdigest()[:12]
        value = f"{value[:107]}-{suffix}"
    return value


def _resolve_repository(
    repository: str, resolver: Resolver, resolved: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    if repository not in resolved:
        default_branch, sha = resolver(repository)
        if not default_branch:
            raise ValueError(f"Empty default branch for {repository}")
        normalized_sha = sha.lower()
        if not SHA_PATTERN.fullmatch(normalized_sha):
            raise ValueError(f"Invalid resolved commit for {repository}: {sha!r}")
        resolved[repository] = (default_branch, normalized_sha)
    return resolved[repository]


def _selected_repositories(
    language: dict[str, Any], seed: str, resolver: Resolver, resolved: dict[str, tuple[str, str]]
) -> list[dict[str, str]]:
    rotating = language["rotating"]
    chosen = rotating[_rotating_index(seed, language["id"], len(rotating))]
    repositories = [(repository, "stable") for repository in language["stable"]]
    repositories.append((chosen, "rotating"))
    selected = []
    for repository, role in repositories:
        default_branch, sha = _resolve_repository(repository, resolver, resolved)
        selected.append(
            {
                "repository": repository,
                "role": role,
                "default_branch": default_branch,
                "sha": sha,
            }
        )
    return selected


def _repository_cells(
    language: str,
    repository: dict[str, str],
    profiles: list[str],
    run_identity: dict[str, str],
) -> list[dict[str, str]]:
    return [
        {
            "cell_id": _cell_id(language, repository["repository"], profile),
            "language": language,
            **repository,
            "profile": profile,
            **run_identity,
        }
        for profile in profiles
    ]


def _run_identity(identity: Mapping[str, object]) -> dict[str, str]:
    if set(identity) != set(RUN_IDENTITY_FIELDS):
        raise ValueError("Benchmark run identity has unexpected fields")
    normalized = {key: _string(identity.get(key), key) for key in RUN_IDENTITY_FIELDS}
    normalized["action_sha"] = normalized["action_sha"].lower()
    if not SHA_PATTERN.fullmatch(normalized["action_sha"]):
        raise ValueError("Invalid benchmark action commit")
    if not RUN_NUMBER_PATTERN.fullmatch(normalized["run_id"]):
        raise ValueError("Invalid benchmark run id")
    if not RUN_NUMBER_PATTERN.fullmatch(normalized["run_attempt"]):
        raise ValueError("Invalid benchmark run attempt")
    if not RUNNER_VALUE_PATTERN.fullmatch(normalized["runner_os"]):
        raise ValueError("Invalid benchmark runner OS")
    if not RUNNER_VALUE_PATTERN.fullmatch(normalized["runner_arch"]):
        raise ValueError("Invalid benchmark runner architecture")
    return normalized


def build_selection(
    manifest: dict[str, Any], seed: str, run_identity: dict[str, str], resolver: Resolver
) -> dict[str, Any]:
    if not seed:
        raise ValueError("Benchmark seed must not be empty")
    identity = _run_identity(run_identity)
    matrix: list[dict[str, str]] = []
    selected_repositories: dict[str, list[dict[str, str]]] = {}
    resolved: dict[str, tuple[str, str]] = {}
    for language in manifest["languages"]:
        selected = _selected_repositories(language, seed, resolver, resolved)
        selected_repositories[language["id"]] = selected
        for repository in selected:
            matrix.extend(
                _repository_cells(language["id"], repository, manifest["profiles"], identity)
            )
    if len(matrix) > 256:
        raise ValueError(f"GitHub Actions matrix limit exceeded: {len(matrix)} > 256")
    if len({cell["cell_id"] for cell in matrix}) != len(matrix):
        raise ValueError("Benchmark cell identifiers are not unique")
    return {
        "schema_version": 1,
        "seed": seed,
        **identity,
        "profiles": list(manifest["profiles"]),
        "expected_cells": len(matrix),
        "selected_repositories": selected_repositories,
        "matrix": {"include": matrix},
    }


def _github_json(url: str, token: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-code-quality-profile-benchmark",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw: object = json.load(response)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"GitHub API request failed for {url}: {exc}") from exc
    return _object(raw, url)


def github_resolver(token: str) -> Resolver:
    def resolve(repository: str) -> tuple[str, str]:
        encoded = "/".join(urllib.parse.quote(part, safe="") for part in repository.split("/"))
        metadata = _github_json(f"https://api.github.com/repos/{encoded}", token)
        branch = _string(metadata.get("default_branch"), f"{repository}.default_branch")
        encoded_branch = urllib.parse.quote(branch, safe="")
        commit = _github_json(
            f"https://api.github.com/repos/{encoded}/commits/{encoded_branch}", token
        )
        sha = _string(commit.get("sha"), f"{repository}.sha")
        return branch, sha

    return resolve


def nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Percentile requires at least one value")
    if not 0 < percentile <= 1:
        raise ValueError("Percentile must be in (0, 1]")
    if not all(math.isfinite(value) and value >= 0 for value in ordered):
        raise ValueError("Durations must be finite non-negative numbers")
    rank = math.ceil(percentile * len(ordered))
    return ordered[rank - 1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _duration_message(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.2f} s"
    if seconds < 100:
        return f"{seconds:.1f} s"
    return f"{seconds:.0f} s"


def _write_shield(path: Path, label: str, seconds: float) -> None:
    _write_json(
        path,
        {
            "schemaVersion": 1,
            "label": label,
            "message": _duration_message(seconds),
            "color": "blueviolet",
        },
    )


def _load_result(path: Path) -> dict[str, Any]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read benchmark result {path}: {exc}") from exc
    result = _object(raw, str(path))
    if result.get("schema_version") != 1:
        raise ValueError(f"Unsupported result schema in {path}")
    return result


def _expected_results(selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cells = _list(
        _object(selection.get("matrix"), "selection.matrix").get("include"), "matrix.include"
    )
    expected_count = selection.get("expected_cells")
    if expected_count != len(cells):
        raise ValueError("Selection expected_cells does not match its matrix")
    run_identity = _run_identity({field: selection.get(field) for field in RUN_IDENTITY_FIELDS})
    expected: dict[str, dict[str, Any]] = {}
    for index, raw_cell in enumerate(cells):
        cell = _object(raw_cell, f"matrix.include[{index}]")
        cell_id = _string(cell.get("cell_id"), f"matrix.include[{index}].cell_id")
        if cell_id in expected:
            raise ValueError(f"Duplicate expected benchmark cell: {cell_id}")
        for field, value in run_identity.items():
            if cell.get(field) != value:
                raise ValueError(f"Selection run identity mismatch for {cell_id}.{field}")
        expected[cell_id] = cell
    return expected


def _validate_result_fields(result: dict[str, Any], cell_id: str) -> None:
    expected_fields = {
        "schema_version",
        *IDENTITY_FIELDS,
        "status",
        "comparable",
        "duration_seconds",
        "total_tokens",
        "report_sha256",
    }
    if set(result) != expected_fields:
        raise ValueError(f"Unexpected benchmark result fields for {cell_id}")


def _validate_token_count(result: dict[str, Any], comparable: bool, cell_id: str) -> None:
    total_tokens = result.get("total_tokens")
    if isinstance(total_tokens, bool) or not isinstance(total_tokens, int) or total_tokens < 0:
        raise ValueError(f"Invalid token count for {cell_id}")
    if result.get("profile") == "none":
        if total_tokens != 0:
            raise ValueError(f"Unexpected jscpd token count for {cell_id}")
    elif comparable and total_tokens == 0:
        raise ValueError(f"Missing jscpd token count for {cell_id}")


def _validate_report_evidence(result: dict[str, Any], result_path: Path, cell_id: str) -> None:
    digest = result.get("report_sha256")
    if result["status"] not in COMPARABLE_STATUSES:
        if digest is not None:
            raise ValueError(f"Infrastructure result carries report evidence for {cell_id}")
        return
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"Invalid report digest for {cell_id}")
    report_path = result_path.with_name("report.json")
    try:
        report_bytes = report_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Missing report evidence for {cell_id}") from exc
    if hashlib.sha256(report_bytes).hexdigest() != digest:
        raise ValueError(f"Report digest mismatch for {cell_id}")
    report = _complete_report(str(report_path.resolve()), result["profile"])
    expected_verdict = "pass" if result["status"] == "pass" else "fail"
    if report is None or report["verdict"] != expected_verdict:
        raise ValueError(f"Invalid report evidence for {cell_id}")


def _validate_expected_identity(
    result: dict[str, Any], expected: dict[str, Any], cell_id: str
) -> None:
    for field in EXPECTED_IDENTITY_FIELDS:
        if result.get(field) != expected.get(field):
            raise ValueError(f"Benchmark result identity mismatch for {cell_id}.{field}")


def _validated_run_attempt(result: dict[str, Any], cell_id: str, current_attempt: int) -> str:
    run_attempt = result.get("run_attempt")
    if not isinstance(run_attempt, str) or not RUN_NUMBER_PATTERN.fullmatch(run_attempt):
        raise ValueError(f"Invalid run attempt for {cell_id}")
    if int(run_attempt) > current_attempt:
        raise ValueError(f"Future run attempt for {cell_id}")
    return run_attempt


def _validated_comparable(result: dict[str, Any], cell_id: str) -> bool:
    status = _string(result.get("status"), f"{cell_id}.status")
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown benchmark status for {cell_id}: {status}")
    comparable = result.get("comparable")
    if not isinstance(comparable, bool) or comparable != (status in COMPARABLE_STATUSES):
        raise ValueError(f"Invalid comparable flag for {cell_id}")
    return comparable


def _validated_duration(result: dict[str, Any], cell_id: str) -> float:
    duration = result.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError(f"Invalid duration for {cell_id}")
    duration_number = float(duration)
    if not math.isfinite(duration_number) or duration_number < 0:
        raise ValueError(f"Invalid duration for {cell_id}")
    return duration_number


def _validate_result(
    result: dict[str, Any],
    expected: dict[str, Any],
    cell_id: str,
    result_path: Path,
    current_attempt: int,
) -> dict[str, Any]:
    _validate_result_fields(result, cell_id)
    _validate_expected_identity(result, expected, cell_id)
    _validated_run_attempt(result, cell_id, current_attempt)
    comparable = _validated_comparable(result, cell_id)
    _validate_token_count(result, comparable, cell_id)
    result["duration_seconds"] = _validated_duration(result, cell_id)
    _validate_report_evidence(result, result_path, cell_id)
    return result


def _actual_results(
    expected: dict[str, dict[str, Any]], results_directory: Path, current_attempt: int
) -> dict[str, dict[str, Any]]:
    actual: dict[str, dict[str, Any]] = {}
    paths = sorted(results_directory.rglob("result.json"))
    if not paths:
        paths = sorted(
            path for path in results_directory.rglob("*.json") if path.name != "report.json"
        )
    seen_attempts: set[tuple[str, str]] = set()
    for path in paths:
        result = _load_result(path)
        cell_id = _string(result.get("cell_id"), f"{path}.cell_id")
        if cell_id not in expected:
            raise ValueError(f"Unexpected benchmark result: {cell_id}")
        validated = _validate_result(result, expected[cell_id], cell_id, path, current_attempt)
        attempt_key = (cell_id, validated["run_attempt"])
        if attempt_key in seen_attempts:
            raise ValueError(f"Duplicate benchmark result: {cell_id} attempt {attempt_key[1]}")
        seen_attempts.add(attempt_key)
        previous = actual.get(cell_id)
        if previous is None or int(validated["run_attempt"]) > int(previous["run_attempt"]):
            actual[cell_id] = validated
    missing = sorted(set(expected) - set(actual))
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Missing benchmark result for {len(missing)} cells: {preview}")
    return actual


def _profile_statistics(
    profiles: list[str], samples: list[dict[str, Any]]
) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for profile in profiles:
        durations = [
            result["duration_seconds"] for result in samples if result["profile"] == profile
        ]
        if not durations:
            raise ValueError(f"No comparable benchmark samples for profile {profile}")
        summary[profile] = {
            "samples": len(durations),
            "p50_seconds": nearest_rank(durations, 0.50),
            "p95_seconds": nearest_rank(durations, 0.95),
        }
    return summary


def _statistics(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50_seconds": nearest_rank(values, 0.50),
        "p95_seconds": nearest_rank(values, 0.95),
    }


def _normalized_statistics(
    profiles: list[str], samples: list[dict[str, Any]]
) -> tuple[dict[str, float | int], dict[str, dict[str, float | int]]]:
    normalized = [
        {
            "profile": result["profile"],
            "seconds": result["duration_seconds"] * 1_000_000 / result["total_tokens"],
        }
        for result in samples
        if result["total_tokens"] > 0
    ]
    if not normalized:
        raise ValueError("No jscpd token counts were produced")
    aggregate = _statistics([item["seconds"] for item in normalized])
    by_profile = {
        profile: _statistics([item["seconds"] for item in normalized if item["profile"] == profile])
        for profile in profiles
        if any(item["profile"] == profile for item in normalized)
    }
    return aggregate, by_profile


def _write_runtime_badges(
    badges_directory: Path,
    aggregate: dict[str, float | int],
    profiles: dict[str, dict[str, float | int]],
    normalized: dict[str, float | int],
    normalized_profiles: dict[str, dict[str, float | int]],
) -> None:
    badges_directory.mkdir(parents=True, exist_ok=True)
    for stale in badges_directory.glob("runtime-*.json"):
        stale.unlink()
    _write_shield(badges_directory / "runtime-p50.json", "runtime p50", aggregate["p50_seconds"])
    _write_shield(badges_directory / "runtime-p95.json", "runtime p95", aggregate["p95_seconds"])
    _write_shield(
        badges_directory / "runtime-per-million-tokens-p50.json",
        "runtime / 1M tokens p50",
        float(normalized["p50_seconds"]),
    )
    _write_shield(
        badges_directory / "runtime-per-million-tokens-p95.json",
        "runtime / 1M tokens p95",
        float(normalized["p95_seconds"]),
    )
    for profile, values in profiles.items():
        _write_shield(
            badges_directory / f"runtime-{profile}-p50.json",
            f"{profile} p50",
            float(values["p50_seconds"]),
        )
        _write_shield(
            badges_directory / f"runtime-{profile}-p95.json",
            f"{profile} p95",
            float(values["p95_seconds"]),
        )
    for profile, values in normalized_profiles.items():
        _write_shield(
            badges_directory / f"runtime-{profile}-per-million-tokens-p50.json",
            f"{profile} / 1M tokens p50",
            float(values["p50_seconds"]),
        )
        _write_shield(
            badges_directory / f"runtime-{profile}-per-million-tokens-p95.json",
            f"{profile} / 1M tokens p95",
            float(values["p95_seconds"]),
        )


def _infrastructure_failures(actual: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    failures = [
        {
            "cell_id": result["cell_id"],
            "repository": result["repository"],
            "profile": result["profile"],
            "status": result["status"],
        }
        for result in actual.values()
        if not result["comparable"]
    ]
    return sorted(failures, key=lambda item: item["cell_id"])


def aggregate_results(
    selection: dict[str, Any],
    results_directory: Path,
    badges_directory: Path,
    current_attempt: int | None = None,
) -> dict[str, Any]:
    if current_attempt is None:
        current_attempt = int(_string(selection.get("run_attempt"), "selection.run_attempt"))
    expected = _expected_results(selection)
    actual = _actual_results(expected, results_directory, current_attempt)
    infrastructure_failures = _infrastructure_failures(actual)
    if infrastructure_failures:
        raise ValueError(
            f"Infrastructure failed for {len(infrastructure_failures)} benchmark cells"
        )
    profiles = [
        _string(item, "selection.profiles[]")
        for item in _list(selection.get("profiles"), "selection.profiles")
    ]
    samples = [result for result in actual.values() if result["comparable"]]
    if not samples:
        raise ValueError("No comparable benchmark samples were produced")
    aggregate_durations = [result["duration_seconds"] for result in samples]
    aggregate = _statistics(aggregate_durations)
    profile_summary = _profile_statistics(profiles, samples)
    normalized, normalized_profiles = _normalized_statistics(profiles, samples)
    _write_runtime_badges(
        badges_directory,
        aggregate,
        profile_summary,
        normalized,
        normalized_profiles,
    )
    lineage = {
        "schema_version": 1,
        "run_id": _string(selection.get("run_id"), "selection.run_id"),
        "run_attempt": str(current_attempt),
        "action_sha": _string(selection.get("action_sha"), "selection.action_sha"),
    }
    _write_json(badges_directory / "benchmark-lineage.json", lineage)
    return {
        "schema_version": 1,
        "expected_cells": len(expected),
        "received_cells": len(actual),
        "comparable_samples": len(samples),
        "infrastructure_failures": infrastructure_failures,
        "aggregate": aggregate,
        "per_million_tokens": normalized,
        "profiles": profile_summary,
        "per_million_tokens_by_profile": normalized_profiles,
        "selected_repositories": selection.get("selected_repositories", {}),
        **{field: selection.get(field) for field in RUN_IDENTITY_FIELDS if field != "run_attempt"},
        "run_attempt": str(current_attempt),
        "seed": selection.get("seed"),
    }


def _command_matrix(arguments: argparse.Namespace) -> int:
    manifest = load_manifest(arguments.manifest)
    selection = build_selection(
        manifest,
        seed=arguments.seed,
        run_identity={
            "action_sha": arguments.action_sha,
            "run_id": arguments.run_id,
            "run_attempt": arguments.run_attempt,
            "runner_os": arguments.runner_os,
            "runner_arch": arguments.runner_arch,
        },
        resolver=github_resolver(arguments.github_token),
    )
    _write_json(arguments.selection, selection)
    compact_matrix = json.dumps(selection["matrix"], separators=(",", ":"))
    if arguments.github_output:
        with arguments.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={compact_matrix}\n")
            stream.write(f"expected_cells={selection['expected_cells']}\n")
    else:
        print(compact_matrix)
    return 0


def _report_object(path_value: str) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _matches_type(value: object, marker: str) -> bool:
    predicates = {
        "bool": lambda item: isinstance(item, bool),
        "integer": _is_integer,
        "list": lambda item: isinstance(item, list),
        "number": _is_number,
        "optional-integer": lambda item: item is None or _is_integer(item),
        "optional-number": lambda item: item is None or _is_number(item),
        "optional-string": lambda item: item is None or isinstance(item, str),
        "status": lambda item: item in {"pass", "fail", "skipped"},
        "string": lambda item: isinstance(item, str) and bool(item),
    }
    return predicates[marker](value)


def _valid_typed_object(value: object, schema: dict[str, str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(schema)
        and all(_matches_type(value.get(field), marker) for field, marker in schema.items())
    )


def _valid_check_shape(name: str, check: object) -> bool:
    return _valid_typed_object(check, CHECK_TYPES[name])


def _valid_tool_shape(name: str, tool: object) -> bool:
    return _valid_typed_object(tool, TOOL_TYPES[name])


def _valid_report_identity(report: dict[str, Any], profile: str) -> bool:
    enforcement = report.get("enforcement")
    return (
        set(report) == REPORT_FIELDS
        and report.get("schema_version") == 2
        and report.get("profile") == profile
        and report.get("verdict") in {"pass", "fail"}
        and report.get("quality_verdict") in {"pass", "fail"}
        and enforcement == {"mode": "absolute", "required_percent": None}
        and report.get("baseline") is None
    )


def _valid_checks_section(checks: object) -> bool:
    return (
        isinstance(checks, dict)
        and set(checks) == REPORT_CHECKS
        and all(_valid_check_shape(name, checks[name]) for name in REPORT_CHECKS)
    )


def _valid_tools_section(tools: object) -> bool:
    if not isinstance(tools, dict) or set(tools) != REPORT_TOOLS:
        return False
    excluded = tools.get("excluded_directories")
    return (
        isinstance(excluded, list)
        and all(isinstance(item, str) and item for item in excluded)
        and all(_valid_tool_shape(name, tools[name]) for name in TOOL_FIELDS)
    )


def _valid_report_sections(report: dict[str, Any]) -> bool:
    return all(
        (
            _valid_checks_section(report.get("checks")),
            _valid_tools_section(report.get("tools")),
        )
    )


def _expected_disabled_checks(profile: str) -> frozenset[str]:
    disabled = {
        "none": REPORT_CHECKS,
        "minimal": frozenset({"semgrep", "yamllint", "markdownlint"}),
    }
    return disabled.get(profile, frozenset())


def _valid_check_outcomes(report: dict[str, Any], profile: str) -> bool:
    checks = report["checks"]
    statuses = {name: check["status"] for name, check in checks.items()}
    blocking = {name for name, check in checks.items() if check["blocking"]}
    disabled = {name for name, status in statuses.items() if status == "skipped"}
    expected_disabled = _expected_disabled_checks(profile)
    return all((disabled == expected_disabled, blocking == REPORT_CHECKS - expected_disabled))


def _expected_check_status(failed: bool) -> str:
    return "fail" if failed else "pass"


def _safe_report_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    cleaned = path.as_posix().removeprefix("./")
    return all(
        (
            not path.is_absolute(),
            re.match(r"^[A-Za-z]:", value) is None,
            ".." not in path.parts,
            cleaned not in {"", "."},
            value == cleaned,
        )
    )


def _valid_fragment(fragment: object) -> bool:
    if not _valid_typed_object(fragment, FRAGMENT_TYPES):
        return False
    assert isinstance(fragment, dict)
    return all(
        (
            _safe_report_path(fragment["path"]),
            fragment["start_line"] >= 1,
            fragment["end_line"] >= fragment["start_line"],
        )
    )


def _valid_clone(clone: object) -> bool:
    if not isinstance(clone, dict) or set(clone) != CLONE_FIELDS:
        return False
    if not _valid_fragment(clone["first"]) or not _valid_fragment(clone["second"]):
        return False
    spans = [
        fragment["end_line"] - fragment["start_line"] + 1
        for fragment in (clone["first"], clone["second"])
    ]
    return all(
        (
            _is_integer(clone["lines"]) and clone["lines"] == spans[0],
            _is_integer(clone["tokens"]) and clone["tokens"] > 0,
            isinstance(clone["language"], str) and bool(clone["language"]),
        )
    )


def _valid_clone_family(family: object) -> bool:
    if not isinstance(family, dict) or set(family) != FAMILY_FIELDS:
        return False
    languages = family["languages"]
    fragments = family["fragments"]
    return all(
        (
            isinstance(family["id"], str)
            and re.fullmatch(r"duplication:[0-9a-f]{16}", family["id"]) is not None,
            family["check"] == "duplication",
            family["kind"] == "clone-family",
            _is_integer(family["lines"]) and family["lines"] > 0,
            isinstance(languages, list),
            bool(languages),
            languages == sorted(set(languages)),
            all(isinstance(language, str) and language for language in languages),
            isinstance(fragments, list),
            len(fragments) >= 1,
            all(_valid_fragment(fragment) for fragment in fragments),
        )
    )


def _fragment_key(fragment: dict[str, Any]) -> tuple[str, int, int]:
    return (fragment["path"], fragment["start_line"], fragment["end_line"])


def _clone_components(
    clones: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int, int], dict[str, Any]], list[set[tuple[str, int, int]]]]:
    fragments: dict[tuple[str, int, int], dict[str, Any]] = {}
    adjacent: dict[tuple[str, int, int], set[tuple[str, int, int]]] = {}
    for clone in clones:
        left = _fragment_key(clone["first"])
        right = _fragment_key(clone["second"])
        fragments[left] = clone["first"]
        fragments[right] = clone["second"]
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)
    unseen = set(fragments)
    components = []
    while unseen:
        component = set()
        pending = [unseen.pop()]
        while pending:
            key = pending.pop()
            if key in component:
                continue
            component.add(key)
            pending.extend(adjacent[key] - component)
        unseen.difference_update(component)
        components.append(component)
    return fragments, components


def _expected_clone_families(clones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fragments, components = _clone_components(clones)
    component_by_fragment = {
        key: index for index, component in enumerate(components) for key in component
    }
    members_by_component: list[list[dict[str, Any]]] = [[] for _ in components]
    for clone in clones:
        index = component_by_fragment[_fragment_key(clone["first"])]
        members_by_component[index].append(clone)

    families = []
    for component, members in zip(components, members_by_component, strict=True):
        keys = sorted(component)
        identity = json.dumps(keys, separators=(",", ":"), ensure_ascii=True)
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()[:16]
        families.append(
            {
                "id": f"duplication:{fingerprint}",
                "check": "duplication",
                "kind": "clone-family",
                "languages": sorted({clone["language"] for clone in members}),
                "lines": max(clone["lines"] for clone in members),
                "fragments": [fragments[key] for key in keys],
            }
        )
    return sorted(families, key=lambda family: (-family["lines"], family["id"]))


def _valid_duplication_evidence(check: dict[str, Any]) -> bool:
    clones = check["clones"]
    families = check["families"]
    if not all(_valid_clone(clone) for clone in clones):
        return False
    if not all(_valid_clone_family(family) for family in families):
        return False
    duplicated = check["duplicated_lines"]
    clone_lines = [clone["lines"] for clone in clones]
    return all(
        (
            bool(clones) == bool(duplicated),
            not clone_lines or max(clone_lines) <= duplicated <= sum(clone_lines),
            all(clone["tokens"] <= check["total_tokens"] for clone in clones),
            families == _expected_clone_families(clones),
        )
    )


def _valid_duplication_metrics(check: dict[str, Any], limit: float | None) -> bool:
    if limit is None:
        return all(
            (
                check["status"] == "skipped",
                check["observed_percent"] is None,
                check["allowed_percent"] is None,
                check["duplicated_lines"] == 0,
                check["total_lines"] == 0,
                check["total_tokens"] == 0,
                not check["families"],
                not check["clones"],
                _valid_duplication_evidence(check),
            )
        )
    observed = check["observed_percent"]
    duplicated = check["duplicated_lines"]
    total = check["total_lines"]
    expected = duplicated / total * 100 if total else 0.0
    failed = duplicated > 0 if limit <= 1e-9 else observed > limit + 1e-9
    return all(
        (
            check["allowed_percent"] == limit,
            0 <= duplicated <= total,
            0 <= observed <= 100,
            math.isclose(observed, expected, rel_tol=1e-12, abs_tol=0.005),
            check["status"] == _expected_check_status(failed),
            _valid_duplication_evidence(check),
        )
    )


def _valid_debt_metrics(
    check: dict[str, Any], limit: int | None, maximum_key: str, reports_observed: bool
) -> bool:
    if limit is None:
        expected_maximum = 0 if reports_observed else None
        return all(
            (
                check["status"] == "skipped",
                check[maximum_key] == expected_maximum,
                check["debt"] == 0,
                check["allowed_debt"] is None,
                not check["findings"],
            )
        )
    maximum = check[maximum_key]
    debt = check["debt"]
    maximum_matches = (maximum > limit) == (debt > 0) if reports_observed else maximum == limit
    return all(
        (
            check["allowed_debt"] == 0,
            check["status"] == _expected_check_status(debt > 0),
            maximum_matches,
            bool(check["findings"]) == (debt > 0),
        )
    )


def _valid_tool_finding(name: str, finding: object) -> bool:
    if not _valid_typed_object(finding, TOOL_FINDING_TYPES):
        return False
    assert isinstance(finding, dict)
    return all(
        (
            finding["tool"] == name,
            _safe_report_path(finding["path"]),
            finding["severity"] in TOOL_SEVERITIES[name],
            finding["location"] in {"path", "source"},
            name == "typos" or finding["location"] == "source",
            name == "typos" or not finding["suggestions"],
            finding["line"] >= 1,
            finding["column"] >= 1,
            finding["end_line"] >= 1,
            finding["end_column"] >= 1,
            (finding["end_line"], finding["end_column"]) >= (finding["line"], finding["column"]),
            all(isinstance(item, str) and item for item in finding["suggestions"]),
        )
    )


def _valid_finding_metrics(name: str, check: dict[str, Any], enabled: bool) -> bool:
    count = check["count"]
    immediate = check["immediate_count"]
    findings = check["findings"]
    findings_valid = all(_valid_tool_finding(name, finding) for finding in findings)
    if not findings_valid:
        return False
    if not enabled:
        return all(
            (
                check["status"] == "skipped",
                count == 0,
                immediate == 0,
                check["allowed_count"] is None,
                not findings,
                findings_valid,
            )
        )
    expected_immediate = (
        sum(finding["severity"] == "error" for finding in findings) if name == "semgrep" else 0
    )
    expected_count = len(findings) - expected_immediate
    finding_keys = [
        (finding["path"], finding["line"], finding["column"], finding["rule"])
        for finding in findings
    ]
    failed = expected_immediate > 0 or expected_count > 0
    return all(
        (
            check["allowed_count"] == 0,
            count == expected_count,
            immediate == expected_immediate,
            finding_keys == sorted(finding_keys),
            check["status"] == _expected_check_status(failed),
            findings_valid,
        )
    )


def _valid_function(function: object) -> bool:
    if not _valid_typed_object(function, FUNCTION_TYPES):
        return False
    assert isinstance(function, dict)
    return all(
        (
            _safe_report_path(function["path"]),
            function["start_line"] >= 1,
            function["end_line"] >= function["start_line"],
            function["ccn"] >= 1,
            function["length"] == function["end_line"] - function["start_line"] + 1,
        )
    )


def _function_sort_key(function: dict[str, Any], metric: str, limit: int) -> tuple:
    return (
        -(function[metric] - limit),
        function["path"],
        function["start_line"],
        function["symbol"],
    )


def _expected_metric_findings(
    functions: list[dict[str, Any]], metric: str, limit: int
) -> list[dict[str, Any]]:
    return sorted(
        (function for function in functions if function[metric] > limit),
        key=lambda function: _function_sort_key(function, metric, limit),
    )


def _expected_complexity_findings(
    functions: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    findings = []
    for function in _expected_metric_findings(functions, "ccn", limit):
        findings.append(
            {
                "id": (
                    f"complexity:{function['path']}:{function['symbol']}:{function['start_line']}"
                ),
                "check": "complexity",
                "kind": "complex-function",
                **function,
                "maximum": limit,
                "excess_debt": function["ccn"] - limit,
            }
        )
    return findings


def _metric_debt(functions: list[dict[str, Any]], metric: str, limit: int) -> int:
    return sum(max(0, function[metric] - limit) for function in functions)


def _valid_function_evidence(report: dict[str, Any], profile: str) -> bool:
    checks = report["checks"]
    functions = checks["complexity"]["functions"]
    if not all(_valid_function(function) for function in functions):
        return False
    if profile == "none":
        return not functions
    contract = PROFILE_CONTRACT[profile]
    ccn_limit = cast(int, contract["complexity"])
    length_limit = cast(int, contract["function_length"])
    argument_limit = cast(int, contract["arguments"])
    return all(
        (
            checks["complexity"]["maximum_observed_ccn"]
            == max((function["ccn"] for function in functions), default=0),
            checks["complexity"]["debt"] == _metric_debt(functions, "ccn", ccn_limit),
            checks["complexity"]["findings"] == _expected_complexity_findings(functions, ccn_limit),
            checks["function_length"]["debt"] == _metric_debt(functions, "length", length_limit),
            checks["function_length"]["findings"]
            == _expected_metric_findings(functions, "length", length_limit),
            checks["arguments"]["debt"]
            == _metric_debt(functions, "parameter_count", argument_limit),
            checks["arguments"]["findings"]
            == _expected_metric_findings(functions, "parameter_count", argument_limit),
        )
    )


def _valid_check_metrics(report: dict[str, Any], profile: str) -> bool:
    checks = report["checks"]
    contract = PROFILE_CONTRACT[profile]
    policy_enabled = contract["policy"] is not None
    duplication_limit = cast(float | None, contract["duplication"])
    complexity_limit = cast(int | None, contract["complexity"])
    function_limit = cast(int | None, contract["function_length"])
    argument_limit = cast(int | None, contract["arguments"])
    return all(
        (
            _valid_duplication_metrics(checks["duplication"], duplication_limit),
            checks["complexity"]["maximum_allowed_ccn"] == complexity_limit,
            _valid_debt_metrics(
                checks["complexity"], complexity_limit, "maximum_observed_ccn", True
            ),
            _valid_debt_metrics(checks["function_length"], function_limit, "maximum", False),
            _valid_debt_metrics(checks["arguments"], argument_limit, "maximum", False),
            _valid_finding_metrics("semgrep", checks["semgrep"], policy_enabled),
            _valid_finding_metrics("yamllint", checks["yamllint"], policy_enabled),
            _valid_finding_metrics("markdownlint", checks["markdownlint"], policy_enabled),
            _valid_finding_metrics("typos", checks["typos"], bool(contract["typos"])),
        )
    )


def _valid_report_verdicts(report: dict[str, Any]) -> bool:
    checks = report["checks"]
    failed = any(check["status"] == "fail" for check in checks.values())
    policy_failed = any(
        check["status"] == "fail" and check["blocking"] for check in checks.values()
    )
    return all(
        (
            report["quality_verdict"] == ("fail" if failed else "pass"),
            report["verdict"] == ("fail" if policy_failed else "pass"),
        )
    )


def _valid_profile_tools(report: dict[str, Any], profile: str) -> bool:
    tools = report["tools"]
    contract = PROFILE_CONTRACT[profile]
    expected_policy = contract["policy"]
    scanners = ("semgrep", "yamllint", "markdownlint")
    return (
        all(tools[name]["policy"] == expected_policy for name in scanners)
        and tools["typos"]["enabled"] == contract["typos"]
    )


def _valid_report_semantics(report: dict[str, Any], profile: str) -> bool:
    return all(
        (
            _valid_check_outcomes(report, profile),
            _valid_check_metrics(report, profile),
            _valid_function_evidence(report, profile),
            _valid_report_verdicts(report),
            _valid_profile_tools(report, profile),
        )
    )


def _complete_report(path_value: str, profile: str) -> dict[str, Any] | None:
    path = Path(path_value)
    if not path.is_absolute():
        return None
    report = _report_object(path_value)
    if report is None:
        return None
    if not _valid_report_identity(report, profile) or not _valid_report_sections(report):
        return None
    return report if _valid_report_semantics(report, profile) else None


def _report_tokens(report: dict[str, Any]) -> int | None:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return None
    duplication = checks.get("duplication")
    if not isinstance(duplication, dict):
        return None
    tokens = duplication.get("total_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        return None
    return tokens


def _classify_report(
    quality_outcome: str, action_result: str, report_path: str, profile: str
) -> tuple[str, int]:
    expected = {"success": "pass", "failure": "fail"}.get(quality_outcome)
    report = _complete_report(report_path, profile)
    if expected is None or action_result != expected or report is None:
        return "infrastructure-fail", 0
    total_tokens = _report_tokens(report)
    if total_tokens is None or (profile == "none") != (total_tokens == 0):
        return "infrastructure-fail", 0
    if report["verdict"] != expected:
        return "infrastructure-fail", 0
    status = "pass" if expected == "pass" else "quality-fail"
    return status, total_tokens


def _validate_record_inputs(arguments: argparse.Namespace) -> None:
    if not REPOSITORY_PATTERN.fullmatch(arguments.repository):
        raise ValueError("Invalid record repository")
    if not SHA_PATTERN.fullmatch(arguments.sha.lower()):
        raise ValueError("Invalid record commit SHA")
    if arguments.profile not in PROFILES:
        raise ValueError("Invalid record profile")


def _report_evidence(arguments: argparse.Namespace, status: str) -> tuple[bytes, str | None]:
    if status not in COMPARABLE_STATUSES:
        return b"", None
    report_path = Path(arguments.report_path)
    report_bytes = report_path.read_bytes() if report_path.is_file() else b""
    digest = hashlib.sha256(report_bytes).hexdigest() if report_bytes else None
    if status in COMPARABLE_STATUSES and not (
        isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest)
    ):
        raise ValueError("Comparable benchmark result lacks report evidence")
    return report_bytes, digest


def _record_identity(arguments: argparse.Namespace) -> dict[str, str]:
    return _run_identity(
        {
            "action_sha": arguments.action_sha,
            "run_id": arguments.run_id,
            "run_attempt": arguments.run_attempt,
            "runner_os": arguments.runner_os,
            "runner_arch": arguments.runner_arch,
        }
    )


def _benchmark_record(
    arguments: argparse.Namespace,
    status: str,
    total_tokens: int,
    report_sha256: str | None,
    duration: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "cell_id": arguments.cell_id,
        "language": arguments.language,
        "repository": arguments.repository,
        "role": arguments.role,
        "sha": arguments.sha.lower(),
        "profile": arguments.profile,
        "status": status,
        "comparable": status in COMPARABLE_STATUSES,
        "duration_seconds": round(duration, 6),
        "total_tokens": total_tokens,
        "report_sha256": report_sha256,
        **_record_identity(arguments),
    }


def _command_record(arguments: argparse.Namespace) -> int:
    ended_ns = time.monotonic_ns()
    if arguments.started_ns < 0 or ended_ns < arguments.started_ns:
        raise ValueError("Invalid benchmark start timestamp")
    duration = (ended_ns - arguments.started_ns) / 1_000_000_000
    status, total_tokens = _classify_report(
        arguments.quality_outcome,
        arguments.action_result,
        arguments.report_path,
        arguments.profile,
    )
    _validate_record_inputs(arguments)
    report_bytes, report_sha256 = _report_evidence(arguments, status)
    record = _benchmark_record(arguments, status, total_tokens, report_sha256, duration)
    _write_json(arguments.output, record)
    if report_sha256:
        arguments.evidence_report.parent.mkdir(parents=True, exist_ok=True)
        arguments.evidence_report.write_bytes(report_bytes)
    return 0


def _command_aggregate(arguments: argparse.Namespace) -> int:
    selection = _load_result(arguments.selection)
    summary = aggregate_results(
        selection, arguments.results, arguments.badges, arguments.run_attempt
    )
    _write_json(arguments.summary, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


def _lineage(path: Path, required: bool) -> tuple[int, int] | None:
    if not path.is_file():
        if required:
            raise ValueError(f"Missing benchmark lineage: {path}")
        return None
    value = _load_result(path)
    if set(value) != {"schema_version", "run_id", "run_attempt", "action_sha"}:
        raise ValueError(f"Invalid benchmark lineage fields: {path}")
    action_sha = _string(value.get("action_sha"), f"{path}.action_sha")
    if value.get("schema_version") != 1 or not SHA_PATTERN.fullmatch(action_sha):
        raise ValueError(f"Invalid benchmark lineage: {path}")
    run_id = _string(value.get("run_id"), f"{path}.run_id")
    run_attempt = _string(value.get("run_attempt"), f"{path}.run_attempt")
    if not RUN_NUMBER_PATTERN.fullmatch(run_id) or not RUN_NUMBER_PATTERN.fullmatch(run_attempt):
        raise ValueError(f"Invalid benchmark lineage numbers: {path}")
    return int(run_id), int(run_attempt)


def _command_guard_publication(arguments: argparse.Namespace) -> int:
    candidate = _lineage(arguments.candidate, required=True)
    published = _lineage(arguments.published, required=False)
    if published is not None and candidate is not None and candidate < published:
        raise ValueError(
            f"Stale benchmark lineage {candidate[0]}.{candidate[1]} < {published[0]}.{published[1]}"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and aggregate profile benchmarks")
    commands = parser.add_subparsers(dest="command", required=True)

    matrix = commands.add_parser("matrix")
    matrix.add_argument("--manifest", type=Path, required=True)
    matrix.add_argument("--seed", required=True)
    matrix.add_argument("--action-sha", required=True)
    matrix.add_argument("--run-id", required=True)
    matrix.add_argument("--run-attempt", required=True)
    matrix.add_argument("--runner-os", required=True)
    matrix.add_argument("--runner-arch", required=True)
    matrix.add_argument("--selection", type=Path, required=True)
    matrix.add_argument("--github-output", type=Path)
    matrix.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    matrix.set_defaults(handler=_command_matrix)

    record = commands.add_parser("record")
    record.add_argument("--cell-id", required=True)
    record.add_argument("--language", required=True)
    record.add_argument("--repository", required=True)
    record.add_argument("--role", choices=("stable", "rotating"), required=True)
    record.add_argument("--sha", required=True)
    record.add_argument("--profile", choices=PROFILES, required=True)
    record.add_argument("--quality-outcome", required=True)
    record.add_argument("--action-result", required=True)
    record.add_argument("--report-path", required=True)
    record.add_argument("--started-ns", type=int, required=True)
    record.add_argument("--runner-os", required=True)
    record.add_argument("--runner-arch", required=True)
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", required=True)
    record.add_argument("--action-sha", required=True)
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--evidence-report", type=Path, required=True)
    record.set_defaults(handler=_command_record)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--selection", type=Path, required=True)
    aggregate.add_argument("--results", type=Path, required=True)
    aggregate.add_argument("--badges", type=Path, required=True)
    aggregate.add_argument("--summary", type=Path, required=True)
    aggregate.add_argument("--run-attempt", type=int, required=True)
    aggregate.set_defaults(handler=_command_aggregate)

    guard = commands.add_parser("guard-publication")
    guard.add_argument("--candidate", type=Path, required=True)
    guard.add_argument("--published", type=Path, required=True)
    guard.set_defaults(handler=_command_guard_publication)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        return int(arguments.handler(arguments))
    except ValueError as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
