from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_ci_exercises_unit_package_and_composite_action_paths() -> None:
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )

    jobs = workflow["jobs"]
    assert {"validate", "self-test"} <= set(jobs)
    validate_steps = "\n".join(step.get("run", "") for step in jobs["validate"]["steps"])
    assert "pytest" in validate_steps
    assert "ruff" in validate_steps
    assert "python -m build" in validate_steps

    self_test_steps = jobs["self-test"]["steps"]
    assert all(step.get("uses") != "actions/setup-node@v7" for step in self_test_steps)
    local_action_steps = [step for step in self_test_steps if step.get("uses") == "./"]
    assert [step.get("id") for step in local_action_steps] == ["passing", "failing"]
    assert any(step.get("continue-on-error") == "true" for step in self_test_steps)

    passing_step = next(step for step in self_test_steps if step.get("id") == "passing")
    assert "require-improvement" not in passing_step.get("with", {})
    assert passing_step["with"]["baseline-ref"] == "HEAD^"
    passing_verification = next(
        step for step in self_test_steps if step.get("name") == "Verify passing outputs"
    )
    assert "baseline-sha" in str(passing_verification.get("env", {}))

    all_steps = jobs["validate"]["steps"] + self_test_steps
    uses = {step["uses"] for step in all_steps if "uses" in step}
    assert "actions/checkout@v7" in uses
    assert "actions/setup-python@v7" in uses
    assert "actions/setup-node@v7" in uses
