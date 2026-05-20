from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from vivarium_cluster_tools.psimulate.branches import Keyspace
from vivarium_cluster_tools.psimulate.jobs import (
    JobParameters,
    build_job_list,
    generate_task_id,
)


def test_branch_config_immutable() -> None:
    "Test that the branch_configuration doesn't get mutated in place."
    original_branch_config = {"foo": "bar", "input_data": {"spam": "eggs"}}
    params = JobParameters(
        model_specification="model_spec.yaml",
        branch_configuration=deepcopy(original_branch_config),
        input_draw=0,
        random_seed=1,
        results_path="results",
        backup_configuration={},
        extras={},
        worker_logging_root="/tmp/worker_logs",
    )
    params.sim_config  # This was previously causing branch_config to change
    assert params.branch_configuration == original_branch_config


class TestGenerateTaskId:
    @property
    def branch_config(self) -> dict[str, dict[str, str]]:
        return {"scenario": {"treatment": "A"}}

    def test_deterministic(self) -> None:
        """Same inputs always produce the same task ID."""
        id1 = generate_task_id(1, 42, self.branch_config)
        id2 = generate_task_id(1, 42, self.branch_config)
        assert id1 == id2

    def test_different_draw(self) -> None:
        """Different input draws produce different IDs."""
        id1 = generate_task_id(1, 42, self.branch_config)
        id2 = generate_task_id(2, 42, self.branch_config)
        assert id1 != id2

    def test_different_seed(self) -> None:
        """Different random seeds produce different IDs."""

        id1 = generate_task_id(1, 42, self.branch_config)
        id2 = generate_task_id(1, 43, self.branch_config)
        assert id1 != id2

    def test_different_branch(self) -> None:
        """Different branch configurations produce different IDs."""
        id1 = generate_task_id(1, 42, {"scenario": {"treatment": "A"}})
        id2 = generate_task_id(1, 42, {"scenario": {"treatment": "B"}})
        assert id1 != id2

    def test_length(self) -> None:
        """Task ID is 16 hex characters."""
        task_id = generate_task_id(0, 0, {})
        assert len(task_id) == 16
        assert all(c in "0123456789abcdef" for c in task_id)

    def test_key_order_insensitive(self) -> None:
        """Dict key order should not affect the hash (JSON sort_keys=True)."""
        branch1 = {"b": 2, "a": 1}
        branch2 = {"a": 1, "b": 2}
        assert generate_task_id(1, 42, branch1) == generate_task_id(1, 42, branch2)


class TestJobParametersTaskId:
    def test_task_id_property(self) -> None:
        """JobParameters.task_id matches generate_task_id for the same params."""
        job_parameters = JobParameters(
            model_specification="test.yaml",
            branch_configuration={"scenario": "A"},
            input_draw=5,
            random_seed=10,
            results_path="/tmp/results",
            backup_configuration={},
            extras={},
            worker_logging_root="/tmp/worker_logs",
        )
        expected = generate_task_id(5, 10, {"scenario": "A"})
        assert job_parameters.task_id == expected


class TestBuildJobList:
    # Two draws x two seeds x one (empty) branch.
    _INPUT_DRAWS = [0, 1]
    _RANDOM_SEEDS = [100, 200]
    _EXPECTED_JOB_KEYS = {(0, 100), (0, 200), (1, 100), (1, 200)}

    def _make_keyspace(self) -> Keyspace:
        return Keyspace(
            branches=[{}],
            keyspace={
                "input_draw": self._INPUT_DRAWS,
                "random_seed": self._RANDOM_SEEDS,
            },
        )

    @pytest.mark.parametrize("backup_freq", [None, 300])
    def test_backup_freq_propagates_into_job_parameters(
        self, tmp_path: Path, backup_freq: int | None
    ) -> None:
        """Covers the ``backup_freq=None`` case used when running psimulate
        without backups: ``build_job_list`` forwards ``backup_freq`` (and the
        rest of the backup configuration) into every ``JobParameters``.
        """
        backup_dir = tmp_path / "backups"
        backup_metadata_path = backup_dir / "backup_metadata.csv"
        jobs, num_completed = build_job_list(
            model_specification_path=tmp_path / "model_spec.yaml",
            output_root=tmp_path / "results",
            keyspace=self._make_keyspace(),
            finished_sim_metadata=pd.DataFrame(),
            backup_freq=backup_freq,
            backup_dir=backup_dir,
            backup_metadata_path=backup_metadata_path,
            worker_logging_root=tmp_path / "logs",
            extras={},
        )

        assert num_completed == 0
        # Guards against a regression in keyspace iteration co-occurring with
        # a backup_freq change.
        assert {(job.input_draw, job.random_seed) for job in jobs} == self._EXPECTED_JOB_KEYS
        for job in jobs:
            assert job.backup_configuration == {
                "backup_dir": backup_dir,
                "backup_freq": backup_freq,
                "backup_metadata_path": backup_metadata_path,
            }

    @pytest.mark.parametrize("backup_freq", [None, 300])
    def test_already_complete_jobs_filtered(
        self, tmp_path: Path, backup_freq: int | None
    ) -> None:
        """When ``finished_sim_metadata`` already contains a (draw, seed) pair,
        ``build_job_list`` excludes it from the returned jobs and counts it
        toward ``num_completed``.
        """
        backup_dir = tmp_path / "backups"
        # Mark one of the four (draw, seed) pairs as already complete.
        already_done = pd.DataFrame(
            [{"input_draw": self._INPUT_DRAWS[0], "random_seed": self._RANDOM_SEEDS[0]}]
        )
        jobs, num_completed = build_job_list(
            model_specification_path=tmp_path / "model_spec.yaml",
            output_root=tmp_path / "results",
            keyspace=self._make_keyspace(),
            finished_sim_metadata=already_done,
            backup_freq=backup_freq,
            backup_dir=backup_dir,
            backup_metadata_path=backup_dir / "backup_metadata.csv",
            worker_logging_root=tmp_path / "logs",
            extras={},
        )

        assert num_completed == 1
        skipped = (self._INPUT_DRAWS[0], self._RANDOM_SEEDS[0])
        assert {(job.input_draw, job.random_seed) for job in jobs} == (
            self._EXPECTED_JOB_KEYS - {skipped}
        )
