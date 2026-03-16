from copy import deepcopy

from vivarium_cluster_tools.psimulate.jobs import JobParameters, generate_task_id


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
