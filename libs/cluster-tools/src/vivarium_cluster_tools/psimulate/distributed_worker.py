"""
==================
Distributed Worker
==================

Custome RQ worker for running vivarium jobs.

"""
import datetime
import json
import math
import os
import random
from pathlib import Path
from time import sleep, time
from traceback import format_exc
from typing import Mapping

import numpy as np
import pandas as pd
import redis
from loguru import logger
from rq import Queue, get_current_job
from rq.job import JobStatus
from rq.registry import FailedJobRegistry
from rq.worker import StopRequested, Worker

from vivarium_cluster_tools.psimulate import globals as vct_globals
from vivarium_cluster_tools.vipin.perf_counters import CounterSnapshot


def retry_handler(job, *exc_info):
    retries = job.meta.get('remaining_retries', 2)

    if retries > 0:
        retries -= 1
        job.meta['remaining_retries'] = retries
        job.set_status(JobStatus.QUEUED)
        job.exc_info = exc_info
        job.save()
        q = Queue(name=job.origin, connection=job.connection)
        q.enqueue_job(job)
        logger.info(f'Retrying job {job.id}')
    else:
        logger.error(f'Failing job {job.id}')
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
        logger.remove()
        logging_directory = Path(os.environ['VIVARIUM_LOGGING_DIRECTORY'])
        logger.add(logging_directory / (str(worker_) + '.log'), level='DEBUG', serialize=True)

        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).work(*args, **kwargs)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random()*60
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
                backoff = random.random()*60
                logger.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                sleep(backoff)
        logger.error(f"Ran out of retries. Killing work horse")

    def _monitor_work_horse_tick(self, job):
        _, ret_val = os.waitpid(self._horse_pid, 0)
        if ret_val == os.EX_OK:  # The process exited normally.
            return
        job_status = job.get_status()
        if job_status is None:  # Job completed and its ttl has expired
            return
        if job_status not in [JobStatus.FINISHED, JobStatus.FAILED]:

            if not job.ended_at:
                job.ended_at = datetime.datetime.utcnow()

            self.handle_job_failure(job=job)

            # Unhandled failure
            logger.error(f'(work-horse terminated unexpectedly; waitpid returned {ret_val})')

            retry_handler(job, f"Work-horse process was terminated unexpectedly (waitpid returned {ret_val})",
                          None, None)

            self.acceptable_failure_count -= 1
            if self.acceptable_failure_count < 0:
                logger.error("This worker has had multiple jobs fail. That may mean the host is sick. Terminating "
                             "the worker to try and shift load onto other hosts.")
                raise StopRequested()


def worker(parameters: Mapping):
    node = f"{os.environ['SGE_CLUSTER_NAME']}:{os.environ['HOSTNAME']}"
    job = f"{os.environ['JOB_NAME']}: {os.environ['JOB_ID']}:{os.environ['SGE_TASK_ID']}"

    input_draw = parameters['input_draw']
    random_seed = parameters['random_seed']
    model_specification_file = parameters['model_specification_file']
    branch_config = parameters['branch_configuration']
    logger.info(f'Launching new job {job} on {node}')
    logger.info('Starting job: {}'.format((input_draw, random_seed, model_specification_file, branch_config)))

    try:
        np.random.seed([input_draw, random_seed])
        worker_ = get_current_job().id

        from vivarium.framework.engine import SimulationContext
        from vivarium.framework.utilities import collapse_nested_dict

        configuration = {}
        run_key = {'input_draw': input_draw, 'random_seed': random_seed}

        if branch_config is not None:
            configuration.update(dict(branch_config))
            run_key.update(dict(branch_config))

        input_data_config = {
            'input_draw_number': input_draw,
        }
        if vct_globals.ARTIFACT_PATH_KEY in dict(branch_config).get(vct_globals.INPUT_DATA_KEY, {}):
            input_data_config.update({
                vct_globals.ARTIFACT_PATH_KEY: branch_config[vct_globals.INPUT_DATA_KEY][vct_globals.ARTIFACT_PATH_KEY]
            })

        configuration.update({
            'run_configuration': {
                'input_draw_number': input_draw,
                'run_id': str(worker_) + '_' + str(time()),
                'results_directory': parameters['results_path'],
                'run_key': run_key,
            },
            'randomness': {
                'random_seed': random_seed,
                'additional_seed': input_draw,
            },
            vct_globals.INPUT_DATA_KEY: input_data_config
        })

        sim = SimulationContext(model_specification_file, configuration=configuration)
        logger.info('Simulation configuration:')
        logger.info(str(sim.configuration))

        start_time = pd.Timestamp(**sim.configuration.time.start.to_dict())
        end_time = pd.Timestamp(**sim.configuration.time.end.to_dict())
        step_size = pd.Timedelta(days=sim.configuration.time.step_size)
        num_steps = int(math.ceil((end_time - start_time)/step_size))

        start_snapshot = CounterSnapshot()
        event = {'start': time()}  # timestamps of application events
        logger.info('Beginning simulation setup.')
        sim.setup()
        event['simulant_initialization_start'] = time()
        exec_time = {'setup_minutes': (event["simulant_initialization_start"] - event["start"]) / 60}  # execution event
        logger.info(f'Simulation setup completed in {exec_time["setup_minutes"]:.3f} minutes.')

        sim.initialize_simulants()
        event['simulation_start'] = time()
        exec_time['simulant_initialization_minutes'] = (
                (event["simulation_start"] - event["simulant_initialization_start"]) / 60)
        logger.info(f'Simulant initialization completed in {exec_time["simulant_initialization_minutes"]:.3f} minutes.')

        logger.info(f'Starting main simulation loop with {num_steps} time steps')
        sim.run()
        event['results_start'] = time()
        exec_time['main_loop_minutes'] = (
                (event["results_start"] - event["simulation_start"]) / 60)
        exec_time['step_mean_seconds'] = (
                (event["results_start"] - event["simulation_start"]) / num_steps)
        logger.info(f'Simulation main loop completed in {exec_time["main_loop_minutes"]:.3f} minutes.')
        logger.info(f'Average step length was {exec_time["step_mean_seconds"]:.3f} seconds.')

        sim.finalize()
        metrics = sim.report()
        event['end'] = time()
        end_snapshot = CounterSnapshot()

        do_sim_epilogue(start_snapshot, end_snapshot, event, exec_time, parameters)

        idx = pd.MultiIndex.from_tuples([(input_draw, random_seed)], names=['input_draw_number', 'random_seed'])
        output_metrics = pd.DataFrame(metrics, index=idx)
        for k, v in collapse_nested_dict(run_key):
            output_metrics[k] = v
        return output_metrics

    except Exception:
        logger.exception('Unhandled exception in worker')
        job = get_current_job()
        job.meta['root_exception'] = format_exc()
        job.save_meta()
        raise
    finally:
        logger.info('Exiting job: {}'.format((input_draw, random_seed, model_specification_file, branch_config)))


def do_sim_epilogue(start: CounterSnapshot, end: CounterSnapshot, event: dict, exec_time: dict, parameters: Mapping):
    exec_time['results_minutes'] = (event['end'] - event["results_start"]) / 60
    logger.info(f'Results reporting completed in {exec_time["results_minutes"]:.3f} minutes.')
    exec_time['total_minutes'] = (event['end'] - event["start"]) / 60
    logger.info(f'Total simulation run time {exec_time["total_minutes"]:.3f} minutes.')

    perf_log = logger.add(
        Path(os.environ['VIVARIUM_LOGGING_DIRECTORY']) / f'perf.{os.environ["JOB_ID"]}.{os.environ["SGE_TASK_ID"]}.log',
        level='DEBUG', serialize=True)
    logger.debug(json.dumps({
        "host": os.environ['HOSTNAME'].split('.')[0],
        "job_number": os.environ['JOB_ID'],
        "task_number": os.environ['SGE_TASK_ID'],
        "draw": parameters['input_draw'],
        "seed": parameters['random_seed'],
        "scenario": parameters['branch_configuration'],  # assumes leaves of branch config tree are scenarios
        "event": event,
        "exec_time": exec_time,
        "counters": (end - start).to_dict()
    }))
    logger.remove(perf_log)
