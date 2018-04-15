import math
import os
import shutil
import socket
import subprocess
import tempfile
from time import time, sleep

import numpy as np
import pandas as pd
import redis
from rq import Queue, get_failed_queue
from rq.registry import StartedJobRegistry, FinishedJobRegistry

try:
    import drmaa
except RuntimeError:
    sge_cluster_name = os.environ['SGE_CLUSTER_NAME']
    os.environ['DRMAA_LIBRARY_PATH'] = f'/usr/local/UGE-{sge_cluster_name}/lib/lx-amd64/libdrmaa.so'
    import drmaa

from vivarium.framework.results_writer import ResultsWriter

from .utils import get_random_free_port, job_already_run, get_cluster_name
from .worker import ResilientWorker


import logging
_log = logging.getLogger(__name__)

MEMORY_PER_CLUSTER_SLOT = 2.5
JOB_NAME = 'vivarium'
JOB = 'vivarium_cluster_tools.worker.job'
WORKER = 'vivarium_cluster_tools.worker.ResilientWorker'
RETRY_HANDLER = 'vivarium_cluster_tools.worker.retry_handler'


def parallel_run(workload_configuration, worker_configuration, output_configuration):
    pass


def parallel_relaunch(results_root):
    pass


def configure_logging(results_root, master_log_path, quiet):

    log_level = logging.ERROR if quiet else logging.DEBUG
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        filename=master_log_path, level=log_level)
    logging.getLogger().addHandler(logging.StreamHandler())
    worker_log_directory = os.path.join(results_root, 'worker_logs')
    os.makedirs(worker_log_directory, exist_ok=True)

    return worker_log_directory


class QueueManager:

    def __init__(self, hostname, port):
        self.queue = Queue(JOB_NAME, connection=redis.Redis(hostname, port))
        self.finished_registry = FinishedJobRegistry(self.queue.name,
                                                     connection=self.queue.connection,
                                                     job_class=self.queue.job_class)
        self.wip_registry = StartedJobRegistry(self.queue.name,
                                               connection=self.queue.connection,
                                               job_class=self.queue.job_class)
        self._start_length = 0

    def launch_jobs(self, job_configs):
        self._start_length = len(job_configs)
        return {self.queue.enqueue(JOB,
                                   parameters=job_config[0],
                                   logging_directory=job_config[1],
                                   with_state_table=job_config[2],
                                   ttl=60*60*24*2,
                                   result_ttl=60*60,
                                   timeout='7d').id: job_config for job_config in job_configs}

    @property
    def jobs_in_progress(self):
        return len(self.queue) + len(self.wip_registry) > 0

    @property
    def finished_jobs(self):
        finished_jobs = self.finished_registry.get_job_ids()
        out = []
        for job_id in finished_jobs:
            job = self.queue.fetch_job(job_id)
            result = [pd.read_json(r) for r in job.result]
            out.append((job_id, result))
            self.finished_registry.remove(job)
        return out

    def get_status(self):
        waiting = len(set(self.queue.job_ids))
        running = len(self.wip_registry)
        failed = len(get_failed_queue(self.queue.connection))
        finished = self._start_length - (running + waiting + failed)
        workers = len(ResilientWorker.all(queue=self.queue))
        return waiting, running, failed, finished, workers


class ResultsManager:

    def __init__(self, simulation_configuration, keyspace, results_root, output_state_table, existing_results):
        self.simulation_configuration = simulation_configuration
        self.keyspace = keyspace
        self.results_writer = ResultsWriter(results_root)
        if output_state_table:
            self.results_writer.add_sub_directory('final_states', 'final_states')
        self._output_state_table = output_state_table
        self.metrics = existing_results if existing_results is not None else pd.DataFrame()
        self.final_states = []

    def process_jobs(self, job_configs, finished_jobs):
        dirty = False
        for job_id, result in finished_jobs:
            if self._output_state_table:
                metrics, state_table = result
                self.final_states.append((job_configs[job_id], state_table))
            else:
                metrics = result
            self.metrics.append(metrics)
            dirty = True

        if dirty:
            self.write_results(self.metrics, self.final_states)

    def write_results(self, metrics, final_states):
        self.results_writer.write_output(metrics, 'output.hdf')

        for config, state_table in final_states:
            run_config = config[0]
            branch_number = self.keyspace.get_branch_number(run_config['config'])
            input_draw = run_config['input_draw']
            random_seed = run_config['random_seed']
            self.results_writer.write_output(
                state_table, f"branch_{branch_number}_idraw_{input_draw}_seed_{random_seed}_.hdf",
                key='final_states'
            )

    def dump_initialization_information(self):
        self.results_writer.dump_simulation_configuration(self.simulation_configuration)
        self.keyspace.persist(self.results_writer)


class RedisBroker:
    def __init__(self):
        self.hostname = None
        self.port = None
        self._process = None

    def initialize(self):
        self.hostname = socket.gethostname()
        self.port = get_random_free_port()
        _log.info('Starting Redis Broker at %s:%s', self.hostname, self.port)

        try:
            self._process = subprocess.Popen(
                [f"echo -e 'timeout 2\nprotected-mode no\nport {self.port}' | redis-server -"],
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError or ImportError:
            raise OSError("Redis server not found. Try `conda install redis`")

    @property
    def url(self):
        if self.hostname is None or self.port is None:
            raise AttributeError('No url available.')

        return f'redis://{self.hostname}:{self.port}'

    def cleanup(self):
        self._process.kill()


class ClusterBroker:

    def __init__(self, project, peak_memory, max_retries):
        self._drmaa_session = drmaa.Session()
        project_flag = f' -P {project}' if get_cluster_name() == 'prod' else ''
        num_slots = int(math.ceil(peak_memory/MEMORY_PER_CLUSTER_SLOT))
        self._preamble = (f'-w n -q all.q -l m_mem_free={peak_memory}G '
                          + f'-N {JOB_NAME} -pe multislot {num_slots}{project_flag}')

        self._max_retries = max_retries
        self._template = None
        self._launcher_name = None
        self._array_job_id = None

    def initialize(self, worker_log_directory, database_broker_url):
        self._drmaa_session.initialize()
        self._launcher_name = self._create_launcher_script(worker_log_directory, database_broker_url)

        self._template = self._drmaa_session.createJobTemplate()
        self._template.workingDirectory = os.getcwd()
        self._template.remoteCommand = shutil.which('sh')
        self._template.args = [self._launcher_name]
        self._template.jobEnvironment = {
            'LC_ALL': 'en_US.UTF-8',
            'LANG': 'en_US.UTF-8',
            'SGE_CLUSTER_NAME': os.environ['SGE_CLUSTER_NAME'],
        }
        self._template.joinFiles = True
        self._template.nativeSpecification = self._preamble
        self._template.outputPath = ':/dev/null'

    def launch_array_job(self, number_of_jobs):
        job_ids = self._drmaa_session.runBulkJobs(self._template, 1, number_of_jobs, 1)
        self._array_job_id = job_ids[0].split('.')[0]

    @staticmethod
    def _create_launcher_script(worker_log_directory, database_broker_url):
        launcher = tempfile.NamedTemporaryFile(mode='w', dir='.', prefix='distributed_worker_launcher_',
                                               suffix='.sh', delete=False)
        launcher.write(
            f'''
            export CEAM_LOGGING_DIRECTORY={worker_log_directory}
            {shutil.which('rq')} worker --url {database_broker_url} --name ${{JOB_ID}}.${{SGE_TASK_ID}} --burst -w "{WORKER}" --exception-handler "{RETRY_HANDLER}" ceam

            ''')
        launcher.close()
        return launcher.name

    def cleanup(self):
        self._template.delete()
        os.remove(self._launcher_name)
        self._kill_jobs()
        self._drmaa_session.exit()

    def _kill_jobs(self):
        try:
            self._drmaa_session.control(self._array_job_id, drmaa.JobControlAction.TERMINATE)
        except drmaa.errors.InvalidJobException:
            # This is the case where all our workers have already shut down
            # on their own, which isn't actually an error.
            pass


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
