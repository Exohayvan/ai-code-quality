from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import ai_code_quality.cli as cli
from ai_code_quality.models import DuplicationResult
from ai_code_quality.profiles import get_profile


def run_cli(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source = Path(__file__).parents[1] / "src"
    environment["PYTHONPATH"] = str(source)
    return subprocess.run(
        [sys.executable, "-m", "ai_code_quality", "--path", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_scan_repository_only_runs_tools_enabled_by_profile(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "run_jscpd", lambda path: DuplicationResult(0.0, 0, 0, ()))
    monkeypatch.setattr(cli, "run_lizard", lambda path: ())
    monkeypatch.setattr(cli, "run_typos", lambda path: calls.append("typos") or ())
    monkeypatch.setattr(cli, "run_semgrep", lambda path, policy: calls.append("semgrep") or ())
    monkeypatch.setattr(cli, "run_yamllint", lambda path, policy: calls.append("yamllint") or ())
    monkeypatch.setattr(
        cli, "run_markdownlint", lambda path, policy: calls.append("markdownlint") or ()
    )
    monkeypatch.setattr(cli, "detect_languages", lambda path: ("python", "typescript"))
    monkeypatch.setattr(
        cli,
        "run_lint",
        lambda path, policy, languages: calls.append("lint") or (),
    )
    monkeypatch.setattr(
        cli,
        "run_coverage",
        lambda path, languages, command=None: calls.append("coverage") or None,
    )

    result = cli.scan_repository(tmp_path, get_profile("minimal"))

    assert set(calls) == {"typos", "lint", "coverage"}
    assert result.semgrep == ()
    assert result.yamllint == ()
    assert result.markdownlint == ()
    assert result.lint == ()


def test_none_profile_runs_without_scanner_tools(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "--level", "none", "--require-improvement", "2")

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / ".ai-code-quality" / "report.json").read_text())
    assert report["verdict"] == "pass"
    assert report["checks"]["duplication"]["status"] == "skipped"
    assert result.stdout.count("Disabled") >= 4


def test_strict_failure_writes_bounded_ai_context(tmp_path: Path) -> None:
    (tmp_path / "complex.py").write_text(
        """def complex_value(a, b, c, d, e, f, g, h, i, j, k):
    if a: return 1
    if b: return 2
    if c: return 3
    if d: return 4
    if e: return 5
    if f: return 6
    if g: return 7
    if h: return 8
    if i: return 9
    if j: return 10
    if k: return 11
    return 0
"""
    )

    result = run_cli(tmp_path, "--level", "strict", "--require-improvement", "false")

    assert result.returncode == 1, result.stderr
    assert "AI Code Quality: FAILED" in result.stdout
    context = json.loads((tmp_path / ".ai-code-quality" / "fix-context.json").read_text())
    finding = next(item for item in context["repair_batch"] if item["check"] == "complexity")
    assert finding["path"] == "complex.py"
    assert finding["start_line"] == 1
    assert finding["ccn"] == 12
    assert finding["maximum"] == 10


def test_report_only_returns_success_with_quality_findings(tmp_path: Path) -> None:
    (tmp_path / "complex.py").write_text(
        "def bad(a, b, c, d, e, f):\n"
        "    return a if a else b if b else c if c else d if d else e if e else f\n"
    )

    result = run_cli(tmp_path, "--level", "maximum", "--require-improvement", "-1")

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / ".ai-code-quality" / "report.json").read_text())
    assert report["verdict"] == "pass"
    assert report["checks"]["complexity"]["status"] == "fail"
    assert report["checks"]["complexity"]["blocking"] is False


def test_zero_improvement_compares_against_explicit_baseline(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Test User")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / "logic.py").write_text("def decide(a):\n    return 1 if a else 0\n")
    git(repository, "add", "logic.py")
    git(repository, "commit", "-m", "baseline")
    baseline = git(repository, "rev-parse", "HEAD")
    (repository / "logic.py").write_text(
        """def decide(a, b, c, d, e, f, g, h, i, j, k):
    if a: return 1
    if b: return 2
    if c: return 3
    if d: return 4
    if e: return 5
    if f: return 6
    if g: return 7
    if h: return 8
    if i: return 9
    if j: return 10
    if k: return 11
    return 0
"""
    )
    git(repository, "commit", "-am", "regress")

    result = run_cli(
        repository,
        "--level",
        "strict",
        "--require-improvement",
        "0",
        "--baseline-ref",
        baseline,
    )

    assert result.returncode == 1, result.stderr
    report = json.loads((repository / ".ai-code-quality" / "report.json").read_text())
    assert report["baseline"]["complexity_debt"] == 0
    assert report["checks"]["complexity"]["debt"] == 2


def test_baseline_comparison_scans_the_same_subdirectory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    service = repository / "services" / "api"
    service.mkdir(parents=True)
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Test User")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / "outside.py").write_text(
        "def outside(a, b, c, d, e, f, g, h, i, j, k):\n"
        "    return a or b or c or d or e or f or g or h or i or j or k\n"
    )
    (service / "logic.py").write_text("def decide(a):\n    return 1 if a else 0\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline")
    baseline = git(repository, "rev-parse", "HEAD")
    (service / "logic.py").write_text(
        """def decide(a, b, c, d, e, f, g, h, i, j, k):
    if a: return 1
    if b: return 2
    if c: return 3
    if d: return 4
    if e: return 5
    if f: return 6
    if g: return 7
    if h: return 8
    if i: return 9
    if j: return 10
    if k: return 11
    return 0
"""
    )
    git(repository, "commit", "-am", "regress service")

    result = run_cli(
        service,
        "--level",
        "strict",
        "--require-improvement",
        "0",
        "--baseline-ref",
        baseline,
    )

    assert result.returncode == 1, result.stderr
    report = json.loads((service / ".ai-code-quality" / "report.json").read_text())
    assert report["baseline"]["complexity_debt"] == 0
    assert report["checks"]["complexity"]["debt"] == 2


def test_coverage_command_runs_in_current_and_baseline_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "coverage-repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Test User")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / "subject.py").write_text("COVERED = 10\nTOTAL = 100\n")
    (repository / "generate.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "values = {}\n"
        "exec(Path('subject.py').read_text(), values)\n"
        "summary = {\"covered_lines\": values['COVERED'], "
        "\"num_statements\": values['TOTAL']}\n"
        "payload = {\"files\": {\"subject.py\": {\"summary\": summary}}}\n"
        "Path('coverage.json').write_text(json.dumps(payload))\n"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline coverage")
    baseline = git(repository, "rev-parse", "HEAD")
    (repository / "subject.py").write_text("COVERED = 15\nTOTAL = 100\n")
    git(repository, "commit", "-am", "improve coverage")

    result = run_cli(
        repository,
        "--level",
        "minimal",
        "--require-improvement",
        "50",
        "--baseline-ref",
        baseline,
        "--coverage-command",
        f"{sys.executable} generate.py",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((repository / ".ai-code-quality" / "report.json").read_text())
    assert report["baseline"]["coverage_percent"] == 10.0
    assert report["checks"]["coverage"]["observed_percent"] == 15.0
    assert report["checks"]["coverage"]["required_percent"] == 15.0


def test_improvement_requires_a_reproducible_baseline_coverage_report(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.com")
    (repository / "subject.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline")
    (repository / "subject.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "current")
    (repository / "coverage.json").write_text(
        json.dumps(
            {
                "files": {
                    "subject.py": {
                        "summary": {"covered_lines": 1, "num_statements": 1}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        repository,
        "--level",
        "minimal",
        "--require-improvement",
        "50",
        "--baseline-ref",
        "HEAD^",
    )

    assert result.returncode == 2
    assert "set coverage-command" in result.stderr
