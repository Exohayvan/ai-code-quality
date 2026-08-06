from __future__ import annotations

import json
from pathlib import Path

from ai_code_quality.checks import lint as lint_check
from ai_code_quality.checks.lint import (
    oxlint_categories,
    parse_oxlint_json,
    parse_ruff_json,
    ruff_selectors,
    run_lint,
    semgrep_lint_config,
)

POLICIES = ("minimal", "basic", "standard", "strict", "hardened", "maximum")


def test_lint_policy_ladders_are_monotonic() -> None:
    previous_ruff: set[str] = set()
    previous_oxlint: set[str] = set()
    previous_semgrep: set[str] = set()

    for policy in POLICIES:
        ruff = set(ruff_selectors(policy))
        oxlint = set(oxlint_categories(policy))
        semgrep = {rule["id"] for rule in semgrep_lint_config(policy, ("go", "java"))["rules"]}
        assert previous_ruff <= ruff
        assert previous_oxlint <= oxlint
        assert previous_semgrep <= semgrep
        previous_ruff = ruff
        previous_oxlint = oxlint
        previous_semgrep = semgrep


def test_parse_ruff_json_normalizes_a_finding(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("return missing\n")
    payload = json.dumps(
        [
            {
                "code": "F821",
                "filename": str(source),
                "location": {"row": 1, "column": 8},
                "end_location": {"row": 1, "column": 15},
                "message": "Undefined name `missing`",
                "severity": "error",
                "fix": None,
            }
        ]
    )

    findings = parse_ruff_json(payload, tmp_path)

    assert len(findings) == 1
    assert findings[0].tool == "ruff"
    assert findings[0].rule == "F821"
    assert findings[0].path == "src/app.py"
    assert findings[0].line == 1


def test_parse_oxlint_json_normalizes_a_finding(tmp_path: Path) -> None:
    source = tmp_path / "web" / "app.ts"
    source.parent.mkdir()
    source.write_text("if (true) {}\n")
    payload = json.dumps(
        {
            "diagnostics": [
                {
                    "message": "Unexpected constant condition",
                    "code": "eslint(no-constant-condition)",
                    "severity": "error",
                    "filename": str(source),
                    "labels": [
                        {
                            "label": "constant",
                            "span": {
                                "offset": 4,
                                "length": 4,
                                "line": 1,
                                "column": 5,
                            },
                        }
                    ],
                    "help": "Remove the constant condition",
                }
            ]
        }
    )

    findings = parse_oxlint_json(payload, tmp_path)

    assert len(findings) == 1
    assert findings[0].tool == "oxlint"
    assert findings[0].rule == "eslint(no-constant-condition)"
    assert findings[0].path == "web/app.ts"
    assert findings[0].suggestions == ("Remove the constant condition",)


def test_oxlint_parser_rejects_unknown_severity(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "diagnostics": [
                {
                    "code": "eslint(eqeqeq)",
                    "filename": str(tmp_path / "src" / "app.ts"),
                    "message": "Unexpected equality comparison",
                    "severity": "note",
                    "help": None,
                    "labels": [
                        {"span": {"line": 2, "column": 5, "length": 2}}
                    ],
                }
            ]
        }
    )

    try:
        parse_oxlint_json(payload, tmp_path)
    except ValueError as exc:
        assert str(exc) == "Invalid oxlint severity"
    else:
        raise AssertionError("unknown oxlint severity was accepted")


def test_run_lint_dispatches_all_detected_language_families(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        lint_check,
        "run_ruff",
        lambda repository, policy: calls.append(("ruff", policy)) or (),
    )
    monkeypatch.setattr(
        lint_check,
        "run_oxlint",
        lambda repository, policy: calls.append(("oxlint", policy)) or (),
    )
    monkeypatch.setattr(
        lint_check,
        "run_semgrep_lint",
        lambda repository, policy, languages: calls.append(
            ("semgrep-lint", languages)
        )
        or (),
    )

    findings = run_lint(
        tmp_path,
        "strict",
        ("go", "javascript", "java", "python", "typescript"),
    )

    assert findings == ()
    assert calls == [
        ("ruff", "strict"),
        ("oxlint", "strict"),
        ("semgrep-lint", ("go", "java")),
    ]


def test_native_lint_policies_ignore_repository_rule_overrides(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff.lint]\nignore = ["F821"]\n', encoding="utf-8"
    )
    (tmp_path / "bad.py").write_text("print(missing_name)\n", encoding="utf-8")
    (tmp_path / ".oxlintrc.json").write_text(
        '{"rules":{"no-debugger":"off"}}\n', encoding="utf-8"
    )
    (tmp_path / "bad.js").write_text("debugger;\n", encoding="utf-8")

    assert [finding.rule for finding in lint_check.run_ruff(tmp_path, "minimal")] == [
        "F821"
    ]
    assert [finding.rule for finding in lint_check.run_oxlint(tmp_path, "minimal")] == [
        "eslint(no-debugger)"
    ]


def test_semgrep_fallback_lints_all_supported_language_families(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "polyglot"
    source.mkdir(parents=True)
    examples = {
        "a.c": "void f() { gets(buffer); }\n",
        "a.cpp": "void f() { gets(buffer); }\n",
        "Example.cs": (
            "using System; class E { void F() { try { G(); } "
            "catch (Exception ignored) {} } void G() {} }\n"
        ),
        "a.go": (
            'package p\nimport "os"\nfunc f(){ '
            'data, _ := os.ReadFile("x"); _ = data }\n'
        ),
        "Example.java": (
            "class E { void f() { try { g(); } "
            "catch (RuntimeException ignored) {} } void g() {} }\n"
        ),
        "Example.kt": (
            "fun f() { try { g() } catch (ignored: RuntimeException) {} }\n"
            "fun g() {}\n"
        ),
        "a.php": "<?php eval($code);\n",
        "a.rb": "eval(code)\n",
        "a.rs": "fn f(v: Result<i32, ()>) { let _ = v.unwrap(); }\n",
        "a.swift": "func f() { let value = try! risky() }\n",
    }
    for name, content in examples.items():
        (source / name).write_text(content, encoding="utf-8")

    languages = (
        "c",
        "cpp",
        "csharp",
        "go",
        "java",
        "kotlin",
        "php",
        "ruby",
        "rust",
        "swift",
    )
    findings = lint_check.run_semgrep_lint(tmp_path, "minimal", languages)

    assert [(finding.rule, Path(finding.path).name) for finding in findings] == [
        ("ai-lint.csharp.empty-catch", "Example.cs"),
        ("ai-lint.java.empty-catch", "Example.java"),
        ("ai-lint.java.empty-catch", "Example.kt"),
        ("ai-lint.c.gets", "a.c"),
        ("ai-lint.c.gets", "a.cpp"),
        ("ai-lint.go.discarded-error", "a.go"),
        ("ai-lint.php.eval", "a.php"),
        ("ai-lint.ruby.eval", "a.rb"),
        ("ai-lint.rust.unwrap", "a.rs"),
        ("ai-lint.swift.force-try", "a.swift"),
    ]


def test_run_lint_is_a_noop_without_supported_source(tmp_path: Path) -> None:
    assert run_lint(tmp_path, "strict", ()) == ()
