"""
End-to-end tests for ``dagger run`` and ``dagger restart``.

These tests run against a real SLURM cluster and exercise the full workflow
pipeline: YAML config parsing, Jobmon workflow construction, SLURM submission,
sequential step execution, output file generation, and (for restart) resuming
a previously failed workflow while skipping already-completed steps.

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

from tests.psimulate.test_e2e import (
    _BRANCHES,
    _EXPECTED_TOTAL_JOBS,
    _MODEL_SPEC,
    _assert_result_task_counts,
    _read_metadata,
)

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


def _write_simulation_step_inputs(script_dir: Path, locations: list[str]) -> dict[str, Path]:
    """Copy the shared model spec into per-location files and return their paths.

    The simulation step computes its output directory from
    ``Path(model_spec_path).stem``.  Using the location name as the
    filename keeps each location's outputs in its own subdirectory
    (e.g. ``<output_dir>/kenya/<timestamp>``).
    """
    script_dir.mkdir(exist_ok=True)
    spec_paths: dict[str, Path] = {}
    spec_content = _MODEL_SPEC.read_text()
    for location in locations:
        path = script_dir / f"{location}.yaml"
        path.write_text(spec_content)
        spec_paths[location] = path
    return spec_paths


def _write_workflow_config(
    config_dir: Path,
    output_dir: Path,
    scripts: dict[str, Path],
    project: str,
) -> Path:
    """Write a 3-step workflow YAML config.

    Steps (each a different type):
      1. ``init``    (command) – creates ``step_1_init.txt``
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
                    "command": str(scripts["init"]),
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                },
                {
                    "name": "process",
                    "type": "python",
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                    "args": {
                        "path": str(scripts["process"]),
                    },
                },
                {
                    "name": "report",
                    "command": (
                        f"bash -c '"
                        f"test -f {output_dir}/step_2_process.txt "
                        f"&& echo step_3_complete > {output_dir}/step_3_report.txt"
                        f"'"
                    ),
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                },
            ],
        }
    }
    config_path = config_dir / "workflow_config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


def _run_dagger(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a dagger CLI command as a subprocess."""
    return subprocess.run(["dagger", *args], capture_output=True, text=True)


class TestDaggerRun:
    """E2E tests for ``dagger run``."""

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

        proc = _run_dagger(
            [
                "run",
                "--config",
                str(config_path),
                "-P",
                slurm_project,
                "-o",
                str(output_dir),
            ]
        )
        assert proc.returncode == 0, (
            f"dagger run failed.\n" f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
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

    def test_sequential_simulation_steps(
        self, shared_tmp_path: Path, slurm_project: str
    ) -> None:
        """Run a workflow with two sequential simulation steps for different locations.

        This exercises the real-Jobmon simulation-step path (no mocks) and
        guards the fix from commit 3d229c2: two simulation steps in the
        same workflow must register distinct Jobmon ``TaskTemplate`` names
        (``psimulate_<step_name>``) rather than colliding on a shared
        ``"psimulate"`` template.

        Each location gets its own model spec file (named after the location)
        so that psimulate writes outputs to a location-specific directory
        (``<output_dir>/<location>/<timestamp>/``).
        """
        output_dir = shared_tmp_path / "workflow_output"
        output_dir.mkdir()
        script_dir = shared_tmp_path / "scripts"
        locations = ["kenya", "ethiopia"]
        spec_paths = _write_simulation_step_inputs(script_dir, locations)

        config = {
            "workflow": {
                "name": "e2e_simulation_workflow_test",
                "project": slurm_project,
                "queue": "all.q",
                "output_directory": str(output_dir),
                "steps": [
                    {
                        "name": location,
                        "type": "simulation",
                        "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                        "args": {
                            "model_specification": str(spec_paths[location]),
                            "branch_configuration": str(_BRANCHES),
                        },
                    }
                    for location in locations
                ],
            }
        }
        config_path = shared_tmp_path / "workflow_config.yaml"
        config_path.write_text(yaml.dump(config))

        proc = _run_dagger(
            [
                "run",
                "--config",
                str(config_path),
                "-P",
                slurm_project,
                "-o",
                str(output_dir),
            ]
        )
        assert (
            proc.returncode == 0
        ), f"dagger run failed.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        # Each location writes outputs under <output_dir>/<location>/<timestamp>/.
        # Verify both location directories exist under the output directory.
        location_dirs = {d.name for d in output_dir.iterdir() if d.is_dir()}
        assert set(locations) == location_dirs, (
            f"Expected location directories {set(locations)}, "
            f"found {location_dirs} in {output_dir}"
        )

        for location in locations:
            location_root = output_dir / location
            timestamp_dirs = [d for d in location_root.iterdir() if d.is_dir()]
            assert len(timestamp_dirs) == 1, (
                f"Expected exactly 1 timestamp directory in {location_root}, "
                f"found {len(timestamp_dirs)}: {timestamp_dirs}"
            )
            step_output_dir = timestamp_dirs[0]
            metadata = _read_metadata(step_output_dir)
            assert len(metadata) == _EXPECTED_TOTAL_JOBS, (
                f"Location '{location}': expected {_EXPECTED_TOTAL_JOBS} completed tasks, "
                f"got {len(metadata)}"
            )
            _assert_result_task_counts(step_output_dir / "results", _EXPECTED_TOTAL_JOBS)


def _write_flaky_workflow_config(output_dir: Path, config_path: Path, project: str) -> None:
    """Write a 3-step workflow whose middle step fails on its first attempt.

    Steps (sequential):
      1. ``init``   – appends a line to ``step_1_runs.txt`` each time it runs
         (so we can prove it is *not* re-run on restart).
      2. ``flaky``  – fails (exit 1) the first time it runs by dropping a
         sentinel file; on any later run the sentinel exists, so it writes
         ``step_2_process.txt`` and succeeds.
      3. ``report`` – verifies step 2's output, writes ``step_3_report.txt``.

    ``max_attempts: 1`` disables Jobmon retries, so the flaky step's first
    failure fails the whole workflow (rather than being retried into success).
    """
    init_cmd = f"bash -c 'echo ran >> {output_dir}/step_1_runs.txt'"
    flaky_cmd = (
        "bash -c '"
        f"S={output_dir}/.flaky_sentinel; "
        'if [ -f "$S" ]; then '
        f"echo recovered > {output_dir}/step_2_process.txt; "
        'else touch "$S"; echo failing-first-attempt >&2; exit 1; fi'
        "'"
    )
    report_cmd = (
        "bash -c '"
        f"test -f {output_dir}/step_2_process.txt "
        f"&& echo step_3_complete > {output_dir}/step_3_report.txt"
        "'"
    )
    config = {
        "workflow": {
            "name": "e2e_restart_test",
            "project": project,
            "queue": "all.q",
            "output_directory": str(output_dir),
            "max_attempts": 1,
            "steps": [
                {
                    "name": "init",
                    "command": init_cmd,
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                },
                {
                    "name": "flaky",
                    "command": flaky_cmd,
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                },
                {
                    "name": "report",
                    "command": report_cmd,
                    "resources": {"memory_gb": 1, "runtime": "00:05:00"},
                },
            ],
        }
    }
    config_path.write_text(yaml.dump(config))


class TestDaggerRestart:
    """E2E tests for ``dagger restart``."""

    def test_restart_resumes_failed_workflow(
        self, shared_tmp_path: Path, slurm_project: str
    ) -> None:
        """A workflow that fails partway is resumed to completion by restart.

        First ``dagger run`` fails at the ``flaky`` step (``max_attempts: 1``),
        leaving only ``init`` complete. ``dagger restart <results_dir>`` then
        resumes the same Jobmon workflow: ``init`` is skipped (proven by its
        run-count file staying at 1), ``flaky`` re-runs and succeeds, and
        ``report`` runs, completing the workflow.
        """
        output_dir = shared_tmp_path / "workflow_output"
        output_dir.mkdir()
        config_path = shared_tmp_path / "workflow_config.yaml"
        _write_flaky_workflow_config(output_dir, config_path, slurm_project)

        # First run: expected to FAIL at the flaky step (no retries).
        first = _run_dagger(
            ["run", "--config", str(config_path), "-P", slurm_project, "-o", str(output_dir)]
        )
        assert first.returncode != 0, (
            f"Expected the first run to fail at the flaky step.\n"
            f"STDOUT:\n{first.stdout}\nSTDERR:\n{first.stderr}"
        )
        assert (output_dir / "step_1_runs.txt").exists(), "init should have completed"
        assert not (
            output_dir / "step_2_process.txt"
        ).exists(), "flaky should not have succeeded"
        assert not (output_dir / "step_3_report.txt").exists(), "report should not have run"

        # Restart: resumes the workflow; init is skipped, flaky recovers, report runs.
        restart = _run_dagger(["restart", str(output_dir), "-P", slurm_project])
        assert restart.returncode == 0, (
            f"Expected restart to complete the workflow.\n"
            f"STDOUT:\n{restart.stdout}\nSTDERR:\n{restart.stderr}"
        )

        assert (
            output_dir / "step_2_process.txt"
        ).exists(), "flaky did not recover on restart"
        assert (
            "step_3_complete" in (output_dir / "step_3_report.txt").read_text()
        ), "report did not run after restart"
        # init was DONE before the failure, so resume must NOT re-run it.
        assert (output_dir / "step_1_runs.txt").read_text().count(
            "ran"
        ) == 1, "init was re-run on restart instead of being skipped"
