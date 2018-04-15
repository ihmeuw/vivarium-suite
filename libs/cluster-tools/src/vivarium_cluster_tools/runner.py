import os
from time import time, sleep

import numpy as np

from .utils import job_already_run
from .brokers import RedisBroker, ClusterBroker
from .monitor import ResultsManager, QueueManager

import logging
_log = logging.getLogger(__name__)

MEMORY_PER_CLUSTER_SLOT = 2.5
JOB_NAME = 'vivarium'
JOB = 'vivarium_cluster_tools.worker.job'
WORKER = 'vivarium_cluster_tools.worker.ResilientWorker'
RETRY_HANDLER = 'vivarium_cluster_tools.worker.retry_handler'


def parallel_run(workload_configuration, worker_configuration, output_configuration):
    _log.info(f'Jobs completed. Results written to: {output_configuration["results_root"]}')


def parallel_relaunch(results_root):
    _log.info(f'Jobs completed. Results written to: {output_configuration["results_root"]}')


def configure_logging(results_root, master_log_path, quiet):

    log_level = logging.ERROR if quiet else logging.DEBUG
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        filename=master_log_path, level=log_level)
    logging.getLogger().addHandler(logging.StreamHandler())
    worker_log_directory = os.path.join(results_root, 'worker_logs')
    os.makedirs(worker_log_directory, exist_ok=True)

    return worker_log_directory


class RunContext:

    def __init__(self, workload_configuration, worker_configuration, output_configuration):
        self._workload_configuration = workload_configuration
        self._worker_configuration = worker_configuration
        self._output_configuration = output_configuration

        self._cluster_broker = ClusterBroker(worker_configuration['project'],
                                             worker_configuration['peak_memory'],
                                             worker_configuration['max_retries'])
        self._redis_broker = RedisBroker()

        self.job_configs = None
        self.results_manager = ResultsManager(workload_configuration['simulation_configuration'],
                                              workload_configuration['keyspace'],
                                              output_configuration['results_root'],
                                              output_configuration['with_state_table'],
                                              output_configuration['existing_outputs'])
        self._number_already_completed = 0

    def initialize(self):
        self._worker_configuration['log_directory'] = configure_logging(self._output_configuration['results_root'],
                                                                        self._output_configuration['master_log_path'],
                                                                        self._output_configuration['quiet'])
        self.results_manager.dump_initialization_information()

        self.job_configs = self._build_job_config_list()
        if not self.job_configs:
            _log.info("Nothing to do")
            return False

        self._redis_broker.initialize()
        self._cluster_broker.initialize(self._worker_configuration['log_directory'], self._redis_broker.url)

    def run(self):
        self._cluster_broker.launch_array_job(len(self.job_configs))
        qm = QueueManager(self._redis_broker.hostname, self._redis_broker.port)

        start_time = time()
        heartbeat = 0
        while qm.jobs_in_progress:
            sleep(5)

            self.results_manager.process_jobs(self.job_configs, qm.finished_jobs)
            heartbeat = (heartbeat + 1) % 4
            self.report_status(qm.get_status(), start_time, heartbeat)

    def cleanup(self):
        pass

    def report_status(self, queue_status, start_time, heartbeat):
        waiting, running, failed, finished, workers = queue_status
        total = waiting + running + failed + finished
        percent_done = 100 * finished / total
        elapsed_time = time() - start_time

        if finished <= 100:
            remaining_time = '---'
        else:
            remaining_time = elapsed_time / (finished+failed) * (waiting+running)
            remaining_time = f'{remaining_time / 60:.1f} minutes'

        _log.info(f'{finished+self._number_already_completed} completed and {failed} failed of '
                  f'{total + self._number_already_completed} ({percent_done:.1f}% completed) '
                  f'/ Remaining time: {remaining_time} ' +
                  '.'*heartbeat + ' '*(4 - heartbeat) + f'    {workers} Workers           ')

    def _build_job_config_list(self):
        job_configs = []

        keyspace = self._workload_configuration['keyspace']
        existing_outputs = self._output_configuration['existing_outputs']

        for (input_draw, random_seed, branch_config) in keyspace:
            parameters = {
                'simulation_configuration': self._workload_configuration['simulation_configuration'],
                'config': branch_config,
                'input_draw': int(input_draw),
                'random_seed': int(random_seed),
                'results_path': self._output_configuration['results_root'],
            }

            if job_already_run(existing_outputs, input_draw, random_seed, branch_config):
                self._number_already_completed += 1
            else:
                job_configs.append(
                    (parameters,
                     self._worker_configuration['log_directory'],
                     self._worker_configuration['output_state_table'])
                )

        if self._number_already_completed:
            _log.info(f"{self._number_already_completed} of {len(keyspace)} jobs completed in previous run.")
            if self._number_already_completed != len(existing_outputs):
                _log.warning("There are jobs from the previous run which would not have been created "
                             "with the configuration saved with the run. That either means that code "
                             "has changed between then and now or that the outputs or configuration data "
                             "have been modified. This may represent a serious error so give it some thought.")

        np.random.shuffle(job_configs)
        return job_configs
