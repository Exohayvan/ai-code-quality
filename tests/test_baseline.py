from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_code_quality.baseline import baseline_worktree, resolve_baseline


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def repository_with_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Test User")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / "value.txt").write_text("baseline\n")
    git(repository, "add", "value.txt")
    git(repository, "commit", "-m", "baseline")
    baseline = git(repository, "rev-parse", "HEAD")
    (repository / "value.txt").write_text("head\n")
    git(repository, "commit", "-am", "head")
    head = git(repository, "rev-parse", "HEAD")
    return repository, baseline, head


def test_explicit_baseline_ref_resolves_exact_commit(tmp_path: Path) -> None:
    repository, baseline, _head = repository_with_two_commits(tmp_path)

    assert resolve_baseline(repository, explicit_ref=baseline) == baseline


def test_pull_request_baseline_uses_merge_base(tmp_path: Path) -> None:
    repository, baseline, _head = repository_with_two_commits(tmp_path)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"base": {"sha": baseline}}}))

    resolved = resolve_baseline(
        repository,
        environment={"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event)},
    )

    assert resolved == baseline


def test_push_baseline_uses_before_sha(tmp_path: Path) -> None:
    repository, baseline, _head = repository_with_two_commits(tmp_path)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": baseline}))

    resolved = resolve_baseline(
        repository,
        environment={"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event)},
    )

    assert resolved == baseline


def test_missing_or_initial_push_baseline_fails_clearly(tmp_path: Path) -> None:
    repository, _baseline, _head = repository_with_two_commits(tmp_path)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": "0" * 40}))

    with pytest.raises(ValueError, match="baseline-ref"):
        resolve_baseline(
            repository,
            environment={"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event)},
        )


def test_baseline_worktree_checks_out_commit_and_cleans_up(tmp_path: Path) -> None:
    repository, baseline, _head = repository_with_two_commits(tmp_path)
    location: Path | None = None

    with baseline_worktree(repository, baseline) as worktree:
        location = worktree
        assert (worktree / "value.txt").read_text() == "baseline\n"
        assert worktree.exists()

    assert location is not None
    assert not location.exists()
    assert "ai-code-quality-baseline" not in git(repository, "worktree", "list")
