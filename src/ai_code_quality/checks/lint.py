from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from ai_code_quality.checks.common import (
    DEFAULT_EXCLUDED_DIRECTORIES,
    normalize_scanner_path,
    resolve_command,
    run_command_capture,
)
from ai_code_quality.checks.languages import source_files
from ai_code_quality.checks.semgrep import parse_semgrep_json
from ai_code_quality.models import ToolFinding

RUFF_VERSION: Final = "0.16.1"
OXLINT_VERSION: Final = "1.77.0"
_MAX_TARGET_ARGUMENT_BYTES: Final = 20_000
_POLICY_ORDER: Final[dict[str, int]] = {
    name: index
    for index, name in enumerate(
        ("minimal", "basic", "standard", "strict", "hardened", "maximum")
    )
}
_JAVASCRIPT_LANGUAGES: Final = frozenset({"javascript", "typescript"})
_NATIVE_LANGUAGES: Final = frozenset({"python", *_JAVASCRIPT_LANGUAGES})

_RUFF_TIERS: Final[dict[str, tuple[str, ...]]] = {
    "minimal": ("E9", "F63", "F7", "F82"),
    "basic": ("E4", "E7", "F"),
    "standard": ("I", "UP", "B", "SIM"),
    "strict": ("ARG", "C4", "PIE", "RET"),
    "hardened": ("S", "PERF", "RUF"),
    "maximum": ("ALL",),
}
_OXLINT_TIERS: Final[dict[str, tuple[str, ...]]] = {
    "minimal": ("correctness",),
    "basic": ("suspicious",),
    "standard": ("pedantic",),
    "strict": ("perf", "style"),
    "hardened": ("nursery",),
    "maximum": ("restriction",),
}

# Semgrep is the dependency-free fallback for languages whose native linters require
# a project build graph. Rules are action-owned and cumulative so baseline counts are
# stable across runs and stronger profiles never disable weaker findings.
_SEMGREP_LINT_RULES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "minimal",
        {
            "id": "ai-lint.c.gets",
            "languages": ["c", "cpp"],
            "message": "gets cannot enforce an input bound",
            "severity": "ERROR",
            "pattern": "gets(...) ",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.go.discarded-error",
            "languages": ["go"],
            "message": "Do not discard a returned error",
            "severity": "WARNING",
            "pattern": "$VALUE, _ := $CALL",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.java.empty-catch",
            "languages": ["java", "kotlin"],
            "message": "Do not silently swallow exceptions",
            "severity": "WARNING",
            "pattern": "try { ... } catch (...) {}",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.csharp.empty-catch",
            "languages": ["csharp"],
            "message": "Do not silently swallow exceptions",
            "severity": "WARNING",
            "pattern": "try { ... } catch ($TYPE $ERROR) {}",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.php.eval",
            "languages": ["php"],
            "message": "Avoid runtime evaluation of source text",
            "severity": "ERROR",
            "pattern": "eval(...) ",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.ruby.eval",
            "languages": ["ruby"],
            "message": "Avoid runtime evaluation of source text",
            "severity": "ERROR",
            "pattern": "eval(...) ",
        },
    ),
    (
        "basic",
        {
            "id": "ai-lint.java.debug-output",
            "languages": ["java"],
            "message": "Use the repository logger instead of debug console output",
            "severity": "WARNING",
            "pattern": "System.out.$METHOD(...) ",
        },
    ),
    (
        "basic",
        {
            "id": "ai-lint.csharp.debug-output",
            "languages": ["csharp"],
            "message": "Use the repository logger instead of debug console output",
            "severity": "WARNING",
            "pattern": "Console.WriteLine(...) ",
        },
    ),
    (
        "basic",
        {
            "id": "ai-lint.php.debug-output",
            "languages": ["php"],
            "message": "Remove debug output from production code",
            "severity": "WARNING",
            "pattern-either": [{"pattern": "var_dump(...)"}, {"pattern": "print_r(...)"}],
        },
    ),
    (
        "standard",
        {
            "id": "ai-lint.c.unbounded-copy",
            "languages": ["c", "cpp"],
            "message": "Use a bounded string copy operation",
            "severity": "WARNING",
            "pattern": "strcpy(...) ",
        },
    ),
    (
        "standard",
        {
            "id": "ai-lint.go.panic",
            "languages": ["go"],
            "message": "Return an error instead of panicking in library code",
            "severity": "WARNING",
            "pattern": "panic(...) ",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.rust.unwrap",
            "languages": ["rust"],
            "message": "Handle the error instead of unconditionally unwrapping",
            "severity": "WARNING",
            "pattern": "$VALUE.unwrap()",
        },
    ),
    (
        "strict",
        {
            "id": "ai-lint.java.print-stack-trace",
            "languages": ["java"],
            "message": "Report exceptions through structured logging",
            "severity": "WARNING",
            "pattern": "$ERROR.printStackTrace()",
        },
    ),
    (
        "strict",
        {
            "id": "ai-lint.ruby.debug-output",
            "languages": ["ruby"],
            "message": "Use the repository logger instead of debug console output",
            "severity": "WARNING",
            "pattern": "puts(...) ",
        },
    ),
    (
        "minimal",
        {
            "id": "ai-lint.swift.force-try",
            "languages": ["swift"],
            "message": "Handle thrown errors instead of using try!",
            "severity": "WARNING",
            "pattern": "try! $EXPRESSION",
        },
    ),
    (
        "hardened",
        {
            "id": "ai-lint.rust.unsafe-block",
            "languages": ["rust"],
            "message": "Document and isolate unsafe code",
            "severity": "WARNING",
            "pattern": "unsafe { ... }",
        },
    ),
    (
        "hardened",
        {
            "id": "ai-lint.php.error-suppression",
            "languages": ["php"],
            "message": "Do not suppress runtime errors",
            "severity": "WARNING",
            "pattern": "@$EXPRESSION",
        },
    ),
    (
        "maximum",
        {
            "id": "ai-lint.java.runtime-exec",
            "languages": ["java"],
            "message": "Avoid direct process execution in maximum policy code",
            "severity": "WARNING",
            "pattern": "Runtime.getRuntime().exec(...) ",
        },
    ),
    (
        "maximum",
        {
            "id": "ai-lint.go.unsafe-package",
            "languages": ["go"],
            "message": "Avoid the unsafe package in maximum policy code",
            "severity": "WARNING",
            "pattern": "unsafe.$MEMBER",
        },
    ),
)


def _policy_index(policy: str) -> int:
    try:
        return _POLICY_ORDER[policy]
    except KeyError as exc:
        raise ValueError(f"Unknown lint policy: {policy}") from exc


def _cumulative(policy: str, tiers: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    maximum = _policy_index(policy)
    values: list[str] = []
    for name in _POLICY_ORDER:
        if _POLICY_ORDER[name] <= maximum:
            values.extend(tiers[name])
    return tuple(values)


def ruff_selectors(policy: str) -> tuple[str, ...]:
    return _cumulative(policy, _RUFF_TIERS)


def oxlint_categories(policy: str) -> tuple[str, ...]:
    return _cumulative(policy, _OXLINT_TIERS)


def semgrep_lint_config(policy: str, languages: tuple[str, ...]) -> dict[str, Any]:
    maximum = _policy_index(policy)
    selected = set(languages)
    rules: list[dict[str, Any]] = []
    for minimum, configured in _SEMGREP_LINT_RULES:
        if _POLICY_ORDER[minimum] > maximum:
            continue
        rule = deepcopy(configured)
        applicable = [language for language in rule["languages"] if language in selected]
        if applicable:
            rule["languages"] = applicable
            rules.append(rule)
    return {"rules": rules}


def _relative_scanner_path(raw: str, repository: Path) -> str:
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            raw = candidate.resolve().relative_to(repository.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Lint scanner returned unsafe path: {raw!r}") from exc
    return normalize_scanner_path(raw)


def _positive_position(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"Invalid lint {label}")
    return value


def parse_ruff_json(payload: str, repository: Path) -> tuple[ToolFinding, ...]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Ruff JSON output") from exc
    if not isinstance(decoded, list):
        raise ValueError("Invalid Ruff JSON output")
    findings: list[ToolFinding] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise ValueError("Invalid Ruff finding")
        location = item.get("location")
        end = item.get("end_location")
        if not isinstance(location, dict) or not isinstance(end, dict):
            raise ValueError("Invalid Ruff location")
        code = item.get("code")
        filename = item.get("filename")
        message = item.get("message")
        if not all(isinstance(value, str) and value for value in (code, filename, message)):
            raise ValueError("Invalid Ruff finding text")
        findings.append(
            ToolFinding(
                tool="ruff",
                rule=code,
                path=_relative_scanner_path(filename, repository),
                line=_positive_position(location.get("row"), "line"),
                column=_positive_position(location.get("column"), "column"),
                end_line=_positive_position(end.get("row"), "end line"),
                end_column=_positive_position(end.get("column"), "end column"),
                message=message,
                severity="error" if item.get("severity") == "error" else "warning",
            )
        )
    return tuple(sorted(findings, key=lambda item: (item.path, item.line, item.column, item.rule)))


def parse_oxlint_json(payload: str, repository: Path) -> tuple[ToolFinding, ...]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid oxlint JSON output") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("diagnostics"), list):
        raise ValueError("Invalid oxlint JSON output")
    findings: list[ToolFinding] = []
    for item in decoded["diagnostics"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid oxlint finding")
        labels = item.get("labels")
        if not isinstance(labels, list) or not labels or not isinstance(labels[0], dict):
            raise ValueError("Invalid oxlint labels")
        span = labels[0].get("span")
        if not isinstance(span, dict):
            raise ValueError("Invalid oxlint span")
        code = item.get("code")
        filename = item.get("filename")
        message = item.get("message")
        if not all(isinstance(value, str) and value for value in (code, filename, message)):
            raise ValueError("Invalid oxlint finding text")
        line = _positive_position(span.get("line"), "line")
        column = _positive_position(span.get("column"), "column")
        length = span.get("length")
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise ValueError("Invalid oxlint span length")
        help_text = item.get("help")
        severity = item.get("severity")
        if severity not in {"error", "warning"}:
            raise ValueError("Invalid oxlint severity")
        suggestions = (help_text,) if isinstance(help_text, str) and help_text else ()
        findings.append(
            ToolFinding(
                tool="oxlint",
                rule=code,
                path=_relative_scanner_path(filename, repository),
                line=line,
                column=column,
                end_line=line,
                end_column=column + max(0, length - 1),
                message=message,
                severity=severity,
                suggestions=suggestions,
            )
        )
    return tuple(sorted(findings, key=lambda item: (item.path, item.line, item.column, item.rule)))


def run_ruff(repository: Path, policy: str) -> tuple[ToolFinding, ...]:
    command = [
        resolve_command("ruff"),
        "check",
        "--isolated",
        "--no-cache",
        "--output-format",
        "json",
        "--select",
        ",".join(ruff_selectors(policy)),
    ]
    for directory in DEFAULT_EXCLUDED_DIRECTORIES:
        command.extend(("--exclude", directory))
    command.append(".")
    output = run_command_capture(
        command, cwd=repository, accepted_exit_codes=frozenset({0, 1})
    )
    return parse_ruff_json(output.stdout, repository)


def run_oxlint(repository: Path, policy: str) -> tuple[ToolFinding, ...]:
    with tempfile.TemporaryDirectory(prefix="ai-quality-oxlint-") as temporary:
        config_path = Path(temporary) / "oxlint.json"
        config_path.write_text("{}\n", encoding="utf-8")
        command = [
            resolve_command("oxlint"),
            "--config",
            str(config_path),
            "--disable-nested-config",
            "--format",
            "json",
        ]
        for category in oxlint_categories(policy):
            command.extend(("--deny", category))
        for directory in DEFAULT_EXCLUDED_DIRECTORIES:
            command.extend(("--ignore-pattern", f"**/{directory}/**"))
        command.append(".")
        output = run_command_capture(
            command, cwd=repository, accepted_exit_codes=frozenset({0, 1})
        )
    return parse_oxlint_json(output.stdout, repository)


def run_semgrep_lint(
    repository: Path, policy: str, languages: tuple[str, ...]
) -> tuple[ToolFinding, ...]:
    config = semgrep_lint_config(policy, languages)
    if not config["rules"]:
        return ()
    selected = set(languages)
    targets = tuple(
        path for path, language in source_files(repository) if language in selected
    )
    if not targets:
        return ()

    batches: list[list[str]] = []
    batch: list[str] = []
    batch_size = 0
    for target in targets:
        target_size = len(target.encode("utf-8")) + 1
        if batch and batch_size + target_size > _MAX_TARGET_ARGUMENT_BYTES:
            batches.append(batch)
            batch = []
            batch_size = 0
        batch.append(target)
        batch_size += target_size
    if batch:
        batches.append(batch)

    findings: list[ToolFinding] = []
    with tempfile.TemporaryDirectory(prefix="ai-quality-lint-") as temporary:
        config_path = Path(temporary) / "semgrep-lint.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        command = [
            resolve_command("semgrep"),
            "scan",
            "--config",
            str(config_path),
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--jobs",
            "1",
            "--timeout",
            "0",
            "--max-target-bytes",
            "0",
        ]
        for targets_batch in batches:
            output = run_command_capture(
                [*command, *targets_batch],
                cwd=repository,
                timeout=900,
                accepted_exit_codes=frozenset({0, 1, 3}),
            )
            batch_findings = parse_semgrep_json(output.stdout)
            if output.returncode == 3 and not any(
                finding.rule == "semgrep.syntax-error"
                for finding in batch_findings
            ):
                raise RuntimeError("Semgrep lint reported unexplained exit code 3")
            findings.extend(batch_findings)

    normalized: list[ToolFinding] = []
    for finding in findings:
        marker = finding.rule.find("ai-lint.")
        rule = finding.rule[marker:] if marker >= 0 else finding.rule
        normalized.append(replace(finding, tool="semgrep-lint", rule=rule))
    return tuple(
        sorted(
            normalized,
            key=lambda item: (item.path, item.line, item.column, item.rule),
        )
    )


def run_lint(
    repository: Path, policy: str, languages: tuple[str, ...]
) -> tuple[ToolFinding, ...]:
    _policy_index(policy)
    if not languages:
        return ()
    findings: list[ToolFinding] = []
    detected = set(languages)
    if "python" in detected:
        findings.extend(run_ruff(repository, policy))
    if detected & _JAVASCRIPT_LANGUAGES:
        findings.extend(run_oxlint(repository, policy))
    fallback = tuple(sorted(detected - _NATIVE_LANGUAGES))
    if fallback:
        findings.extend(run_semgrep_lint(repository, policy, fallback))
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.path, item.line, item.column, item.tool, item.rule),
        )
    )
