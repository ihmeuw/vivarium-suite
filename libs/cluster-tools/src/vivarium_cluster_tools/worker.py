import os
from time import time, sleep
import datetime
from traceback import format_exc
import random
import logging
from typing import Mapping

import pandas as pd
import numpy as np

import redis

from rq import get_current_job
from rq import Queue
from rq.job import JobStatus
from rq.connections import get_current_connection
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
        logging.info(f'Retrying job {job.id}')
    else:
        logging.error(f'Failing job {job.id}')
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
        logging_directory = os.environ['CEAM_LOGGING_DIRECTORY']
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            filename=os.path.join(logging_directory, str(worker_)+'.log'), level=logging.DEBUG)
        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).work(*args, **kwargs)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random()*60
                logging.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                sleep(backoff)
        logging.error(f"Ran out of retries. Killing worker")

    def main_work_horse(self, job, queue):
        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).main_work_horse(job, queue)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random()*60
                logging.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                sleep(backoff)
        logging.error(f"Ran out of retries. Killing work horse")

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
            logging.error(f'(work-horse terminated unexpectedly; waitpid returned {ret_val})')

            retry_handler(job, f"Work-horse process was terminated unexpectedly (waitpid returned {ret_val})",
                          None, None)

            self.acceptable_failure_count -= 1
            if self.acceptable_failure_count < 0:
                logging.error("This worker has had multiple jobs fail. That may mean the host is sick. Terminating "
                              "the worker to try and shift load onto other hosts.")
                raise StopRequested()


def job(parameters: Mapping, logging_directory: str, with_state_table: bool):
    input_draw = parameters['input_draw']
    random_seed = parameters['random_seed']
    component_config = parameters['simulation_configuration']
    branch_config = parameters['config']
    try:
        np.random.seed([input_draw, random_seed])
        logging.info('Starting job: {}'.format((input_draw, random_seed, component_config, branch_config)))
        worker_ = get_current_job().id

        from vivarium.framework.engine import build_simulation_configuration, run, setup_simulation
        from vivarium.framework.components import load_component_manager
        from vivarium.framework.util import collapse_nested_dict

        config = build_simulation_configuration(parameters)
        config.configuration.update(branch_config)
        config.configuration.run_configuration.update({'input_draw_number': input_draw,
                                                       'run_id': str(worker_) + '_' + str(time())})
        if branch_config is not None:
            run_key = dict(branch_config)
            run_key.update({'input_draw': input_draw, 'random_seed': random_seed})
            config.configuration.run_configuration.update({'run_key': run_key,
                                                           'results_directory': parameters['results_path']},
                                                          layer='override', source=str(worker_))
        logging.info('Simulation input config:')
        logging.info(str(config))

        component_manager = load_component_manager(config)
        simulation = setup_simulation(component_manager, config)
        metrics, final_state = run(simulation)
        idx = pd.MultiIndex.from_tuples([(input_draw, random_seed)],
                                        names=['input_draw_number', 'random_seed'])
        output = [pd.DataFrame(metrics, index=idx).to_json()]
        if with_state_table:
            final_state['input_draw_number'] = input_draw
            final_state['random_seed'] = random_seed
            if branch_config:
                for k, v in collapse_nested_dict(branch_config):
                    final_state[k] = v
            output.append(final_state.to_json())
        return output
    except Exception as e:
        logging.exception('Unhandled exception in worker')
        job = get_current_job()
        job.meta['root_exception'] = format_exc()
        job.save_meta()
        raise
    finally:
        logging.info('Exiting job: {}'.format((input_draw, random_seed, component_config, branch_config)))
