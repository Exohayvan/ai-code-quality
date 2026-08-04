from __future__ import annotations

import subprocess

import pytest

from scripts.cleanup_benchmark_artifacts import cleanup_artifacts


def test_cleanup_freezes_all_artifact_ids_before_deleting() -> None:
    calls: list[list[str]] = []
    artifact_ids = "\n".join(str(value) for value in range(1, 254)) + "\n"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, artifact_ids, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    deleted = cleanup_artifacts("owner/repository", "123", run=fake_run)

    assert deleted == 253
    assert calls[0] == [
        "gh",
        "api",
        "--paginate",
        "repos/owner/repository/actions/runs/123/artifacts?per_page=100",
        "--jq",
        ".artifacts[].id",
    ]
    assert len(calls) == 254
    assert calls[1] == [
        "gh",
        "api",
        "--method",
        "DELETE",
        "repos/owner/repository/actions/artifacts/1",
    ]
    assert calls[-1][-1].endswith("/253")


def test_cleanup_stops_before_deletion_when_listing_fails() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        cleanup_artifacts("owner/repository", "123", run=fake_run)

    assert len(calls) == 1


def test_cleanup_propagates_deletion_failure() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "11\n12\n", "")
        if command[-1].endswith("/12"):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(subprocess.CalledProcessError):
        cleanup_artifacts("owner/repository", "123", run=fake_run)

    assert calls[-1][-1].endswith("/12")


@pytest.mark.parametrize("artifact_output", ("01\n", "١\n", "1\n01\n"))
def test_cleanup_rejects_noncanonical_artifact_ids_before_deletion(
    artifact_output: str,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, artifact_output, "")

    with pytest.raises(ValueError, match="artifact ID"):
        cleanup_artifacts("owner/repository", "123", run=fake_run)

    assert len(calls) == 1


@pytest.mark.parametrize("run_id", ("01", "١", "0"))
def test_cleanup_rejects_noncanonical_run_ids(run_id: str) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(ValueError, match="workflow run ID"):
        cleanup_artifacts("owner/repository", run_id, run=fake_run)

    assert calls == []


@pytest.mark.parametrize(
    "artifact_output",
    (
        " 1\n",
        "1 \n",
        "\t1\t\n",
        "\n",
        " \n",
        "1\n 2\n",
        "1\n\n",
        "1\r\n",
        "1\v2\n",
        "1\f2\n",
        "\u00a01\u00a0\n",
    ),
)
def test_cleanup_rejects_whitespace_normalized_artifact_ids(
    artifact_output: str,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, artifact_output, "")

    with pytest.raises(ValueError, match="artifact ID"):
        cleanup_artifacts("owner/repository", "123", run=fake_run)

    assert len(calls) == 1


def test_cleanup_accepts_empty_artifact_listing() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    assert cleanup_artifacts("owner/repository", "123", run=fake_run) == 0
    assert len(calls) == 1
