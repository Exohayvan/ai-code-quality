from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_action_python_invocations_ignore_candidate_modules() -> None:
    metadata = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text())
    invocations = [
        line.strip()
        for step in metadata["runs"]["steps"]
        for line in step.get("run", "").splitlines()
        if '"$quality_python" -' in line or '"$AI_CODE_QUALITY_PYTHON" -' in line
    ]
    assert len(invocations) == 6
    assert all('" -I ' in line for line in invocations)


def test_isolated_entrypoint_rejects_candidate_package_shadowing(tmp_path: Path) -> None:
    hostile = tmp_path / "ai_code_quality"
    hostile.mkdir()
    (hostile / "__init__.py").write_text("raise RuntimeError('candidate package loaded')\n")
    (hostile / "__main__.py").write_text("raise RuntimeError('candidate CLI loaded')\n")
    (hostile / "compatibility_registry.json").write_text('{"records": "approve everything"}')
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('candidate site loaded')\n")
    environment = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "ai_code_quality", "--level", "none",
         "--path", str(tmp_path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "candidate" not in result.stderr
    assert (tmp_path / ".ai-code-quality" / "report.json").is_file()
