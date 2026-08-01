from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final

_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Unable to run git {' '.join(arguments[:2])}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise ValueError(
            f"Git command failed while resolving the comparison baseline: {detail}"
        )
    return completed.stdout.strip()


def _commit(repository: Path, ref: str) -> str:
    if not ref or "\x00" in ref:
        raise ValueError("Comparison baseline ref cannot be empty")
    resolved = _git(repository, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    if not _SHA_PATTERN.fullmatch(resolved):
        raise ValueError("Git returned an invalid comparison baseline commit")
    return resolved.lower()


def _event_payload(environment: Mapping[str, str]) -> dict[str, object]:
    event_path = environment.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        return {}
    try:
        payload: object = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read GitHub event payload: {exc}") from exc
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ValueError("GitHub event payload must be a JSON object")
    return payload


def resolve_baseline(
    repository: Path,
    *,
    explicit_ref: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    root = repository.resolve()
    if explicit_ref and explicit_ref.strip():
        return _commit(root, explicit_ref.strip())

    env = os.environ if environment is None else environment
    payload = _event_payload(env)
    event_name = env.get("GITHUB_EVENT_NAME", "")

    if event_name in {"pull_request", "pull_request_target"}:
        pull_request = payload.get("pull_request")
        if isinstance(pull_request, dict):
            base = pull_request.get("base")
            if isinstance(base, dict):
                base_sha = base.get("sha")
                if isinstance(base_sha, str) and _SHA_PATTERN.fullmatch(base_sha):
                    verified_base = _commit(root, base_sha)
                    merge_base = _git(root, "merge-base", "HEAD", verified_base)
                    if not _SHA_PATTERN.fullmatch(merge_base):
                        raise ValueError("Git returned an invalid pull-request merge base")
                    return merge_base.lower()

    if event_name == "push":
        before = payload.get("before")
        if (
            isinstance(before, str)
            and _SHA_PATTERN.fullmatch(before)
            and set(before) != {"0"}
        ):
            return _commit(root, before)

    raise ValueError(
        "Unable to determine a comparison baseline. Use actions/checkout with fetch-depth: 0 "
        "and provide baseline-ref for manual or initial-branch runs."
    )


def repository_root_and_relative(path: Path) -> tuple[Path, Path]:
    target = path.resolve()
    root = Path(_git(target, "rev-parse", "--show-toplevel")).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Analyzed path is outside its Git repository") from exc
    return root, relative


@contextmanager
def baseline_worktree(repository: Path, commit: str) -> Iterator[Path]:
    root = repository.resolve()
    verified_commit = _commit(root, commit)
    worktree = Path(tempfile.mkdtemp(prefix="ai-code-quality-baseline-"))
    added = False
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), verified_commit)
        added = True
        yield worktree
    finally:
        if added:
            try:
                _git(root, "worktree", "remove", "--force", str(worktree))
            finally:
                shutil.rmtree(worktree, ignore_errors=True)
                _git(root, "worktree", "prune")
        else:
            shutil.rmtree(worktree, ignore_errors=True)
