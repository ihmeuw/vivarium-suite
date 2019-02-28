import os
import atexit
import tempfile
import shutil
import socket
import subprocess
import math
from typing import Dict
from pathlib import Path
from time import sleep, time
from getpass import getuser
from types import SimpleNamespace

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
from vivarium.framework.results_writer import get_results_writer, ResultsWriter
from vivarium.framework.utilities import collapse_nested_dict
from vivarium_cluster_tools.branches import Keyspace
from vivarium_cluster_tools.distributed_worker import ResilientWorker
from vivarium_public_health.dataset_manager import Artifact, parse_artifact_path_config

import logging

_log = logging.getLogger(__name__)


def uge_specification(peak_memory, project, job_name):
    try:
        if os.environ['SGE_CLUSTER_NAME'] == "dev":
            project = None
        else:  # prod or new cluster
            assert project in ['proj_cost_effect', 'proj_cost_effect_diarrhea',
                               'proj_cost_effect_dcpn', 'proj_cost_effect_conic',
                               'proj_csu'], 'Script only for use by sanofi and cost effectiveness teams.'
            project = project
    except KeyError:
        raise Exception('This script must be run on the IHME cluster')

    preamble = '-w n -q all.q -l m_mem_free={}G -N {}'.format(peak_memory, job_name)

    if os.environ['SGE_CLUSTER_NAME'] == "cluster":
        preamble += " -l fthread=2"
        preamble += " -l archive=TRUE"
    else:
        # Calculate slot count based on expected peak memory usage and 2g per slot
        num_slots = int(math.ceil(peak_memory / 2.5))
        preamble += ' -pe multi_slot {}'.format(num_slots)

    if project:
        preamble += ' -P {}'.format(project)

    return preamble


def init_job_template(jt, peak_memory, broker_url, sge_log_directory, worker_log_directory, project, job_name):
    launcher = tempfile.NamedTemporaryFile(mode='w', dir='.', prefix='celery_worker_launcher_',
                                           suffix='.sh', delete=False)
    atexit.register(lambda: os.remove(launcher.name))
    launcher.write(f'''
    export CEAM_LOGGING_DIRECTORY={worker_log_directory}
    {shutil.which(
        'rq')} worker --url {broker_url} --name ${{JOB_ID}}.${{SGE_TASK_ID}} --burst -w "vivarium_cluster_tools.distributed_worker.ResilientWorker" --exception-handler "vivarium_cluster_tools.distributed_worker.retry_handler" ceam

    ''')
    launcher.close()

    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which('sh')
    jt.args = [launcher.name]
    jt.outputPath = f":{sge_log_directory}"
    jt.errorPath = f":{sge_log_directory}"
    sge_cluster = os.environ['SGE_CLUSTER_NAME']
    jt.jobEnvironment = {
        'LC_ALL': 'en_US.UTF-8',
        'LANG': 'en_US.UTF-8',
        'SGE_CLUSTER_NAME': sge_cluster,
    }
    jt.joinFiles = True
    jt.nativeSpecification = uge_specification(peak_memory, project, job_name)
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


def start_cluster(drmaa_session, num_workers, peak_memory, sge_log_directory, worker_log_directory, project,
                  job_name="ceam"):
    hostname = socket.getfqdn()
    port = get_random_free_port()
    _log.info('Starting Redis Broker at %s:%s', hostname, port)
    broker_process = launch_redis(port)
    broker_url = 'redis://{}:{}'.format(hostname, port)

    s = drmaa_session
    jt = init_job_template(s.createJobTemplate(), peak_memory, broker_url, sge_log_directory,
                           worker_log_directory, project, job_name)
    if num_workers:
        job_ids = s.runBulkJobs(jt, 1, num_workers, 1)
        array_job_id = job_ids[0].split('.')[0]

        def kill_jobs():
            if "drmaa" not in dir():
                import drmaa
            try:
                s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
            except drmaa.errors.InvalidJobException:
                # This is the case where all our workers have already shut down
                # on their own, which isn't actually an error.
                pass

        atexit.register(kill_jobs)

    queue = Queue('ceam', connection=redis.Redis(hostname, port))
    return queue


def configure_sge_logging(results_root, quiet):
    if quiet:
        sge_log_directory = "/dev/null"
    else:
        sge_log_directory = os.path.join(results_root, "sge_logs")
        os.makedirs(sge_log_directory, exist_ok=True)

    return sge_log_directory


def configure_worker_logging(results_root, log_path, quiet):
    if '{results_root}' in log_path:
        master_log_file = log_path.format(results_root=results_root)
    else:
        master_log_file = log_path  # Could be more defensive
    log_level = logging.ERROR if quiet else logging.DEBUG
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        filename=master_log_file, level=log_level)
    logging.getLogger().addHandler(logging.StreamHandler())

    worker_log_directory = os.path.join(results_root, 'worker_logs')
    os.makedirs(worker_log_directory, exist_ok=True)

    return worker_log_directory


def get_default_output_directory(run_type):
    """Returns the default output directory for the run type specified.

    Parameters
    ----------
    run_type : {'validation', 'final'}

    Returns
    -------
    str
        The default output directory.
    """
    if run_type == 'validation':
        username = getuser()
        return f'/share/scratch/users/{username}/vivarium_results'
    elif run_type == 'final':
        return '/home/j/Project/Cost_Effectiveness/CEAM/Results'
    else:
        raise ValueError(f'Run type must be either "validation" or "final".  You specified {run_type}')


def validate_arguments(arguments: Dict) -> Dict:
    if arguments.restart and not arguments.result_directory:
        raise ValueError(f"'result_directory' Must be specified with 'restart'")
    if not arguments.restart and not arguments.model_specification_file:
        raise ValueError(f"simulation configuration file must be supplied to run distrbuted runner")
    return arguments


class RunContext:
    def __init__(self, arguments):
        # TODO This constructor has side effects (it creates directories under some circumstances) which is weird.
        # It should probably be split into two phases with the side effects in the second phase.

        self.cluster_project = arguments.project
        self.peak_memory = arguments.peak_memory
        self.number_already_completed = 0
      
        if arguments.result_directory is None:
            results_directory = get_default_output_directory(arguments.run_type)
        else:
            results_directory = arguments.result_directory

        if arguments.restart:
            self.results_writer = ResultsWriter(results_directory)
            self.keyspace = Keyspace.from_previous_run(self.results_writer.results_root)
            self.existing_outputs = pd.read_hdf(os.path.join(self.results_writer.results_root, 'output.hdf'))
        else:
            self.results_writer = get_results_writer(results_directory, arguments.model_specification_file)
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
        self.sge_log_directory = configure_sge_logging(self.results_writer.results_root, arguments.quiet)
        self.worker_log_directory = configure_worker_logging(self.results_writer.results_root, arguments.log, arguments.quiet)

        # Each state table is 50-200 MB, so we only write the final state table for small jobs.
        max_jobs = 100
        if arguments.run_type == 'validation' and len(self.keyspace) <= max_jobs:
            self.with_state_table = True
            self.results_writer.add_sub_directory('final_states', 'final_states')
        else:
            self.with_state_table = False
            if arguments.run_type == 'validation':
                _log.warning(f"You're launching {len(self.keyspace)} jobs.  This is too many to write final "
                             f"state tables. If you need information from the state table, reduce the "
                             f"number of draws or branches.")

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
                      'with_state_table': ctx.with_state_table,
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
        _log.info(f"{number_already_completed} of {len(ctx.keyspace)} jobs completed in previous run.")
        if number_already_completed != len(ctx.existing_outputs):
            _log.warning("There are jobs from the previous run which would not have been created "
                         "with the configuration saved with the run. That either means that code "
                         "has changed between then and now or that the outputs or configuration data "
                         "have been modified. This may represent a serious error so give it some thought.")

    ctx.number_already_completed = number_already_completed
    np.random.shuffle(jobs)
    return jobs


def process_job_results(job_arguments, queue, ctx):
    start_time = time()
    collected = set()

    if ctx.existing_outputs is not None:
        results = ctx.existing_outputs
    else:
        results = pd.DataFrame()

    finished_registry = FinishedJobRegistry(queue.name, connection=queue.connection, job_class=queue.job_class)
    wip_registry = StartedJobRegistry(queue.name, connection=queue.connection, job_class=queue.job_class)

    heartbeat = 0
    while (len(queue) + len(wip_registry)) > 0:
        sleep(5)
        finished_jobs = finished_registry.get_job_ids()

        chunk_size=10
        for i, finished_jobs_chunk in enumerate(chunks(finished_jobs, chunk_size)):
            final_states = {}
            dirty = False
            for job_id in finished_jobs_chunk:
                start = time()
                job = queue.fetch_job(job_id)
                end = time()
                _log.info(f'\tfetched job in {end - start:.4f}')
                start = end
                if ctx.with_state_table:
                    result, final_state = [pd.read_msgpack(r) for r in job.result]
                    final_states[job.id] = final_state
                else:
                    result = pd.read_msgpack(job.result[0])
                    end = time()
                    _log.info(f'\tread from msgpack in {end - start:.4f}')
                results = results.append(result)
                dirty = True
                finished_registry.remove(job)

            if dirty:
                _log.info(f"\tWriting {len(finished_jobs)} jobs to output.hdf. "
                        f"{(i * chunk_size + len(finished_jobs_chunk)) / len(finished_jobs) * 100:.1f}% done.")
                ctx.results_writer.write_output(results, 'output.hdf')
                for job_id, f in final_states.items():
                    run_config = job_arguments[job_id]
                    branch_number = ctx.keyspace.get_branch_number(run_config['branch_configuration'])
                    input_draw = run_config['input_draw']
                    random_seed = run_config['random_seed']
                    ctx.results_writer.write_output(
                        f, f"branch_{branch_number}_idraw_{input_draw}_seed_{random_seed}_.hdf",
                        key='final_states'
                    )

        fail_queue = get_failed_queue(queue.connection)

        # TODO: Sometimes there are duplicate job_ids, why?
        waiting_jobs = len(set(queue.job_ids))
        running_jobs = len(wip_registry)
        rjs = wip_registry.get_job_ids()
        finished_jobs = len(finished_registry) + len(results) - ctx.number_already_completed
        failed_jobs = len(fail_queue)

        percent_complete = 100 * finished_jobs / (waiting_jobs + running_jobs + finished_jobs + failed_jobs)
        elapsed_time = time() - start_time

        if finished_jobs <= 100:
            remaining_time = '---'
        else:
            remaining_time = elapsed_time / (finished_jobs + failed_jobs) * (waiting_jobs + running_jobs)
            remaining_time = '{:.1f} minutes'.format(remaining_time / 60)

        # display run info, and a "heartbeat"
        heartbeat = (heartbeat + 1) % 4
        worker_count = len(ResilientWorker.all(queue=queue))
        _log.info(f'{finished_jobs + ctx.number_already_completed} completed and {failed_jobs} '
                  f'failed of {waiting_jobs + running_jobs + finished_jobs + failed_jobs + ctx.number_already_completed} '
                  f'({percent_complete:.1f}% completed)' +
                  f'/ Remaining time: {remaining_time} ' +
                  '.' * heartbeat + ' ' * (4 - heartbeat) +
                  f'    {worker_count} Workers' +
                  '           ')


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def check_user_sge_config():
    """Warn if a user has set their stdout and stderr output paths
    in a home directory config file. This overrides settings from py-drmaa."""

    sge_config = Path().home() / ".sge_request"

    if sge_config.exists():
        with open(sge_config, "r") as f:
            for line in f:
                line = line.strip()
                if (('-o ' in line) or ('-e' in line)) and not line.startswith("#"):
                    _log.warn("You may have settings in your .sge_request file "
                              "that could overwrite the log location set by this script. "
                              f"Your .sge_request file is here: {sge_config}.  Look for "
                              "-o and -e and comment those lines to recieve logs side-by-side"
                              "with the worker logs.")


def main(model_specification_file, branch_configuration_file, result_directory, project, peak_memory,
         copy_data=False, num_input_draws=None, num_random_seeds=None, num_workers=None, max_retries=1,
         run_type='validation', quiet=False, log='{results_root}/master.log', restart=False):
    
    arguments = SimpleNamespace(**locals())
    arguments = validate_arguments(arguments)
    ctx = RunContext(arguments)
    check_user_sge_config()
    jobs = build_job_list(ctx)

    if len(jobs) == 0:
        _log.info("Nothing to do")
        return

    _log.info('Starting jobs. Results will be written to: {}'.format(ctx.results_writer.results_root))

    num_workers = len(jobs)
    drmaa_session = drmaa.Session()
    drmaa_session.initialize()

    queue = start_cluster(drmaa_session, num_workers, ctx.peak_memory, ctx.sge_log_directory,
                          ctx.worker_log_directory, ctx.cluster_project)

    # TODO: might be nice to have tighter ttls but it's hard to predict how long our jobs
    # will take from model to model and the entire system is short lived anyway
    job_arguments = {queue.enqueue('vivarium_cluster_tools.distributed_worker.worker',
                                   parameters=job,
                                   ttl=60 * 60 * 24 * 2,
                                   result_ttl=60 * 60,
                                   timeout='7d').id: job for job in jobs}

    process_job_results(job_arguments, queue, ctx)

    _log.info('Jobs completed. Results written to: {}'.format(ctx.results_writer.results_root))


