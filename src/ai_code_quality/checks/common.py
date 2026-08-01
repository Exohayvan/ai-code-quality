from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
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

_DRIVE_PATH = re.compile(r"^[A-Za-z]:")


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


def run_command(command: Sequence[str], *, cwd: Path, timeout: int = 600) -> str:
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
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(
            f"Scanner exited with code {completed.returncode}: {detail or 'no diagnostic output'}"
        )
    return completed.stdout
