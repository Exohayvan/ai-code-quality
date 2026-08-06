from __future__ import annotations

from pathlib import Path

from ai_code_quality.checks.languages import detect_languages, language_for_path


def test_detect_languages_supports_polyglot_repositories_and_excludes_generated_trees(
    tmp_path: Path,
) -> None:
    files = {
        "src/app.py": "python",
        "web/app.tsx": "typescript",
        "legacy/app.js": "javascript",
        "service/Main.java": "java",
        "native/main.c": "c",
        "native/engine.cpp": "cpp",
        "dotnet/App.cs": "csharp",
        "go/main.go": "go",
        "rust/src/lib.rs": "rust",
        "ruby/app.rb": "ruby",
        "php/index.php": "php",
        "swift/App.swift": "swift",
        "kotlin/App.kt": "kotlin",
    }
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source\n")
    generated = tmp_path / "nested" / "node_modules" / "ignored.ts"
    generated.parent.mkdir(parents=True)
    generated.write_text("ignored\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "ignored.py").write_text("ignored\n")

    detected = detect_languages(tmp_path)

    assert detected == tuple(sorted(set(files.values())))


def test_language_for_path_handles_headers_and_unknown_files() -> None:
    assert language_for_path("include/api.hpp") == "cpp"
    assert language_for_path("include/api.h") == "c"
    assert language_for_path("README.md") is None
