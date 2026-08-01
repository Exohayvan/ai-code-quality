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
    assert any(step.get("uses") == "./" for step in self_test_steps)
    assert any(step.get("continue-on-error") == "true" for step in self_test_steps)
