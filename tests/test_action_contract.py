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
        "function-length-debt",
        "argument-debt",
        "semgrep-findings",
        "yamllint-findings",
        "markdownlint-findings",
        "typo-findings",
        "report-path",
        "fix-context-path",
        "baseline-sha",
    }
    assert metadata["runs"]["using"] == "composite"
    assert any(step.get("id") == "quality" for step in metadata["runs"]["steps"])


def test_package_version_matches_v1_2_3_release() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert metadata["project"]["version"] == "1.2.3"


def test_action_outputs_forward_quality_step_outputs() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())

    for name, output in metadata["outputs"].items():
        assert output["value"] == f"${{{{ steps.quality.outputs.{name} }}}}"


def test_action_installs_tools_in_an_isolated_virtual_environment() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())
    install_step = next(
        step
        for step in metadata["runs"]["steps"]
        if step.get("name") == "Install pinned quality tools"
    )
    install_script = install_step["run"]

    assert "-m venv" in install_script
    assert "_base_executable" in install_script
    assert install_script.index("_base_executable") < install_script.index(
        'rm -rf "$quality_environment"'
    )
    assert "--user" not in install_script
    assert "RUNNER_TEMP" in install_script
    assert "semgrep==1.172.0" in install_script
    assert "yamllint==1.38.0" in install_script
    assert "markdownlint-cli@0.49.1" in install_script
    assert "ai_code_quality.install_typos" in install_script
    assert "process.versions.node" in install_script
    assert "Node.js 22 or newer is required" in install_script
    assert "GITHUB_PATH" in install_script


def test_action_provisions_its_own_node_22_runtime() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())
    steps = metadata["runs"]["steps"]
    setup_step = next(step for step in steps if step.get("name") == "Set up Node.js")
    install_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Install pinned quality tools"
    )

    assert setup_step["uses"] == ("actions/setup-node@820762786026740c76f36085b0efc47a31fe5020")
    assert setup_step["with"]["node-version"] == "22"
    assert steps.index(setup_step) < install_index
