"""
End-to-end tests for ``psimulate workflow``.

These tests run against a real SLURM cluster and exercise the full workflow
pipeline: YAML config parsing, Jobmon workflow construction, SLURM submission,
sequential step execution, and output file generation.

They are gated behind the ``@pytest.mark.cluster`` marker and require:
1. Execution on a SLURM cluster (automatically detected)
2. The ``--runslow`` pytest option
3. Running on the designated slow test day (default: Sunday)

A SLURM project can be specified with ``--slurm-project`` (defaults to
``proj_simscience``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest
import yaml

RESULTS_DIR = "/mnt/team/simulation_science/priv/engineering/tests/output/"

pytestmark = [pytest.mark.cluster, pytest.mark.slow, pytest.mark.weekly]


def _make_shared_tmp_dir() -> Path:
    """Create a temporary directory on the shared filesystem."""
    dir_str = tempfile.mkdtemp(dir=RESULTS_DIR)
    os.chmod(dir_str, os.stat(RESULTS_DIR).st_mode)
    return Path(dir_str)


def _cleanup_dir(path: Path) -> None:
    """Try up to 10 times to remove a directory tree."""
    for _ in range(10):
        if not path.exists():
            break
        time.sleep(1)
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def shared_tmp_path() -> Iterator[Path]:
    """Temporary directory on a shared filesystem visible to all cluster nodes."""
    results_dir = _make_shared_tmp_dir()
    yield results_dir
    _cleanup_dir(results_dir)


@pytest.fixture
def slurm_project(request: pytest.FixtureRequest) -> str:
    """SLURM project for cluster tests, from --slurm-project CLI option."""
    return str(request.config.getoption("--slurm-project"))


def _write_step_scripts(script_dir: Path, output_dir: Path) -> dict[str, Path]:
    """Create the helper scripts used by the workflow steps.

    Returns a dict mapping step name to script path.
    """
    script_dir.mkdir(exist_ok=True)

    # Step 1: shell script — writes a marker file
    init_script = script_dir / "init.sh"
    init_script.write_text(
        f"#!/bin/bash\n"
        f"set -euo pipefail\n"
        f'echo "step_1_complete" > {output_dir}/step_1_init.txt\n'
    )
    init_script.chmod(0o755)

    # Step 2: python script — verifies step 1's output then writes its own
    process_script = script_dir / "process.py"
    process_script.write_text(
        "from pathlib import Path\n"
        f'output = Path("{output_dir}")\n'
        'assert (output / "step_1_init.txt").exists(), "Step 1 output missing"\n'
        '(output / "step_2_process.txt").write_text("step_2_complete\\n")\n'
    )

    return {"init": init_script, "process": process_script}


def _write_workflow_config(
    config_dir: Path,
    output_dir: Path,
    scripts: dict[str, Path],
    project: str,
) -> Path:
    """Write a 3-step workflow YAML config.

    Steps (each a different type):
      1. ``init``    (shell)   – creates ``step_1_init.txt``
      2. ``process`` (python)  – verifies step 1, creates ``step_2_process.txt``
      3. ``report``  (command) – verifies step 2, creates ``step_3_report.txt``
    """
    config = {
        "workflow": {
            "name": "e2e_workflow_test",
            "project": project,
            "queue": "all.q",
            "output_directory": str(output_dir),
            "steps": [
                {
                    "name": "init",
                    "type": "shell",
                    "path": str(scripts["init"]),
                    "resources": {"memory": 1, "runtime": "00:05:00"},
                },
                {
                    "name": "process",
                    "type": "python",
                    "path": str(scripts["process"]),
                    "resources": {"memory": 1, "runtime": "00:05:00"},
                },
                {
                    "name": "report",
                    "command": (
                        f"bash -c '"
                        f"test -f {output_dir}/step_2_process.txt "
                        f"&& echo step_3_complete > {output_dir}/step_3_report.txt"
                        f"'"
                    ),
                    "resources": {"memory": 1, "runtime": "00:05:00"},
                },
            ],
        }
    }
    config_path = config_dir / "workflow_config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


def _run_psimulate(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a psimulate CLI command as a subprocess."""
    return subprocess.run(["psimulate", *args], capture_output=True, text=True)


class TestPsimulateWorkflow:
    """E2E tests for ``psimulate workflow``."""

    def test_three_step_workflow(self, shared_tmp_path: Path, slurm_project: str) -> None:
        """Run a 3-step workflow and verify all steps execute in order.

        The workflow has three steps, each using a different step type
        (shell, python, raw command).  Steps 2 and 3 assert that their
        upstream dependency's marker file exists before writing their own,
        confirming that Jobmon enforces the sequential execution order.
        """
        output_dir = shared_tmp_path / "workflow_output"
        output_dir.mkdir()
        script_dir = shared_tmp_path / "scripts"

        scripts = _write_step_scripts(script_dir, output_dir)
        config_path = _write_workflow_config(
            shared_tmp_path, output_dir, scripts, slurm_project
        )

        proc = _run_psimulate(
            [
                "workflow",
                "-c",
                str(config_path),
                "-P",
                slurm_project,
                "-o",
                str(output_dir),
            ]
        )
        assert proc.returncode == 0, (
            f"psimulate workflow failed.\n" f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

        # Verify all 3 step marker files were created
        expected_markers = {
            "step_1_init.txt": "step_1_complete",
            "step_2_process.txt": "step_2_complete",
            "step_3_report.txt": "step_3_complete",
        }
        for filename, expected_content in expected_markers.items():
            marker = output_dir / filename
            assert marker.exists(), f"Missing marker file: {marker}"
            assert (
                expected_content in marker.read_text()
            ), f"{filename} does not contain expected content '{expected_content}'"

        # Verify configuration.yaml was written to output directory
        config_output = output_dir / "configuration.yaml"
        assert config_output.exists(), "configuration.yaml not written"

        saved_config = yaml.safe_load(config_output.read_text())
        assert "workflow" in saved_config
        assert saved_config["workflow"]["name"] == "e2e_workflow_test"
        assert len(saved_config["workflow"]["steps"]) == 3
