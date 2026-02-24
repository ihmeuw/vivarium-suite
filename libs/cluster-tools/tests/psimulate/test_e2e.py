"""
End-to-end tests for psimulate run and restart.

These tests run against a real SLURM cluster and exercise the full psimulate
pipeline: job creation, SLURM submission, worker execution, result collection,
and output file generation.

They are gated behind the ``@pytest.mark.cluster`` marker and require:
1. Execution on a SLURM cluster (automatically detected)
2. The ``--runslow`` pytest option
3. Running on the designated slow test day (default: Sunday)

A SLURM project can be specified with ``--slurm-project`` (defaults to
``proj_simscience``).

These tests serve as a pre-refactor behavioral baseline: they capture the
current system's observable behavior so that we can verify the jobmon refactor
preserves it (red/green TDD).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pandas as pd
import pytest
import yaml

_DATA_DIR = Path(__file__).parent / "data"
_MODEL_SPEC = _DATA_DIR / "e2e_model_spec.yaml"
_BRANCHES = _DATA_DIR / "e2e_branches.yaml"

# Total jobs produced by the branch config (2 draws x 2 seeds x 1 branch)
_EXPECTED_TOTAL_JOBS = 4

# Generous timeout for SLURM scheduling + execution (10 minutes)
_TIMEOUT = 600

RESULTS_DIR = "/mnt/team/simulation_science/priv/engineering/tests/output/"

# Don't enforce weekly run requirement during development
pytestmark = [pytest.mark.cluster, pytest.mark.slow, pytest.mark.weekly]


def _make_shared_tmp_dir() -> Path:
    """Create a temporary directory on the shared filesystem."""
    dir_str = tempfile.mkdtemp(dir=RESULTS_DIR)
    os.chmod(dir_str, os.stat(RESULTS_DIR).st_mode)
    return Path(dir_str)


def _cleanup_dir(path: Path) -> None:
    """Try up to 10 times to remove a directory tree.

    NOTE: There are times where the directory is not removed even after
    several attempts with a rest between them.  Typically the dir is empty.
    """
    for _ in range(10):
        if not path.exists():
            break
        time.sleep(1)
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def shared_tmp_path() -> Iterator[Path]:
    """Temporary directory on a shared filesystem visible to all cluster nodes.

    pytest's ``tmp_path`` creates directories under ``/tmp`` which is
    node-local.  Workers scheduled on other nodes cannot access those
    paths, causing every job to fail immediately.  This fixture creates
    a temporary directory under the user's home directory (which lives
    on the shared ``/ihme`` filesystem) and cleans it up after the test.
    """
    results_dir = _make_shared_tmp_dir()
    yield results_dir
    _cleanup_dir(results_dir)


@pytest.fixture(scope="module")
def completed_sim_output(request: pytest.FixtureRequest) -> Iterator[Path]:
    """Run the basic simulation once for the entire test module.

    This avoids duplicating the expensive ``psimulate run`` for every test
    that needs a completed simulation as a prerequisite (restart, expand, etc.).
    The yielded path is the timestamped output directory and should be treated
    as **read-only** by consumers.
    """
    slurm_project = str(request.config.getoption("--slurm-project"))
    tmp_path = _make_shared_tmp_dir()
    _, output_dir = _run_basic_simulation(tmp_path, slurm_project)
    yield output_dir
    _cleanup_dir(tmp_path)


@pytest.fixture
def completed_sim_copy(completed_sim_output: Path) -> Iterator[Path]:
    """Provide an isolated deep-copy of a completed simulation run.

    Tests that mutate the output directory (restart deletes files, expand adds
    jobs) should use this fixture instead of ``completed_sim_output`` directly
    so that each test starts from a pristine completed state.
    """
    copy_root = _make_shared_tmp_dir()
    copy_dir = copy_root / "output"
    shutil.copytree(completed_sim_output, copy_dir)
    yield copy_dir
    _cleanup_dir(copy_root)


@pytest.fixture
def slurm_project(request: pytest.FixtureRequest) -> str:
    """SLURM project for cluster tests, from --slurm-project CLI option."""
    return str(request.config.getoption("--slurm-project"))


def _run_psimulate(
    args: list[str],
    timeout: int = _TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a psimulate CLI command as a subprocess."""
    cmd = ["psimulate", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _find_output_dir(result_directory: Path) -> Path:
    """Discover the timestamped output directory created by ``psimulate run``.

    ``psimulate run`` creates ``result_directory / model_name / timestamp``.
    This helper navigates that structure to find the leaf directory.
    """
    # result_directory / <model_name> / <timestamp>
    model_dirs = [d for d in result_directory.iterdir() if d.is_dir()]
    assert len(model_dirs) == 1, (
        f"Expected exactly 1 model directory in {result_directory}, "
        f"found {len(model_dirs)}: {model_dirs}"
    )
    timestamp_dirs = [d for d in model_dirs[0].iterdir() if d.is_dir()]
    assert len(timestamp_dirs) == 1, (
        f"Expected exactly 1 timestamp directory in {model_dirs[0]}, "
        f"found {len(timestamp_dirs)}: {timestamp_dirs}"
    )
    return timestamp_dirs[0]


def _read_metadata(output_dir: Path) -> pd.DataFrame:
    """Read the finished simulation metadata CSV from an output directory."""
    metadata_path = output_dir / "finished_sim_metadata.csv"
    assert metadata_path.exists(), f"Metadata file not found at {metadata_path}"
    return pd.read_csv(metadata_path)


def _common_slurm_args(slurm_project: str) -> list[str]:
    """Return common SLURM-related CLI arguments used across tests."""
    return [
        "-P",
        slurm_project,
        "-r",
        "00:03:00",
        "-m",
        "1",
    ]


def _run_basic_simulation(
    tmp_path: Path,
    slurm_project: str,
    extra_args: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run a basic psimulate simulation and return (process_result, output_dir).

    This is a shared helper for tests that need a completed simulation run
    as a prerequisite.
    """
    result_dir = tmp_path / "results"
    result_dir.mkdir()

    args = [
        "run",
        str(_MODEL_SPEC),
        str(_BRANCHES),
        "-o",
        str(result_dir),
        *_common_slurm_args(slurm_project),
        "-w",
        str(_EXPECTED_TOTAL_JOBS),
    ]
    if extra_args:
        args.extend(extra_args)

    proc = _run_psimulate(args)
    if proc.returncode != 0:
        # Include stderr in assertion for debugging
        pytest.fail(
            f"psimulate run failed with exit code {proc.returncode}.\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    output_dir = _find_output_dir(result_dir)
    return proc, output_dir


class TestPsimulateRun:
    """E2E tests for ``psimulate run``."""

    @pytest.mark.xfail(reason="psimulate jobs on jenkins are not configured")
    def test_basic_run(self, completed_sim_output: Path) -> None:
        """Run a minimal simulation and verify output files are created."""
        output_dir = completed_sim_output

        # Verify metadata file
        metadata = _read_metadata(output_dir)
        assert (
            len(metadata) == _EXPECTED_TOTAL_JOBS
        ), f"Expected {_EXPECTED_TOTAL_JOBS} rows in metadata, got {len(metadata)}"

        # Verify expected columns exist
        assert "input_draw" in metadata.columns
        assert "random_seed" in metadata.columns
        assert pd.api.types.is_numeric_dtype(metadata["input_draw"])
        assert pd.api.types.is_numeric_dtype(metadata["random_seed"])

        # Verify all draw/seed combinations are unique
        metadata_draw_seed_pairs = metadata[["input_draw", "random_seed"]].drop_duplicates()
        assert len(metadata_draw_seed_pairs) == _EXPECTED_TOTAL_JOBS

        # Verify supporting output files exist
        assert (output_dir / "model_specification.yaml").exists()
        assert (output_dir / "keyspace.yaml").exists()
        assert (output_dir / "branches.yaml").exists()
        assert (output_dir / "requirements.txt").exists()

        # Verify keyspace YAML content
        keyspace = yaml.safe_load((output_dir / "keyspace.yaml").read_text())
        assert "input_draw" in keyspace
        assert "random_seed" in keyspace
        assert len(keyspace["input_draw"]) == 2  # input_draw_count: 2
        assert len(keyspace["random_seed"]) == 2  # random_seed_count: 2

        # Verify log directories were created
        log_dirs = list((output_dir / "logs").iterdir())
        assert len(log_dirs) >= 1, "Expected at least one log directory"

        # Verify results output from DeathsObserver
        results_dir = output_dir / "results"
        assert results_dir.exists()

        deaths_dir = results_dir / "dead"
        assert deaths_dir.exists()

        deaths_df = pd.read_parquet(deaths_dir)

        assert "input_draw" in deaths_df.columns
        assert "random_seed" in deaths_df.columns

        deaths_combinations = deaths_df[["input_draw", "random_seed"]].drop_duplicates()

        assert len(deaths_combinations) == _EXPECTED_TOTAL_JOBS, (
            f"Expected results for {_EXPECTED_TOTAL_JOBS} draw/seed combinations, "
            f"got {len(deaths_combinations)}"
        )

        # Verify the specific combinations match the metadata
        pd.testing.assert_frame_equal(
            deaths_combinations.sort_values(["input_draw", "random_seed"]).reset_index(
                drop=True
            ),
            metadata_draw_seed_pairs.sort_values(["input_draw", "random_seed"]).reset_index(
                drop=True
            ),
        )

    @pytest.mark.xfail(reason="psimulate jobs on jenkins are not configured")
    def test_run_with_max_workers(self, shared_tmp_path: Path, slurm_project: str) -> None:
        """Verify that --max-workers (-w) is accepted and all jobs still complete."""
        result_dir = shared_tmp_path / "results"
        result_dir.mkdir()

        proc = _run_psimulate(
            [
                "run",
                str(_MODEL_SPEC),
                str(_BRANCHES),
                "-o",
                str(result_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                "2",  # Limit to 2 concurrent workers for 4 jobs
            ]
        )
        assert proc.returncode == 0, (
            f"psimulate run with --max-workers 2 failed.\n" f"STDERR:\n{proc.stderr}"
        )

        output_dir = _find_output_dir(result_dir)
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS


class TestPsimulateRestart:
    """E2E tests for ``psimulate restart``."""

    @pytest.mark.xfail(reason="psimulate jobs on jenkins are not configured")
    def test_restart_completes_remaining(
        self, completed_sim_copy: Path, slurm_project: str
    ) -> None:
        """Delete partial outputs, restart, and verify only missing jobs re-run."""
        output_dir = completed_sim_copy

        # Verify initial completion
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS

        # Simulate a partial run by removing SOME outputs.
        # Delete metadata file and half of the result files.
        metadata_path = output_dir / "finished_sim_metadata.csv"
        metadata_path.unlink()

        results_dir = output_dir / "results"
        if results_dir.exists():
            result_files = list(results_dir.iterdir())
            # Delete half of the result files to simulate partial completion
            files_to_delete = result_files[: len(result_files) // 2]
            for f in files_to_delete:
                f.unlink()

        # Restart -- should re-run only the missing jobs
        proc = _run_psimulate(
            [
                "restart",
                str(output_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
            ]
        )
        assert proc.returncode == 0, f"psimulate restart failed.\nSTDERR:\n{proc.stderr}"

        # Verify all jobs completed (both preserved and re-run)
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS

        # Verify draw/seed combinations are correct
        draw_seed_pairs = metadata[["input_draw", "random_seed"]].drop_duplicates()
        assert len(draw_seed_pairs) == _EXPECTED_TOTAL_JOBS


class TestPsimulateExpand:
    """E2E tests for ``psimulate expand``."""

    @pytest.mark.xfail(reason="psimulate jobs on jenkins are not configured")
    def test_expand_adds_draws_and_seeds(
        self, completed_sim_copy: Path, slurm_project: str
    ) -> None:
        """Expand a completed run by adding draws and seeds, verify new jobs complete."""
        output_dir = completed_sim_copy

        # Verify initial completion: 2 draws x 2 seeds = 4 jobs
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS
        initial_draws = set(metadata["input_draw"])
        initial_seeds = set(metadata["random_seed"])

        # Expand by adding 1 draw and 1 seed.
        # New jobs: (1 new draw x 2 old seeds) + (3 total draws x 1 new seed) = 2 + 3 = 5
        # Total: 4 original + 5 new = 9
        proc = _run_psimulate(
            [
                "expand",
                str(output_dir),
                "--add-draws",
                "1",
                "--add-seeds",
                "1",
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
            ]
        )
        assert (
            proc.returncode == 0
        ), f"psimulate expand failed.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        metadata = _read_metadata(output_dir)
        expected_total = 9  # 3 draws x 3 seeds
        assert (
            len(metadata) == expected_total
        ), f"Expected {expected_total} rows after expand, got {len(metadata)}"

        # Verify we have 3 distinct draws (original 2 + 1 new)
        expanded_draws = set(metadata["input_draw"])
        assert (
            len(expanded_draws) == 3
        ), f"Expected 3 distinct draws, got {len(expanded_draws)}: {expanded_draws}"
        new_draws = expanded_draws - initial_draws
        assert len(new_draws) == 1, f"Expected 1 new draw, got {new_draws}"

        # Verify we have 3 distinct seeds (original 2 + 1 new)
        expanded_seeds = set(metadata["random_seed"])
        assert (
            len(expanded_seeds) == 3
        ), f"Expected 3 distinct seeds, got {len(expanded_seeds)}: {expanded_seeds}"
        new_seeds = expanded_seeds - initial_seeds
        assert len(new_seeds) == 1, f"Expected 1 new seed, got {new_seeds}"

        # Every draw/seed pair should be unique
        draw_seed_pairs = metadata[["input_draw", "random_seed"]].drop_duplicates()
        assert len(draw_seed_pairs) == expected_total


class TestPsimulateLoadTest:
    """E2E tests for ``psimulate test``."""

    # Number of workers to use for the load test
    _NUM_WORKERS = 2

    @pytest.mark.xfail(reason="large_results load test currently failing")
    def test_large_results(self, shared_tmp_path: Path, slurm_project: str) -> None:
        """Run the large_results load test and verify outputs are produced."""
        result_dir = shared_tmp_path / "load_test_results"
        result_dir.mkdir()

        proc = _run_psimulate(
            [
                "test",
                "large_results",
                "-n",
                str(self._NUM_WORKERS),
                "-o",
                str(result_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(self._NUM_WORKERS),
            ],
            timeout=_TIMEOUT,
        )
        assert proc.returncode == 0, (
            f"psimulate test large_results failed.\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

        # Verify that the output directory was created with results
        output_dir = _find_output_dir(result_dir)
        metadata = _read_metadata(output_dir)
        assert (
            len(metadata) == self._NUM_WORKERS
        ), f"Expected {self._NUM_WORKERS} rows in metadata, got {len(metadata)}"
