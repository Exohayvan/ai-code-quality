from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_action_metadata_exposes_confirmed_public_interface() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())

    assert metadata["name"] == "AI Code Quality"
    assert metadata["inputs"]["level"]["default"] == "standard"
    assert metadata["inputs"]["require-improvement"]["default"] == "2"
    assert metadata["inputs"]["path"]["default"] == "."
    assert set(metadata["inputs"]) == {
        "level",
        "require-improvement",
        "path",
        "baseline-ref",
        "repair-limit",
        "annotation-limit",
    }
    assert set(metadata["outputs"]) == {
        "result",
        "duplication-percent",
        "maximum-ccn",
        "complexity-debt",
        "report-path",
        "fix-context-path",
        "baseline-sha",
    }
    assert metadata["runs"]["using"] == "composite"
    assert any(step.get("id") == "quality" for step in metadata["runs"]["steps"])


def test_package_version_matches_v1_1_release() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert metadata["project"]["version"] == "1.1.0"


def test_action_outputs_forward_quality_step_outputs() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())

    for name, output in metadata["outputs"].items():
        assert output["value"] == f"${{{{ steps.quality.outputs.{name} }}}}"


def test_action_installs_tools_in_an_isolated_virtual_environment() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())
    install_script = metadata["runs"]["steps"][0]["run"]

    assert "-m venv" in install_script
    assert "--user" not in install_script
    assert "RUNNER_TEMP" in install_script
