from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_code_quality.checks.markdownlint as markdownlint_check
import ai_code_quality.checks.semgrep as semgrep_check
from ai_code_quality.checks.common import CommandOutput
from ai_code_quality.checks.markdownlint import parse_markdownlint_json, run_markdownlint
from ai_code_quality.checks.semgrep import parse_semgrep_json, run_semgrep
from ai_code_quality.checks.typos import parse_typos_jsonl, run_typos
from ai_code_quality.checks.yamllint import parse_yamllint_output, run_yamllint
from ai_code_quality.policies import (
    markdownlint_config,
    semgrep_config,
    yamllint_config,
)


def test_parse_semgrep_json_normalizes_coordinates_and_severity() -> None:
    payload = json.dumps(
        {
            "version": "1.172.0",
            "results": [
                {
                    "check_id": "python-eval",
                    "path": "src/risky.py",
                    "start": {"line": 5, "col": 12},
                    "end": {"line": 5, "col": 19},
                    "extra": {"message": "Avoid eval", "severity": "ERROR"},
                }
            ],
            "errors": [],
        }
    )

    findings = parse_semgrep_json(payload)

    assert len(findings) == 1
    assert findings[0].tool == "semgrep"
    assert findings[0].rule == "python-eval"
    assert findings[0].path == "src/risky.py"
    assert findings[0].line == 5
    assert findings[0].column == 12
    assert findings[0].end_line == 5
    assert findings[0].end_column == 19
    assert findings[0].severity == "error"


def test_parse_semgrep_json_preserves_source_syntax_errors_as_blocking_findings() -> None:
    payload = json.dumps(
        {
            "version": "1.172.0",
            "results": [],
            "errors": [
                {
                    "code": 3,
                    "level": "warn",
                    "message": (
                        "Syntax error at line tui_gateway/server.py:5538:\n `,` was unexpected"
                    ),
                    "path": "tui_gateway/server.py",
                    "type": "Syntax error",
                }
            ],
        }
    )

    findings = parse_semgrep_json(payload)

    assert len(findings) == 1
    assert findings[0].tool == "semgrep"
    assert findings[0].rule == "semgrep.syntax-error"
    assert findings[0].path == "tui_gateway/server.py"
    assert findings[0].line == 5538
    assert findings[0].column == 1
    assert findings[0].end_line == 5538
    assert findings[0].end_column == 1
    assert findings[0].severity == "error"
    assert findings[0].message == (
        "Syntax error at line tui_gateway/server.py:5538:\n `,` was unexpected"
    )


def test_parse_semgrep_json_preserves_partial_parsing_spans_as_blocking_findings() -> None:
    location = {
        "path": "src/compiler/types.ts",
        "start": {"line": 6314, "col": 30, "offset": 0},
        "end": {"line": 6314, "col": 37, "offset": 7},
    }
    payload = json.dumps(
        {
            "version": "1.172.0",
            "results": [],
            "errors": [
                {
                    "code": 3,
                    "level": "warn",
                    "type": ["PartialParsing", [location]],
                    "message": (
                        "Syntax error at line src/compiler/types.ts:6314:\n"
                        " `symbol:` was unexpected"
                    ),
                    "path": "src/compiler/types.ts",
                    "spans": [
                        {
                            "file": location["path"],
                            "start": location["start"],
                            "end": location["end"],
                        }
                    ],
                }
            ],
        }
    )

    findings = parse_semgrep_json(payload)

    assert len(findings) == 1
    assert findings[0].rule == "semgrep.syntax-error"
    assert findings[0].path == "src/compiler/types.ts"
    assert findings[0].line == 6314
    assert findings[0].column == 30
    assert findings[0].end_line == 6314
    assert findings[0].end_column == 37
    assert findings[0].severity == "error"


@pytest.mark.parametrize(
    ("field", "malformed"),
    (("line", 6314.0), ("offset", False)),
)
def test_parse_semgrep_json_rejects_type_mismatched_partial_parsing_spans(
    field: str, malformed: object
) -> None:
    location = {
        "path": "src/compiler/types.ts",
        "start": {"line": 6314, "col": 30, "offset": 0},
        "end": {"line": 6314, "col": 37, "offset": 7},
    }
    span_start = dict(location["start"])
    span_start[field] = malformed
    payload = json.dumps(
        {
            "version": "1.172.0",
            "results": [],
            "errors": [
                {
                    "code": 3,
                    "level": "warn",
                    "type": ["PartialParsing", [location]],
                    "message": "Syntax error at line src/compiler/types.ts:6314:\n bad syntax",
                    "path": location["path"],
                    "spans": [
                        {
                            "file": location["path"],
                            "start": span_start,
                            "end": location["end"],
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="partial parsing"):
        parse_semgrep_json(payload)


def test_parse_semgrep_json_fails_closed_on_engine_errors() -> None:
    payload = json.dumps({"version": "1.172.0", "results": [], "errors": [{"message": "bad rule"}]})

    with pytest.raises(ValueError, match="Semgrep reported scanner errors"):
        parse_semgrep_json(payload)


def test_run_semgrep_accepts_strict_exit_for_source_syntax_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps(
        {
            "version": "1.172.0",
            "results": [],
            "errors": [
                {
                    "code": 3,
                    "level": "warn",
                    "message": "Syntax error at line broken.py:9:\n unexpected token",
                    "path": "broken.py",
                    "type": "Syntax error",
                }
            ],
        }
    )
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs) -> CommandOutput:
        observed["accepted_exit_codes"] = kwargs.get("accepted_exit_codes")
        return CommandOutput(payload, "", 3)

    monkeypatch.setattr(semgrep_check, "resolve_command", lambda name: name)
    monkeypatch.setattr(semgrep_check, "run_command_capture", fake_run)

    findings = run_semgrep(tmp_path, "standard")

    assert observed["accepted_exit_codes"] == frozenset({0, 3})
    assert len(findings) == 1
    assert findings[0].rule == "semgrep.syntax-error"
    assert findings[0].path == "broken.py"
    assert findings[0].line == 9


def test_run_semgrep_disables_internal_timeouts_and_oversubscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({"version": "1.172.0", "results": [], "errors": []})
    observed: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs) -> CommandOutput:
        observed["command"] = command
        return CommandOutput(payload, "", 0)

    monkeypatch.setattr(semgrep_check, "resolve_command", lambda name: name)
    monkeypatch.setattr(semgrep_check, "run_command_capture", fake_run)

    assert run_semgrep(tmp_path, "strict") == ()
    command = observed["command"]
    assert command[command.index("--jobs") + 1] == "1"
    assert command[command.index("--timeout") + 1] == "0"
    assert command[command.index("--timeout-threshold") + 1] == "0"


def test_run_semgrep_rejects_unexplained_strict_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({"version": "1.172.0", "results": [], "errors": []})

    monkeypatch.setattr(semgrep_check, "resolve_command", lambda name: name)
    monkeypatch.setattr(
        semgrep_check,
        "run_command_capture",
        lambda command, **kwargs: CommandOutput(payload, "", 3),
    )

    with pytest.raises(RuntimeError, match="unexplained exit code 3"):
        run_semgrep(tmp_path, "standard")


def test_parse_yamllint_output_normalizes_parsable_lines() -> None:
    findings = parse_yamllint_output(
        "./config.yml:2:9: [warning] truthy value should be one of [false, true] (truthy)\n"
    )

    assert len(findings) == 1
    assert findings[0].tool == "yamllint"
    assert findings[0].path == "config.yml"
    assert findings[0].line == 2
    assert findings[0].column == 9
    assert findings[0].rule == "truthy"
    assert findings[0].severity == "warning"


def test_parse_markdownlint_json_normalizes_cli_payload() -> None:
    payload = json.dumps(
        [
            {
                "fileName": "README.md",
                "lineNumber": 5,
                "ruleNames": ["MD040", "fenced-code-language"],
                "ruleDescription": "Fenced code blocks should have a language specified",
                "errorDetail": None,
                "errorContext": "```",
                "errorRange": [5, 3],
                "severity": "error",
            }
        ]
    )

    findings = parse_markdownlint_json(payload)

    assert len(findings) == 1
    assert findings[0].tool == "markdownlint"
    assert findings[0].rule == "MD040"
    assert findings[0].line == 5
    assert findings[0].column == 5
    assert findings[0].end_column == 7
    assert findings[0].severity == "error"


def test_markdownlint_no_target_help_is_an_empty_scan(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(markdownlint_check, "resolve_command", lambda name: name)
    monkeypatch.setattr(
        markdownlint_check,
        "run_command_capture",
        lambda *args, **kwargs: CommandOutput(
            "Usage: markdownlint [options] [files|directories|globs...]\n",
            "",
            0,
        ),
    )

    assert markdownlint_check.run_markdownlint(tmp_path, "basic") == ()


def test_parse_typos_jsonl_normalizes_corrections() -> None:
    findings = parse_typos_jsonl(
        '{"type":"typo","path":"./README.md","line_num":3,'
        '"byte_offset":10,"typo":"teh","corrections":["the"]}\n'
    )

    assert len(findings) == 1
    assert findings[0].tool == "typos"
    assert findings[0].rule == "typo"
    assert findings[0].path == "README.md"
    assert findings[0].line == 3
    assert findings[0].column == 11
    assert findings[0].suggestions == ("the",)


def test_parse_typos_jsonl_converts_utf8_byte_offsets_to_character_columns(
    tmp_path,
) -> None:
    (tmp_path / "README.md").write_text("café teh\n")

    findings = parse_typos_jsonl(
        '{"type":"typo","path":"./README.md","line_num":1,'
        '"byte_offset":6,"typo":"teh","corrections":["the"]}',
        repository=tmp_path,
    )

    assert findings[0].column == 6
    assert findings[0].end_column == 8


def test_parse_typos_jsonl_normalizes_filename_typos_without_line_numbers() -> None:
    findings = parse_typos_jsonl(
        '{"type":"typo","path":"./teh-file.txt","byte_offset":0,"typo":"teh","corrections":["the"]}'
    )

    assert findings[0].path == "teh-file.txt"
    assert findings[0].line == 1
    assert findings[0].column == 1
    assert findings[0].path_context is True


def test_parse_typos_jsonl_ignores_documented_informational_messages() -> None:
    payload = "\n".join(
        (
            '{"type":"binary_file","path":"./logo.png"}',
            '{"type":"file_type","path":"./README.md","file_type":"markdown"}',
            '{"type":"file","path":"./README.md"}',
            '{"type":"parse","path":"./README.md","line_num":1,"kind":"word","data":"hello"}',
        )
    )

    assert parse_typos_jsonl(payload) == ()


def test_parse_typos_jsonl_fails_closed_on_embedded_errors() -> None:
    with pytest.raises(ValueError, match="typos reported scanner error"):
        parse_typos_jsonl('{"type":"error","path":"./README.md","msg":"cannot read"}')


def test_external_linters_recursively_exclude_generated_directories(tmp_path) -> None:
    generated = tmp_path / "nested" / "node_modules" / "package"
    generated.mkdir(parents=True)
    (generated / "bad.yml").write_text("key:  yes\n")
    (generated / "bad.md").write_text("#bad\n")
    (generated / "bad.txt").write_text("teh\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")

    assert run_yamllint(tmp_path, "strict") == ()
    assert run_markdownlint(tmp_path, "strict") == ()
    assert run_typos(tmp_path) == ()


def test_semgrep_rules_only_expand_as_profiles_strengthen() -> None:
    previous: set[str] = set()
    for policy in ("basic", "standard", "strict", "hardened", "maximum"):
        rules = semgrep_config(policy)["rules"]
        current = {rule["id"] for rule in rules}
        assert previous < current
        previous = current


def test_yaml_and_markdown_column_limits_tighten_monotonically() -> None:
    expected = {
        "basic": None,
        "standard": None,
        "strict": 120,
        "hardened": 100,
        "maximum": 80,
    }
    for policy, maximum in expected.items():
        yaml_rule = yamllint_config(policy)["rules"]["line-length"]
        markdown_rule = markdownlint_config(policy).get("MD013")
        if maximum is None:
            assert yaml_rule == "disable" or yaml_rule["level"] == "warning"
            assert markdown_rule is False
        else:
            assert yaml_rule["max"] == maximum
            assert markdown_rule["line_length"] == maximum


def test_yamllint_does_not_treat_github_actions_on_key_as_truthy_value() -> None:
    for policy in ("basic", "standard", "strict", "hardened", "maximum"):
        assert yamllint_config(policy)["rules"]["truthy"]["check-keys"] is False
