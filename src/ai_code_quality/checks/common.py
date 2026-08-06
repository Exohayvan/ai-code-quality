from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

DEFAULT_EXCLUDED_DIRECTORIES: Final[tuple[str, ...]] = (
    ".git",
    ".venv",
    ".ai-code-quality",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    "target",
    "bin",
    "obj",
    ".generated",
)

COVERAGE_REPORT_EXACT_NAMES: Final[tuple[str, ...]] = (
    "coverage.json",
    "coverage-summary.json",
    "lcov.info",
    "coverage.lcov",
    "coverage.out",
    "cover.out",
    "coverage.xml",
)
COVERAGE_REPORT_XML_PREFIXES: Final[tuple[str, ...]] = ("cobertura", "jacoco")
COVERAGE_REPORT_PATTERNS: Final[tuple[str, ...]] = tuple(
    pattern
    for name in (
        *COVERAGE_REPORT_EXACT_NAMES,
        *(f"{prefix}*.xml" for prefix in COVERAGE_REPORT_XML_PREFIXES),
    )
    for pattern in (name, f"**/{name}")
)

_DRIVE_PATH = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class CommandOutput:
    stdout: str
    stderr: str
    returncode: int


def normalize_scanner_path(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or _DRIVE_PATH.match(normalized) or ".." in path.parts:
        raise ValueError(f"Scanner returned unsafe path: {raw!r}")
    cleaned = path.as_posix().removeprefix("./")
    if not cleaned or cleaned == ".":
        raise ValueError(f"Scanner returned unsafe path: {raw!r}")
    return cleaned


def resolve_command(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if os.name == "nt":
        resolved = shutil.which(f"{name}.cmd")
        if resolved:
            return resolved
    raise RuntimeError(f"Required command {name!r} was not found on PATH")


def run_command_capture(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 600,
    accepted_exit_codes: frozenset[int] = frozenset({0}),
) -> CommandOutput:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Scanner timed out after {timeout} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to start scanner: {exc}") from exc
    if completed.returncode not in accepted_exit_codes:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(
            f"Scanner exited with code {completed.returncode}: {detail or 'no diagnostic output'}"
        )
    return CommandOutput(completed.stdout, completed.stderr, completed.returncode)


def run_command(command: Sequence[str], *, cwd: Path, timeout: int = 600) -> str:
    return run_command_capture(command, cwd=cwd, timeout=timeout).stdout
