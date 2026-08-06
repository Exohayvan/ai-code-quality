from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Final

from ai_code_quality.checks.common import DEFAULT_EXCLUDED_DIRECTORIES

_EXTENSION_LANGUAGES: Final[dict[str, str]] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
}

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = tuple(
    sorted(set(_EXTENSION_LANGUAGES.values()))
)


def language_for_path(path: str | Path) -> str | None:
    suffix = PurePosixPath(str(path).replace("\\", "/")).suffix.lower()
    return _EXTENSION_LANGUAGES.get(suffix)


def source_files(repository: Path) -> tuple[tuple[str, str], ...]:
    root = repository.resolve()
    found: list[tuple[str, str]] = []
    excluded = set(DEFAULT_EXCLUDED_DIRECTORIES)
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(
            name
            for name in names
            if name not in excluded and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                continue
            language = language_for_path(name)
            if language is None:
                continue
            relative = path.relative_to(root).as_posix()
            found.append((relative, language))
    return tuple(found)


def detect_languages(repository: Path) -> tuple[str, ...]:
    return tuple(sorted({language for _path, language in source_files(repository)}))
