from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "profile-benchmarks.yml"
README = ROOT / "README.md"


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step_named(steps, name: str):
    return next(step for step in steps if step.get("name") == name)


def test_readme_runtime_badges_use_main_normalized_p95_endpoints() -> None:
    readme = README.read_text(encoding="utf-8")
    for profile in ("minimal", "basic", "standard", "strict", "hardened", "maximum"):
        raw_endpoint = (
            "https://raw.githubusercontent.com/Exohayvan/ai-code-quality/main/.github/badges/"
            f"runtime-{profile}-per-million-tokens-p95.json"
        )
        shields_endpoint = f"https://img.shields.io/endpoint?url={quote(raw_endpoint, safe='')}"
        assert shields_endpoint in readme
    assert "runtime-none-per-million-tokens" not in readme


def test_benchmark_workflow_is_bounded_fan_out() -> None:
    workflow = _workflow()
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    jobs = workflow["jobs"]
    assert set(jobs) == {"prepare", "benchmark", "aggregate", "publish", "cleanup"}
    benchmark = jobs["benchmark"]
    assert benchmark["needs"] == "prepare"
    assert benchmark["strategy"]["fail-fast"] == "false"
    assert benchmark["strategy"]["matrix"] == "${{ fromJSON(needs.prepare.outputs.matrix) }}"
    assert int(benchmark["strategy"]["max-parallel"]) <= 256


def test_benchmark_cells_pin_inputs_and_always_upload_results() -> None:
    steps = _workflow()["jobs"]["benchmark"]["steps"]
    target_checkout = _step_named(steps, "Check out target repository")
    assert target_checkout["with"]["repository"] == "${{ matrix.repository }}"
    assert target_checkout["with"]["ref"] == "${{ matrix.sha }}"
    assert target_checkout["with"]["path"] == "target"
    action_checkout = _step_named(steps, "Check out benchmark action")
    assert action_checkout["with"]["ref"] == "${{ github.sha }}"
    assert action_checkout["with"]["path"] == "action"
    quality = next(step for step in steps if step.get("id") == "quality")
    assert quality["uses"] == "./action"
    assert quality["continue-on-error"] == "true"
    assert quality["with"]["require-improvement"] == "false"
    upload = _step_named(steps, "Upload benchmark result")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "${{ github.run_attempt }}" in upload["with"]["name"]


def test_benchmark_workflow_has_one_fail_closed_badge_writer() -> None:
    jobs = _workflow()["jobs"]
    aggregate = jobs["aggregate"]
    publish = jobs["publish"]
    assert aggregate["needs"] == ["prepare", "benchmark"]
    assert aggregate["if"] == "${{ always() }}"
    assert aggregate["permissions"]["contents"] == "read"
    assert publish["needs"] == "aggregate"
    assert publish["permissions"]["contents"] == "write"
    aggregate_script = "\n".join(
        step.get("run", "") for step in aggregate["steps"] if "run" in step
    )
    publish_script = "\n".join(step.get("run", "") for step in publish["steps"] if "run" in step)
    assert "benchmark_profiles.py aggregate" in aggregate_script
    assert "git add" not in aggregate_script
    assert "git add .github/badges" in publish_script
    assert "git add ." not in {line.strip() for line in publish_script.splitlines()}
    assert ".github/badges" in publish_script


def test_result_recording_uses_monotonic_time_and_validated_action_evidence() -> None:
    benchmark = _workflow()["jobs"]["benchmark"]
    script = "\n".join(step.get("run", "") for step in benchmark["steps"] if "run" in step)

    assert "time.monotonic_ns()" in script
    assert '--quality-outcome "$QUALITY_OUTCOME"' in script
    assert '--action-result "$ACTION_RESULT"' in script
    assert '--report-path "$REPORT_PATH"' in script
    assert "--evidence-report" in script
    assert 'REPORT_PATH="$GITHUB_WORKSPACE/target/.ai-code-quality/report.json"' in script
    assert 'ACTION_RESULT="fail"' in script


def test_artifacts_support_partial_reruns_without_collisions() -> None:
    jobs = _workflow()["jobs"]
    selection = _step_named(jobs["prepare"]["steps"], "Upload pinned benchmark selection")
    results = _step_named(jobs["aggregate"]["steps"], "Download all benchmark results")
    summary = _step_named(jobs["aggregate"]["steps"], "Upload aggregate evidence")

    assert selection["with"]["name"] == "benchmark-selection"
    assert selection["with"]["overwrite"] == "true"
    assert "${{ github.run_attempt }}" not in results["with"]["pattern"]
    assert "merge-multiple" not in results["with"]
    assert "${{ github.run_attempt }}" in summary["with"]["name"]
    assert selection["with"]["retention-days"] == "1"
    assert (
        _step_named(jobs["benchmark"]["steps"], "Upload benchmark result")["with"]["retention-days"]
        == "1"
    )
    assert summary["with"]["retention-days"] == "1"


def test_cleanup_deletes_artifacts_only_after_successful_publication() -> None:
    cleanup = _workflow()["jobs"]["cleanup"]

    assert cleanup["needs"] == "publish"
    assert cleanup["if"] == "${{ needs.publish.result == 'success' }}"
    assert cleanup["permissions"] == {"actions": "write", "contents": "read"}
    script = "\n".join(step.get("run", "") for step in cleanup["steps"] if "run" in step)
    assert "scripts/cleanup_benchmark_artifacts.py" in script
    assert '--repository "${{ github.repository }}"' in script
    assert '--run-id "${{ github.run_id }}"' in script


def test_publication_rechecks_lineage_after_each_remote_refresh() -> None:
    publish = _workflow()["jobs"]["publish"]
    script = "\n".join(step.get("run", "") for step in publish["steps"] if "run" in step)

    assert "git fetch origin main" in script
    assert "git reset --hard origin/main" in script
    assert "guard-publication" in script
    assert script.index("git reset --hard origin/main") < script.index("guard-publication")


def test_benchmark_workflow_documents_252_cell_cost() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "expected_cells" in text
    assert "252" in readme
    assert "nearest-rank" in readme
    assert "two fixed" in readme.lower()
    assert "rotating" in readme.lower()


def test_third_party_actions_are_pinned_to_exact_commits() -> None:
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses", "")
            if uses.startswith("actions/"):
                assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", uses)
