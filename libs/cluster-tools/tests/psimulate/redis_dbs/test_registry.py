from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.redis_dbs.registry import RegistryManager


@pytest.mark.parametrize("num_queues", [1, 7, 10, 19, 37])
@pytest.mark.parametrize("num_jobs", [1, 10, 42, 1337, 2023])
def test_allocate_jobs(mocker: MockerFixture, num_queues: int, num_jobs: int) -> None:
    # mock the QueueManager class
    mocker.patch("vivarium_cluster_tools.psimulate.redis_dbs.registry.QueueManager")
    queues = [(f"queue_{i}", i) for i in range(num_queues)]
    manager = RegistryManager(queues, num_jobs, 0)
    # create a list of dummy jobs based on a list of integers
    jobs = [{"job": i} for i in range(num_jobs)]
    # make an array of all the jobs in every queue
    jobs_by_queue = list(manager.allocate_jobs(jobs))
    # Check that each "layer" of jobs has strictly smaller-priority jobs than the next
    largest_queue_size = max(len(queue) for queue in jobs_by_queue)
    for i in range(largest_queue_size - 1):
        this_layer = {queue[i]["job"] for queue in jobs_by_queue if len(queue) > i}
        next_layer = {queue[i + 1]["job"] for queue in jobs_by_queue if len(queue) > i + 1}
        assert max(this_layer) < min(next_layer)


def create_mock_job(job_id: str, job_parameters: dict[str, Any]) -> MagicMock:
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.kwargs = {"job_parameters": job_parameters}
    return mock_job


@pytest.fixture
def mock_registry_manager() -> RegistryManager:
    # Mock the QueueManager used within RegistryManager
    with patch(
        "vivarium_cluster_tools.psimulate.redis_dbs.registry.QueueManager"
    ) as MockQueueManager:
        # Create mock jobs for each QueueManager
        mock_jobs_list_1 = [
            create_mock_job("1", {"input_draw": 1, "random_seed": 123}),
            create_mock_job("2", {"input_draw": 2, "random_seed": 456}),
        ]
        mock_jobs_list_2 = [
            create_mock_job("3", {"input_draw": 3, "random_seed": 789}),
            create_mock_job("4", {"input_draw": 4, "random_seed": 101}),
        ]

        # Mock instances of QueueManager and their _queue.jobs to return the mock jobs
        mock_queue_manager_instance_1 = MagicMock()
        mock_queue_manager_instance_1._queue.jobs = mock_jobs_list_1

        mock_queue_manager_instance_2 = MagicMock()
        mock_queue_manager_instance_2._queue.jobs = mock_jobs_list_2

        # Assuming RegistryManager initializes two QueueManagers internally
        MockQueueManager.side_effect = [
            mock_queue_manager_instance_1,
            mock_queue_manager_instance_2,
        ]

        # Initialize your RegistryManager with mocked QueueManagers
        manager = RegistryManager([("queue_0", 0), ("queue_1", 0)], 4, 0)

        return manager


def test_registry_manager_get_params_by_job(mock_registry_manager: RegistryManager) -> None:
    params_by_job = mock_registry_manager.get_params_by_job()
    assert params_by_job["1"] == {"input_draw": 1, "random_seed": 123}
    assert params_by_job["2"] == {"input_draw": 2, "random_seed": 456}
    assert params_by_job["3"] == {"input_draw": 3, "random_seed": 789}
    assert params_by_job["4"] == {"input_draw": 4, "random_seed": 101}
