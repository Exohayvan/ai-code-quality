from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Final

_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CANONICAL_ID: Final = re.compile(r"[1-9][0-9]*")
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _artifact_ids(output: str) -> tuple[str, ...]:
    if not output:
        artifact_ids: tuple[str, ...] = ()
    else:
        lines = output.split("\n")
        if lines[-1] == "":
            lines.pop()
        artifact_ids = tuple(lines)
    if any(_CANONICAL_ID.fullmatch(value) is None for value in artifact_ids):
        raise ValueError("GitHub returned an invalid artifact ID")
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("GitHub returned duplicate artifact IDs")
    return artifact_ids


def cleanup_artifacts(
    repository: str,
    run_id: str,
    *,
    run: Runner = subprocess.run,
) -> int:
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid GitHub repository")
    if _CANONICAL_ID.fullmatch(run_id) is None:
        raise ValueError("Invalid GitHub workflow run ID")

    listing = run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
            "--jq",
            ".artifacts[].id",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    artifact_ids = _artifact_ids(listing.stdout)
    for artifact_id in artifact_ids:
        run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{repository}/actions/artifacts/{artifact_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return len(artifact_ids)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete temporary artifacts from one workflow run")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    deleted = cleanup_artifacts(options.repository, options.run_id)
    print(f"Deleted {deleted} temporary benchmark artifacts")


if __name__ == "__main__":
    main()
