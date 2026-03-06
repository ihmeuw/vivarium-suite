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

from vivarium_cluster_tools.psimulate.results.writing import collect_metadata

_DATA_DIR = Path(__file__).parent / "data"
_MODEL_SPEC = _DATA_DIR / "e2e_model_spec.yaml"
_MODEL_SPEC_FAIL_ONCE = _DATA_DIR / "e2e_model_spec_fail_once.yaml"
_BRANCHES = _DATA_DIR / "e2e_branches.yaml"

# Total jobs produced by the branch config (2 draws x 2 seeds x 1 branch)
_EXPECTED_TOTAL_JOBS = 4

# Generous timeout for SLURM scheduling + execution (10 minutes)
_TIMEOUT = 600

RESULTS_DIR = "/mnt/team/simulation_science/priv/engineering/tests/output/"

# Don't enforce weekly run requirement during development
pytestmark = [pytest.mark.cluster, pytest.mark.slow]


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

    The copy directory name includes the original timestamp *and* the unique
    temp-dir suffix so that Jobmon workflow names (derived from
    ``output_paths.root.name``) are unique both within a session and across
    repeated test runs.
    """
    copy_root = _make_shared_tmp_dir()
    # e.g. "2026_02_27_11_01_29_tmp1u9ock2s" — timestamp for readability,
    # temp suffix for uniqueness across runs.
    unique_name = f"{completed_sim_output.name}_{copy_root.name}"
    copy_dir = copy_root / unique_name
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
    """Collect metadata for completed tasks from per-task JSON + result parquet files."""
    metadata_dir = output_dir / "metadata"
    results_dir = output_dir / "results"
    assert metadata_dir.exists(), f"Metadata directory not found at {metadata_dir}"
    assert results_dir.exists(), f"Results directory not found at {results_dir}"
    metadata = collect_metadata(metadata_dir, results_dir)
    assert (
        not metadata.empty
    ), f"No completed task metadata found in {metadata_dir} / {results_dir}"
    return metadata


def _get_result_task_ids(results_dir: Path) -> dict[str, set[str]]:
    """Return ``{metric_name: {task_id, ...}}`` from parquet files under *results_dir*.

    Each metric subdirectory (e.g. ``results/dead/``) contains chunked parquet
    files whose stems encode the output file number (``0000``, ``0001``, …).
    Within each file, individual task contributions are identified by their
    unique ``(input_draw, random_seed)`` pair.  We read those two columns
    and return the set of composite task-id strings per metric so callers can
    compare counts before and after an operation.
    """
    result: dict[str, set[str]] = {}
    for metric_dir in sorted(results_dir.iterdir()):
        if not metric_dir.is_dir():
            continue
        df = pd.read_parquet(metric_dir, columns=["input_draw", "random_seed"])
        ids = {
            f"{row.input_draw}_{row.random_seed}"
            for row in df[["input_draw", "random_seed"]].drop_duplicates().itertuples()
        }
        result[metric_dir.name] = ids
    return result


def _assert_result_task_counts(results_dir: Path, expected: int) -> dict[str, set[str]]:
    """Assert every metric subdir in *results_dir* has exactly *expected* task IDs.

    Returns the ``{metric: {task_id, ...}}`` dict for further inspection.
    """
    task_ids = _get_result_task_ids(results_dir)
    assert task_ids, f"No metric subdirectories found under {results_dir}"
    for metric, ids in task_ids.items():
        assert (
            len(ids) == expected
        ), f"Metric '{metric}': expected {expected} task results, got {len(ids)}"
    return task_ids


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

        # Verify each metric directory has exactly one result per job
        _assert_result_task_counts(results_dir, _EXPECTED_TOTAL_JOBS)

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

    def test_run_with_auto_retry(self, shared_tmp_path: Path, slurm_project: str) -> None:
        """Verify Jobmon's automatic retry makes fail-once tasks pass.

        Uses ``FailOnceComponent`` with the default ``--max-attempts 3``.
        Every task fails on its first attempt and succeeds on the second
        (because the sentinel file now exists).  The run should complete
        successfully without a manual restart.
        """
        sentinel_dir = shared_tmp_path / "fail_sentinels"
        sentinel_dir.mkdir()

        result_dir = shared_tmp_path / "results"
        result_dir.mkdir()

        env = {**os.environ, "FAIL_ONCE_SENTINEL_DIR": str(sentinel_dir)}
        proc = subprocess.run(
            [
                "psimulate",
                "run",
                str(_MODEL_SPEC_FAIL_ONCE),
                str(_BRANCHES),
                "-o",
                str(result_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            env=env,
        )
        assert proc.returncode == 0, (
            f"psimulate run with auto-retry failed.\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

        output_dir = _find_output_dir(result_dir)
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS
        _assert_result_task_counts(output_dir / "results", _EXPECTED_TOTAL_JOBS)


class TestPsimulateRestart:
    """E2E tests for ``psimulate restart``.

    Restart now uses Jobmon's native resume: the workflow is rebuilt with the
    same ``workflow_args`` and ``workflow.run(resume=True)`` is called.  Jobmon
    skips tasks that already completed (status DONE) and retries any that
    failed or were pending.
    """

    def test_restart_of_completed_run_raises(
        self, completed_sim_output: Path, slurm_project: str
    ) -> None:
        """Restart a fully-completed run and verify Jobmon rejects it.

        When all tasks already succeeded, Jobmon raises
        ``WorkflowAlreadyComplete`` rather than silently re-running anything.
        psimulate should propagate this as a non-zero exit with the
        distinctive error message in stderr.
        """
        output_dir = completed_sim_output

        # Verify initial completion
        metadata_before = _read_metadata(output_dir)
        assert len(metadata_before) == _EXPECTED_TOTAL_JOBS
        _assert_result_task_counts(output_dir / "results", _EXPECTED_TOTAL_JOBS)

        # Restart -- Jobmon should refuse to resume a DONE workflow
        proc = _run_psimulate(
            [
                "restart",
                str(output_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
            ]
        )
        assert proc.returncode != 0, (
            f"Expected psimulate restart to fail for a completed workflow, "
            f"but it exited 0.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        assert "WorkflowAlreadyComplete" in proc.stderr, (
            f"Expected WorkflowAlreadyComplete in stderr.\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    def test_restart_after_total_failure(
        self, shared_tmp_path: Path, slurm_project: str
    ) -> None:
        """Run a simulation where every task fails, then restart to completion.

        Uses ``FailOnceComponent`` which creates a sentinel file per
        (draw, seed) on first execution and raises ``RuntimeError``.  On the
        Jobmon-resume restart the sentinels already exist, so every task
        succeeds.  The ``FAIL_ONCE_SENTINEL_DIR`` env var is set in the test
        environment and propagated through SLURM to the workers.
        """
        sentinel_dir = shared_tmp_path / "fail_sentinels"
        sentinel_dir.mkdir()

        result_dir = shared_tmp_path / "results"
        result_dir.mkdir()

        # Initial run with --max-attempts 1: every task fails permanently
        env = {**os.environ, "FAIL_ONCE_SENTINEL_DIR": str(sentinel_dir)}
        initial_proc = subprocess.run(
            [
                "psimulate",
                "run",
                str(_MODEL_SPEC_FAIL_ONCE),
                str(_BRANCHES),
                "-o",
                str(result_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
                "--max-attempts",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            env=env,
        )
        # Workflow should finish but not with a clean DONE (all tasks failed).
        output_dir = _find_output_dir(result_dir)
        results_dir = output_dir / "results"

        # No completed results expected (every task crashed before writing).
        completed_ids = _get_result_task_ids(results_dir) if results_dir.exists() else {}
        total_completed = sum(len(ids) for ids in completed_ids.values())
        assert total_completed == 0, (
            f"Expected 0 completed tasks after initial all-fail run, "
            f"got {total_completed}.  STDOUT:\n{initial_proc.stdout}\n"
            f"STDERR:\n{initial_proc.stderr}"
        )

        # Sentinel files should exist for every (draw, seed).
        sentinel_files = list(sentinel_dir.iterdir())
        assert len(sentinel_files) >= _EXPECTED_TOTAL_JOBS, (
            f"Expected {_EXPECTED_TOTAL_JOBS} sentinel files, "
            f"got {len(sentinel_files)}: {sentinel_files}"
        )

        #  Restart: sentinels exist, so every task now succeeds
        restart_proc = subprocess.run(
            [
                "psimulate",
                "restart",
                str(output_dir),
                *_common_slurm_args(slurm_project),
                "-w",
                str(_EXPECTED_TOTAL_JOBS),
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            env=env,
        )
        assert restart_proc.returncode == 0, (
            f"psimulate restart failed.\n"
            f"STDOUT:\n{restart_proc.stdout}\n"
            f"STDERR:\n{restart_proc.stderr}"
        )

        # All jobs should now be complete.
        metadata = _read_metadata(output_dir)
        assert len(metadata) == _EXPECTED_TOTAL_JOBS, (
            f"Expected {_EXPECTED_TOTAL_JOBS} completed jobs after restart, "
            f"got {len(metadata)}"
        )
        _assert_result_task_counts(results_dir, _EXPECTED_TOTAL_JOBS)


class TestPsimulateExpand:
    """E2E tests for ``psimulate expand``."""

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

        # Verify initial result files
        results_dir = output_dir / "results"
        _assert_result_task_counts(results_dir, _EXPECTED_TOTAL_JOBS)

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

        # Verify result files reflect the expanded total
        _assert_result_task_counts(results_dir, expected_total)


class TestPsimulateLoadTest:
    """E2E tests for ``psimulate test``."""

    # Number of workers to use for the load test
    _NUM_WORKERS = 2

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
