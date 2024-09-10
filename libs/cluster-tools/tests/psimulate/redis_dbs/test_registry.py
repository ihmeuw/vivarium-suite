from unittest.mock import MagicMock, patch

import pytest

from vivarium_cluster_tools.psimulate.redis_dbs.registry import RegistryManager


@pytest.mark.parametrize("num_queues", [1, 7, 10, 19, 37])
@pytest.mark.parametrize("num_jobs", [1, 10, 42, 1337, 2023])
def test_allocate_jobs(mocker, num_queues, num_jobs):
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


def create_mock_job(job_id, job_parameters):
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.kwargs = {"raw_job_parameters_dict": job_parameters}
    return mock_job


@pytest.fixture
def mock_registry_manager():
    # Mock the QueueManager used within RegistryManager
    with patch(
        "vivarium_cluster_tools.psimulate.redis_dbs.registry.QueueManager"
    ) as MockQueueManager:
        # Create mock jobs for each QueueManager
        mock_jobs_list_1 = [
            create_mock_job(1, "parameters1"),
            create_mock_job(2, "parameters2"),
        ]
        mock_jobs_list_2 = [
            create_mock_job(3, "parameters3"),
            create_mock_job(4, "parameters4"),
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


def test_registry_manager_get_params_by_job(mock_registry_manager):
    params_by_job = mock_registry_manager.get_params_by_job()
    assert params_by_job[1] == "parameters1"
    assert params_by_job[2] == "parameters2"
    assert params_by_job[3] == "parameters3"
    assert params_by_job[4] == "parameters4"
