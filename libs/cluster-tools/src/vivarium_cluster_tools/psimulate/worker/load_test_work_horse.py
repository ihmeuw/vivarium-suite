"""
================
Load Test Worker
================

Worker executable for doing load testing.

"""

import time
from collections.abc import Callable

import numpy as np
import pandas as pd
from loguru import logger
from vivarium.framework.randomness import get_hash

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES
from vivarium_cluster_tools.psimulate.jobs import JobParameters


def get_psimulate_test_dict() -> (
    dict[str, dict[str, Callable[[JobParameters], pd.DataFrame] | str | int]]
):
    return {
        "sleep": {
            "function": sleep_test,
            "peak_memory": 1,
            "max_runtime": "00:05:00",
        },
        "large_results": {
            "function": large_results_test,
            "peak_memory": 2,
            "max_runtime": "00:30:00",
        },
    }


def sleep_test(job_parameters: JobParameters) -> pd.DataFrame:
    min_sleep_time = 5
    max_sleep_time = 60
    rs = np.random.RandomState(seed=get_hash(f"sleep_test_{job_parameters.random_seed}"))
    sleep_time = rs.randint(min_sleep_time, max_sleep_time)

    logger.info(f"Sleeping for {sleep_time}s.")
    time.sleep(sleep_time)
    logger.info(f"Sleep test successful.")
    return pd.DataFrame(
        {"sleep_time": sleep_time}, index=pd.Index([job_parameters.random_seed], name="seed")
    )


def large_results_test(job_parameters: JobParameters) -> pd.DataFrame:
    time.sleep(30)
    np.random.seed(seed=get_hash(f"large_results_test_{job_parameters.random_seed}"))
    return pd.DataFrame(np.random.random(10_000_000).reshape((1_000_000, 10)))


def work_horse(job_parameters: JobParameters) -> pd.DataFrame:
    node = f"{ENV_VARIABLES.HOSTNAME.value}"
    job = f"{ENV_VARIABLES.JOB_ID.value}:{ENV_VARIABLES.TASK_ID.value}"

    test_type = job_parameters.extras["test_type"]
    test_runner = get_psimulate_test_dict()[test_type]["function"]

    logger.info(f"Launching new job {job} on {node}")
    logger.info(f"Starting job: {job_parameters}")
    if not callable(test_runner):
        raise ValueError(f"Test runner for {test_type} is not callable: {test_runner}")
    try:
        return test_runner(job_parameters)
    except Exception:
        logger.exception("Unhandled exception in worker")
        raise
    finally:
        logger.info(f"Exiting job: {job_parameters}")
