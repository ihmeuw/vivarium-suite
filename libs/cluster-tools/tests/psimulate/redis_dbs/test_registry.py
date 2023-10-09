import pytest

from vivarium_cluster_tools.psimulate.redis_dbs.registry import RegistryManager


@pytest.mark.parametrize("num_queues", [1, 7, 10, 19, 37])
@pytest.mark.parametrize("num_jobs", [1, 10, 42, 1337, 2023])
def test_allocate_jobs(mocker, num_queues, num_jobs):
    # mock the QueueManager class
    mocker.patch("vivarium_cluster_tools.psimulate.redis_dbs.registry.QueueManager")
    queues = [(f"queue_{i}", i) for i in range(num_queues)]
    manager = RegistryManager(queues, 0)
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
