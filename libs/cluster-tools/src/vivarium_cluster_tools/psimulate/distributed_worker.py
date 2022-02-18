"""
==================
Distributed Worker
==================

Custom RQ worker for running vivarium jobs.

"""
import json
import math
import os
import random
from pathlib import Path
from time import sleep, time
from traceback import format_exc

import pandas as pd
import redis
from loguru import logger
from rq import Queue, get_current_job
from rq.job import JobStatus
from rq.registry import FailedJobRegistry
from rq.worker import Worker
from vivarium.framework.engine import SimulationContext
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate import jobs
from vivarium_cluster_tools.psimulate.cluster import ENV_VARIABLES
from vivarium_cluster_tools.vipin.perf_counters import CounterSnapshot


def retry_handler(job, *exc_info):
    retries = job.meta.get("remaining_retries", 2)

    if retries > 0:
        retries -= 1
        job.meta["remaining_retries"] = retries
        job.set_status(JobStatus.QUEUED)
        job.exc_info = exc_info
        job.save()
        q = Queue(name=job.origin, connection=job.connection)
        q.enqueue_job(job)
        logger.info(f"Retrying job {job.id}")
    else:
        logger.error(f"Failing job {job.id}")
        # exc_string = Worker._get_safe_exception_string(traceback.format_exception(*exc_info))
        q = Queue(name=job.origin, connection=job.connection)
        failed_queue = FailedJobRegistry(queue=q)
        failed_queue.add(job, exc_string=exc_info)
    return False


class ResilientWorker(Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.acceptable_failure_count = 3

    def work(self, *args, **kwargs):
        worker_ = self.name
        logging_directory = Path(ENV_VARIABLES.VIVARIUM_LOGGING_DIRECTORY.value)
        logger.add(logging_directory / (str(worker_) + ".log"), level="DEBUG", serialize=True)
        kwargs["logging_level"] = "DEBUG"

        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).work(*args, **kwargs)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random() * 60
                logger.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                sleep(backoff)
        logger.error(f"Ran out of retries. Killing worker")

    def main_work_horse(self, job, queue):
        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).main_work_horse(job, queue)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random() * 60
                logger.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                sleep(backoff)
        logger.error(f"Ran out of retries. Killing work horse")

    def fork_work_horse(self, job, queue):
        """Spawns a work horse to perform the actual work and passes it a job."""
        child_pid = os.fork()
        ENV_VARIABLES.RQ_WORKER_ID.update(self.name)
        ENV_VARIABLES.RQ_JOB_ID.update(job.id)
        if child_pid == 0:
            self.main_work_horse(job, queue)
            os._exit(0)  # just in case
        else:
            self._horse_pid = child_pid
            self.procline("Forked {0} at {1}".format(child_pid, time()))


def worker(job_parameters: dict):
    node = f"{ENV_VARIABLES.CLUSTER_NAME.value}:{ENV_VARIABLES.HOSTNAME.value}"
    job = f"{ENV_VARIABLES.JOB_NAME.value}: {ENV_VARIABLES.JOB_ID.value}:{ENV_VARIABLES.TASK_ID.value}"

    job_parameters = jobs.JobParameters(**job_parameters)

    logger.info(f"Launching new job {job} on {node}")
    logger.info(f"Starting job: {job_parameters}")

    try:
        configuration = job_parameters.branch_configuration
        # TODO: Need to test serialization of an empty dict, then this
        #   can go away.  If you're successfully running code and this
        #   assert is still here, delete it.
        assert configuration is not None

        configuration.update(
            {
                "run_configuration": {
                    "run_id": str(get_current_job().id) + "_" + str(time()),
                    "results_directory": job_parameters.results_path,
                    "run_key": job_parameters.job_specific,
                },
                "randomness": {
                    "random_seed": job_parameters.random_seed,
                    "additional_seed": job_parameters.input_draw,
                },
                "input_data": {
                    "input_draw_number": job_parameters.input_draw,
                },
            }
        )

        sim = SimulationContext(
            job_parameters.model_specification, configuration=configuration
        )
        logger.info("Simulation configuration:")
        logger.info(str(sim.configuration))

        start_time = pd.Timestamp(**sim.configuration.time.start.to_dict())
        end_time = pd.Timestamp(**sim.configuration.time.end.to_dict())
        step_size = pd.Timedelta(days=sim.configuration.time.step_size)
        num_steps = int(math.ceil((end_time - start_time) / step_size))

        start_snapshot = CounterSnapshot()
        event = {"start": time()}  # timestamps of application events
        logger.info("Beginning simulation setup.")
        sim.setup()
        event["simulant_initialization_start"] = time()
        exec_time = {
            "setup_minutes": (event["simulant_initialization_start"] - event["start"]) / 60
        }  # execution event
        logger.info(
            f'Simulation setup completed in {exec_time["setup_minutes"]:.3f} minutes.'
        )

        sim.initialize_simulants()
        event["simulation_start"] = time()
        exec_time["simulant_initialization_minutes"] = (
            event["simulation_start"] - event["simulant_initialization_start"]
        ) / 60
        logger.info(
            f'Simulant initialization completed in {exec_time["simulant_initialization_minutes"]:.3f} minutes.'
        )

        logger.info(f"Starting main simulation loop with {num_steps} time steps")
        sim.run()
        event["results_start"] = time()
        exec_time["main_loop_minutes"] = (
            event["results_start"] - event["simulation_start"]
        ) / 60
        exec_time["step_mean_seconds"] = (
            event["results_start"] - event["simulation_start"]
        ) / num_steps
        logger.info(
            f'Simulation main loop completed in {exec_time["main_loop_minutes"]:.3f} minutes.'
        )
        logger.info(f'Average step length was {exec_time["step_mean_seconds"]:.3f} seconds.')

        sim.finalize()
        metrics = sim.report(print_results=False)
        event["end"] = time()
        end_snapshot = CounterSnapshot()

        do_sim_epilogue(start_snapshot, end_snapshot, event, exec_time, job_parameters)

        idx = pd.MultiIndex.from_tuples(
            [(job_parameters.input_draw, job_parameters.random_seed)],
            names=["input_draw_number", "random_seed"],
        )
        output_metrics = pd.DataFrame(metrics, index=idx)
        for k, v in collapse_nested_dict(job_parameters.branch_configuration):
            output_metrics[k] = v
        return output_metrics

    except Exception:
        logger.exception("Unhandled exception in worker")
        job = get_current_job()
        job.meta["root_exception"] = format_exc()
        job.save_meta()
        raise
    finally:
        logger.info(f"Exiting job: {job_parameters}")


def do_sim_epilogue(
    start: CounterSnapshot,
    end: CounterSnapshot,
    event: dict,
    exec_time: dict,
    parameters: jobs.JobParameters,
):
    exec_time["results_minutes"] = (event["end"] - event["results_start"]) / 60
    logger.info(f'Results reporting completed in {exec_time["results_minutes"]:.3f} minutes.')
    exec_time["total_minutes"] = (event["end"] - event["start"]) / 60
    logger.info(f'Total simulation run time {exec_time["total_minutes"]:.3f} minutes.')

    perf_log = logger.add(
        Path(ENV_VARIABLES.VIVARIUM_LOGGING_DIRECTORY.value)
        / f"perf.{ENV_VARIABLES.JOB_ID.value}.{ENV_VARIABLES.TASK_ID.value}.log",
        level="DEBUG",
        serialize=True,
    )
    logger.debug(
        json.dumps(
            {
                "host": ENV_VARIABLES.HOSTNAME.value.split(".")[0],
                "job_number": ENV_VARIABLES.JOB_ID.value,
                "task_number": ENV_VARIABLES.TASK_ID.value,
                "draw": parameters.input_draw,
                "seed": parameters.random_seed,
                "scenario": parameters.branch_configuration,
                "event": event,
                "exec_time": exec_time,
                "counters": (end - start).to_dict(),
            }
        )
    )
    logger.remove(perf_log)
