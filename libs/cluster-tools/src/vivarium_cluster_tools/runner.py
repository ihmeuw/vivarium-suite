import os
from collections import defaultdict
import atexit
import math
import tempfile
import shutil
import socket
import subprocess
from pathlib import Path
import random
from time import sleep, time
from types import SimpleNamespace

from loguru import logger
import numpy as np
import pandas as pd
import redis
from rq import Queue, get_failed_queue
from rq.registry import StartedJobRegistry, FinishedJobRegistry

try:
    import drmaa
except (RuntimeError, OSError):
    sge_cluster_name = os.environ['SGE_CLUSTER_NAME']
    if sge_cluster_name == "cluster":  # new cluster
        os.environ['DRMAA_LIBRARY_PATH'] = '/opt/sge/lib/lx-amd64/libdrmaa.so'
    else:  # old cluster - dev or prod
        os.environ['DRMAA_LIBRARY_PATH'] = f'/usr/local/UGE-{sge_cluster_name}/lib/lx-amd64/libdrmaa.so'
    import drmaa

from vivarium.framework.configuration import build_model_specification
from vivarium.framework.results_writer import ResultsWriter
from vivarium.framework.utilities import collapse_nested_dict
from vivarium_public_health.dataset_manager import Artifact, parse_artifact_path_config

from vivarium_cluster_tools.branches import Keyspace
from vivarium_cluster_tools.distributed_worker import ResilientWorker
from vivarium_cluster_tools import utilities, globals as vtc_globals


def init_job_template(jt, peak_memory, sge_log_directory, worker_log_directory,
                      project, job_name, worker_settings_file):
    launcher = tempfile.NamedTemporaryFile(mode='w', dir='.', prefix='vivarium_cluster_tools_launcher_',
                                           suffix='.sh', delete=False)
    atexit.register(lambda: os.remove(launcher.name))
    output_dir = str(worker_settings_file.resolve().parent)
    launcher.write(f'''
    export VIVARIUM_LOGGING_DIRECTORY={worker_log_directory}
    export PYTHONPATH={output_dir}:$PYTHONPATH
    
    {shutil.which('rq')} worker -c {worker_settings_file.stem()} --name ${{JOB_ID}}.${{SGE_TASK_ID}} --burst -w "vivarium_cluster_tools.distributed_worker.ResilientWorker" --exception-handler "vivarium_cluster_tools.distributed_worker.retry_handler" vivarium

    ''')
    launcher.close()

    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which('sh')
    jt.args = [launcher.name]
    jt.outputPath = f":{sge_log_directory}"
    jt.errorPath = f":{sge_log_directory}"
    sge_cluster = utilities.get_cluster_name()
    jt.jobEnvironment = {
        'LC_ALL': 'en_US.UTF-8',
        'LANG': 'en_US.UTF-8',
        'SGE_CLUSTER_NAME': sge_cluster,
    }
    jt.joinFiles = True
    jt.nativeSpecification = utilities.get_uge_specification(peak_memory, project, job_name)
    return jt


def get_random_free_port():
    # NOTE: this implementation is vulnerable to rare race conditions where some other process gets the same
    # port after we free our socket but before we use the port number we got. Should be so rare in practice
    # that it doesn't matter.
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def launch_redis(port):
    try:
        # inline config for redis server.
        redis_process = subprocess.Popen(["redis-server", "--port", f"{port}",
                                          "--timeout", "2",
                                          "--protected-mode", "no"], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise OSError("In order for redis to launch you need both the redis client and the python bindings. "
                      "You seem to be missing the redis client.  Do 'conda install redis' and try again. If "
                      "failures continue you may need to download redis yourself, make it and add it to PATH.")
    atexit.register(redis_process.kill)
    return redis_process


def launch_redis_processes(num_processes):
    hostname = socket.getfqdn()
    redis_ports = []
    for i in range(num_processes):
        port = get_random_free_port()
        logger.info(f'Starting Redis Broker at {hostname}:{port}')
        launch_redis(port)
        redis_ports.append((hostname, port))

    redis_urls = [f'redis://{hostname}:{port}' for hostname, port in redis_ports]
    worker_config = f"""
    import random

    redis_urls = {redis_urls}

    REDIS_URL = random.choice(redis_urls)
    """
    return worker_config, redis_ports


def start_cluster(drmaa_session, num_workers, peak_memory, sge_log_directory, worker_log_directory,
                  project, worker_settings_file, redis_ports, job_name="ceam"):
    s = drmaa_session
    jt = init_job_template(s.createJobTemplate(), peak_memory, sge_log_directory,
                           worker_log_directory, project, job_name, worker_settings_file)
    if num_workers:
        job_ids = s.runBulkJobs(jt, 1, num_workers, 1)
        array_job_id = job_ids[0].split('.')[0]

        def kill_jobs():
            if "drmaa" not in dir():
                # FIXME: The global drmaa should be available here.
                # This is maybe a holdover from old code?
                # Maybe something to do with atexit?
                import drmaa
            try:
                s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
            # FIXME: Hack around issue where drmaa.errors sometimes doesn't
            # exist.
            except Exception as e:
                if 'There are no jobs registered' in str(e):
                    # This is the case where all our workers have already shut down
                    # on their own, which isn't actually an error.
                    pass
                elif 'Discontinued delete' in str(e):
                    # sge has already cleaned up some of the jobs.
                    pass
                else:
                    raise

        atexit.register(kill_jobs)

    return [Queue('vivarium', connection=redis.Redis(hostname, port)) for hostname, port in redis_ports]


class RunContext:
    def __init__(self, arguments):
        # TODO This constructor has side effects (it creates directories under some circumstances) which is weird.
        # It should probably be split into two phases with the side effects in the second phase.

        self.cluster_project = arguments.project
        self.peak_memory = arguments.peak_memory
        self.number_already_completed = 0
        self.results_writer = ResultsWriter(arguments.result_directory)
        self.job_name = Path(arguments.result_directory).resolve().parts[-2]  # The model specification name.

        if arguments.restart:
            self.keyspace = Keyspace.from_previous_run(self.results_writer.results_root)
            self.existing_outputs = pd.read_hdf(os.path.join(self.results_writer.results_root, 'output.hdf'))
        else:
            model_specification = build_model_specification(arguments.model_specification_file)

            self.keyspace = Keyspace.from_branch_configuration(arguments.num_input_draws, arguments.num_random_seeds,
                                                               arguments.branch_configuration_file)

            if "input_data.artifact_path" in self.keyspace.get_data():
                raise ValueError("An artifact path can only be supplied in the model specification file, "
                                 "not the branches configuration.")

            if "artifact_path" in model_specification.configuration.input_data:
                artifact_path = parse_artifact_path_config(model_specification.configuration)
                if arguments.copy_data:
                    self.copy_artifact(artifact_path, self.keyspace.get_data().get('input_data.location'))
                    artifact_path = os.path.join(self.results_writer.results_root, "data_artifact.hdf")
                model_specification.configuration.input_data.update(
                    {"artifact_path": artifact_path},
                    source=__file__)

            model_specification_path = os.path.join(self.results_writer.results_root, 'model_specification.yaml')
            shutil.copy(arguments.model_specification_file, model_specification_path)

            self.existing_outputs = None

            # Log some basic stuff about the simulation to be run.
            self.keyspace.persist(self.results_writer)
        self.model_specification = os.path.join(self.results_writer.results_root, 'model_specification.yaml')

        self.sge_log_directory = os.path.join(self.results_writer.results_root, "sge_logs")
        os.makedirs(self.sge_log_directory, exist_ok=True)
        self.worker_log_directory = os.path.join(self.results_writer.results_root, 'worker_logs')
        os.makedirs(self.worker_log_directory, exist_ok=True)

    def copy_artifact(self, artifact_path, locations):
        full_art = Artifact(artifact_path)

        artifact_locs = set(full_art.load('metadata.locations'))
        if not set(locations).issubset(artifact_locs):
            raise ValueError(f'You have specified locations {", ".join(set(locations) - artifact_locs)} in your '
                             f'branches/model specifications that are not present in the specified artifact.')

        # very slow to copy just relevant locs so copy the whole artifact
        self.results_writer.copy_file(artifact_path, "data_artifact.hdf")


def build_job_list(ctx):
    jobs = []
    number_already_completed = 0

    for (input_draw, random_seed, branch_config) in ctx.keyspace:
        parameters = {'model_specification_file': ctx.model_specification,
                      'branch_configuration': branch_config,
                      'input_draw': int(input_draw),
                      'random_seed': int(random_seed),
                      'results_path': ctx.results_writer.results_root,
                      }

        do_schedule = True
        if ctx.existing_outputs is not None:
            mask = ctx.existing_outputs.input_draw == int(input_draw)
            mask &= ctx.existing_outputs.random_seed == int(random_seed)
            for k, v in collapse_nested_dict(branch_config):
                if isinstance(v, float):
                    mask &= np.isclose(ctx.existing_outputs[k], v)
                else:
                    mask &= ctx.existing_outputs[k] == v
            do_schedule = not np.any(mask)

        if do_schedule:
            jobs.append(parameters)
        else:
            number_already_completed += 1

    if number_already_completed:
        logger.info(f"{number_already_completed} of {len(ctx.keyspace)} jobs completed in previous run.")
        if number_already_completed != len(ctx.existing_outputs):
            logger.warning("There are jobs from the previous run which would not have been created "
                           "with the configuration saved with the run. That either means that code "
                           "has changed between then and now or that the outputs or configuration data "
                           "have been modified. This may represent a serious error so give it some thought.")

    ctx.number_already_completed = number_already_completed
    np.random.shuffle(jobs)
    return jobs


class RegistryManager:

    retries_before_fail = 10
    backoff = 30

    def __init__(self, queues, already_completed):
        logger.info('Building registries.')

        self._queues = {}
        self._wip = {}
        self._finished = {}

        self._status = {}
        self._retries = {}

        self._failed = []
        self._completed = []

        self._previously_completed = already_completed

        for key, queue in enumerate(queues):
            self._queues[key] = queue
            self._wip[key] = StartedJobRegistry(queue.name, connection=queue.connection, job_class=queue.job_class)
            self._finished[key] = FinishedJobRegistry(queue.name, connection=queue.connection,
                                                      job_class=queue.job_class)
            self._status[key] = {'total': 0, 'pending': 0, 'running': 0,
                                 'failed': 0, 'finished': 0, 'done': 0., 'workers': 0}
            self.update_and_report(key)
            self._retries[key] = RegistryManager.retries_before_fail

    def jobs_to_finish(self):
        status = self.get_status()
        return status['pending'] + status['running'] > 0

    def __iter__(self):
        keys = list(self._queues.keys())
        for key in keys:
            yield key

    def get_finished_jobs(self, registry_key):
        logger.info(f'Retrieving finished jobs for queue {registry_key}')
        finished_jobs = None
        while self._retries[registry_key] > 0:
            try:
                finished_jobs = self._finished[registry_key].get_job_ids()
                break
            except redis.exceptions.ConnectionError:
                self.sleep_on_it(registry_key)
            except KeyError:
                logger.exception(f'Queue {registry_key} already complete or failed.')
                break

        if finished_jobs is None:
            self.abandon_queue(registry_key)
            finished_jobs = []

        return finished_jobs

    def get_batch_results(self, registry_key, finished_jobs, batch_size=10):
        for i, finished_jobs_chunk in enumerate(chunks(finished_jobs, batch_size)):
            chunk_results = []
            for job_id in finished_jobs_chunk:
                result = self.get_result(registry_key, job_id)
                if result is not None:
                    chunk_results.append(result)
            yield chunk_results

    def get_job(self, registry_key, job_id):
        logger.info(f'Fetching job {job_id} from queue {registry_key}.')

        job = None
        while self._retries[registry_key] > 0:
            try:
                start = time()
                job = self._queues[registry_key].fetch_job(job_id)
                end = time()
                logger.info(f'Fetched job {job_id} from queue {registry_key} in {end - start:.2f}s')
                break
            except redis.exceptions.ConnectionError:
                self.sleep_on_it(registry_key)
            except KeyError:
                logger.exception(f'Queue {registry_key} already complete or failed.')
                break

        if job is None:
            self.abandon_queue(registry_key)

        return job

    def get_result(self, registry_key, job_id):
        job = self.get_job(registry_key, job_id)
        result = None
        if job is not None:
            start = time()
            result = pd.read_msgpack(job.result[0])
            end = time()
            logger.info(f'Read {job_id} from msgpack from queue {registry_key} in {end - start:.2f}s.')
            self._finished[registry_key].remove(job)
            self._status[registry_key]['finished'] += 1
        return result

    def sleep_on_it(self, registry_key):
        backoff = RegistryManager.backoff * random.random()
        logger.warning(f'Failed to connect to redis for queue {registry_key}.  Retrying in {backoff}s.')
        self._retries[registry_key] -= 1
        sleep(backoff)

    def abandon_queue(self, registry_key):
        logger.error(f'Lost connection to redis for queue {registry_key}.  Abandoning redis instance.')
        self._status[registry_key]['failed'] += self._status[registry_key]['pending'] + self._status[registry_key]['running']
        self._status[registry_key]['pending'] = 0
        self._status[registry_key]['running'] = 0
        # TODO: Probably should cleanup, but not sure how yet.
        try:
            self._failed.append(
                (self._queues.pop(registry_key), self._finished.pop(registry_key), self._failed.pop(registry_key))
            )
        except (IndexError, KeyError):
            logger.exception("Guess redis cleans up after itself?")

    def complete_queue(self, registry_key):
        logger.info(f'All jobs on queue {registry_key} complete.')
        # TODO: Probably should cleanup, but not sure how yet.
        try:
            self._completed.append(
                (self._queues.pop(registry_key), self._finished.pop(registry_key), self._failed.pop(registry_key))
            )
        except (IndexError, KeyError):
            logger.exception("Guess redis cleans up after itself?")

    def update_and_report(self, registry_key=None):
        if registry_key is not None:
            queue_name = registry_key
            status = self._status[registry_key]
        else:
            queue_name = 'all'
            status = self.get_status()

        template = (f"Queue {queue_name} - Total jobs: {{total}}, % Done: {{done}}%\n"
                    f"Pending: {{pending}}, Running: {{running}}, Failed: {{failed}}, Finished: {{finished}}\n"
                    f"Workers: {{workers}}.")

        self.update(registry_key)
        logger.info(template.format(**status))

    def update(self, registry_key=None):
        # FIXME: This is slightly unsafe, but I've never seen errors here and I'm sleepy.
        if registry_key is not None:
            # TODO: Sometimes there are duplicate job_ids, why?
            q_pending = len(set(self._queues[registry_key].job_ids))
            q_running = len(self._wip[registry_key])
            q_to_write = len(self._finished[registry_key])
            q_failed = len(get_failed_queue(self._queues[registry_key].connection))
            q_finished = self._status[registry_key]['finished']
            q_total = q_pending + q_running + q_failed + q_to_write + q_finished
            q_workers = len(ResilientWorker.all(queue=self._queues[registry_key]))

            self._status[registry_key]['pending'] = q_pending
            self._status[registry_key]['running'] = q_running + q_to_write
            self._status[registry_key]['failed'] = q_failed
            self._status[registry_key]['finished'] = q_finished
            self._status[registry_key]['total'] = q_total
            self._status[registry_key]['workers'] = q_workers
            self._status[registry_key]['done'] = q_finished / q_total * 100

            if sum([q_pending, q_running, q_to_write]) == 0:
                self.complete_queue(registry_key)
        else:
            for registry_key in self._queues:
                self.update(registry_key)

    def get_status(self):
        status = defaultdict(int)
        for queue_status in self._status.values():
            for k, v in queue_status.items():
                status[k] += v
        status['done'] = status['finished'] / status['total'] * 100
        status['finished'] += self._previously_completed
        return status


def process_job_results(queues, ctx):
    start_time = time()

    if ctx.existing_outputs is not None:
        results = ctx.existing_outputs
    else:
        results = pd.DataFrame()

    registry_manager = RegistryManager(queues, len(results))

    logger.info('Entering main processing loop.')
    while registry_manager.jobs_to_finish():
        sleep(5)
        for registry_key in registry_manager:
            logger.info(f'Checking queue {registry_key}')
            finished_jobs = registry_manager.get_finished_jobs(registry_key)

            written_count = 0
            for result_batch in registry_manager.get_batch_results(registry_key, finished_jobs):
                if result_batch:  # Redis might have fallen over, in which case this is empty.
                    start = time()
                    results = pd.concat([results] + result_batch, axis=0)
                    end = time()
                    logger.info(f"Concatenated {len(result_batch)} results in {end - start:.2f}s.")

                    start = end
                    ctx.results_writer.write_output(results, 'output.hdf')
                    end = time()
                    logger.info(f"Updated output.hdf in {end - start:.4f}s.")

                    written_count += len(result_batch)
                    logger.info(f"Writing {len(finished_jobs)} jobs to output.hdf. "
                                f"{written_count / len(finished_jobs) * 100:.1f}% done.")

            registry_manager.update_and_report(registry_key)

        registry_manager.update_and_report()
        logger.info(f'Elapsed time: {(time() - start_time)/60:.1f} minutes.')


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def check_user_sge_config():
    """Warn if a user has set their stdout and stderr output paths
    in a home directory config file. This overrides settings from py-drmaa."""

    sge_config = Path().home() / ".sge_request"

    if sge_config.exists():
        with sge_config.open('r') as f:
            for line in f:
                line = line.strip()
                if (('-o ' in line) or ('-e' in line)) and not line.startswith("#"):
                    logger.warning("You may have settings in your .sge_request file "
                                   "that could overwrite the log location set by this script. "
                                   f"Your .sge_request file is here: {sge_config}.  Look for "
                                   "-o and -e and comment those lines to recieve logs side-by-side"
                                   "with the worker logs.")


def main(model_specification_file, branch_configuration_file, result_directory, project, peak_memory, redis_processes,
         copy_data=False, num_input_draws=None, num_random_seeds=None, restart=False):

    output_directory = utilities.get_output_directory(model_specification_file, result_directory, restart)
    utilities.configure_master_process_logging_to_file(output_directory)

    arguments = SimpleNamespace(model_specification_file=model_specification_file,
                                branch_configuration_file=branch_configuration_file,
                                result_directory=output_directory,
                                project=project,
                                peak_memory=peak_memory,
                                copy_data=copy_data,
                                num_input_draws=num_input_draws,
                                num_random_seeds=num_random_seeds,
                                restart=restart)
    ctx = RunContext(arguments)
    check_user_sge_config()
    jobs = build_job_list(ctx)

    if len(jobs) == 0:
        logger.info("Nothing to do")
        return

    logger.info('Starting jobs. Results will be written to: {}'.format(ctx.results_writer.results_root))

    if redis_processes == -1:
        redis_processes = int(math.ceil(len(jobs) / vtc_globals.DEFAULT_JOBS_PER_REDIS_INSTANCE))

    worker_template, redis_ports = launch_redis_processes(redis_processes)
    worker_file = output_directory / 'settings.py'
    with worker_file.open('r') as f:
        f.write(worker_template)

    workers_per_queue = int(math.ceil(len(jobs) / redis_processes))
    chunked_jobs = chunks(jobs, workers_per_queue)

    drmaa_session = drmaa.Session()
    drmaa_session.initialize()

    queues = start_cluster(drmaa_session, len(jobs), ctx.peak_memory, ctx.sge_log_directory, ctx.worker_log_directory,
                           project, worker_file, redis_ports, ctx.job_name)

    for i, queue in enumerate(queues):
        logger.info(f'Enqueuing jobs in queue {i}')
        queue_jobs = next(chunked_jobs)
        for job in queue_jobs:
            # TODO: might be nice to have tighter ttls but it's hard to predict how long our jobs
            # will take from model to model and the entire system is short lived anyway
            queue.enqueue('vivarium_cluster_tools.distributed_worker.worker',
                          parameters=job,
                          ttl=60 * 60 * 24 * 2,
                          result_ttl=60 * 60,
                          timeout='7d')

    process_job_results(queues, ctx)

    logger.info('Jobs completed. Results written to: {}'.format(ctx.results_writer.results_root))
