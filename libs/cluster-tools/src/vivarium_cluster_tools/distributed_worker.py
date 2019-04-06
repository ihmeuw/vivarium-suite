import datetime
import math
import os
from pathlib import Path
import random
from time import time, sleep
from traceback import format_exc
from typing import Mapping

from loguru import logger
import numpy as np
import pandas as pd
import redis
from rq import Queue
from rq import get_current_job
from rq.connections import get_current_connection
from rq.job import JobStatus
from rq.queue import get_failed_queue
from rq.worker import Worker, StopRequested


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
        failed_queue = get_failed_queue(get_current_connection(), job.__class__)
        failed_queue.quarantine(job, exc_info=exc_info)
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

        from vivarium.framework.engine import setup_simulation, run
        from vivarium.framework.configuration import build_model_specification
        from vivarium.framework.utilities import collapse_nested_dict

        model_specification = build_model_specification(model_specification_file)

        start_time = pd.Timestamp(**model_specification.configuration.time.start.to_dict())
        end_time = pd.Timestamp(**model_specification.configuration.time.end.to_dict())
        step_size = pd.Timedelta(days=model_specification.configuration.time.step_size)
        num_steps = int(math.ceil((end_time - start_time)/step_size))

        run_key = {'input_draw_number': input_draw, 'random_seed': random_seed}

        if branch_config is not None:
            model_specification.configuration.update(branch_config)
            run_key.update(dict(branch_config))

        model_specification.configuration.update({
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
            'input_data': {
                'input_draw_number': input_draw,
            }
        }, layer='override', source=str(worker_))

        logger.info('Simulation model specification:')
        logger.info(str(model_specification))

        start = time()
        logger.info('Beginning simulation setup.')
        simulation = setup_simulation(model_specification)
        logger.info(f'Simulation setup complete in {(time() - start)/60} minutes.')
        sim_start = time()
        logger.info('Starting main simulation loop.')
        metrics, final_state = run(simulation)
        end = time()
        logger.info(f'Simulation main loop completed in {(end - sim_start)/60} minutes.')
        logger.info(f'Average step length was {(end - sim_start)/num_steps} seconds.')
        logger.info(f'Total simulation run time {(end - start) / 60} minutes.')

        for k, v in collapse_nested_dict(run_key):
            metrics[k] = v
        output = [metrics.to_msgpack()]
        return output

    except Exception:
        logger.exception('Unhandled exception in worker')
        job = get_current_job()
        job.meta['root_exception'] = format_exc()
        job.save_meta()
        raise
    finally:
        logger.info('Exiting job: {}'.format((input_draw, random_seed, model_specification_file, branch_config)))
