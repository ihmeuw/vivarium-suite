import os
import atexit
import tempfile
import shutil
import socket
import argparse
import subprocess
import math
from time import sleep, time
from getpass import getuser

import numpy as np
import pandas as pd
import redis
from rq import Queue, get_failed_queue
from rq.registry import StartedJobRegistry, FinishedJobRegistry
import yaml

try:
    import drmaa
except RuntimeError:
    sge_cluster_name = os.environ['SGE_CLUSTER_NAME']
    os.environ['DRMAA_LIBRARY_PATH'] = f'/usr/local/UGE-{sge_cluster_name}/lib/lx-amd64/libdrmaa.so'
    import drmaa


from vivarium.framework.results_writer import get_results_writer, get_results_writer_for_restart
from vivarium.framework.util import collapse_nested_dict
from ceam_experiments.branches import Keyspace
from ceam_experiments.distributed_worker import ResilientWorker

import logging
_log = logging.getLogger(__name__)


def uge_specification(peak_memory, project, job_name='ceam'):
    # Are we on dev or prod?
    result = str(subprocess.check_output(['qconf', '-ss']))
    if 'cluster-dev.ihme.washington.edu' in result:
        project = None
    elif 'cluster-prod.ihme.washington.edu' in result:
        assert project in ['proj_cost_effect', 'proj_csu'], 'Script only for use by sanofi and cost effectiveness teams.'
        project = project
    else:
        raise Exception('This script must be run on the IHME cluster')

    preamble = '-w n -q all.q -l m_mem_free={}G -N {}'.format(peak_memory, job_name)

    # Calculate slot count based on expected peak memory usage and 2g per slot
    num_slots = int(math.ceil(peak_memory/2.5))
    preamble += ' -pe multi_slot {}'.format(num_slots)

    if project:
        preamble += ' -P {}'.format(project)

    return preamble


def init_job_template(jt, peak_memory, broker_url, worker_log_directory, project):
    launcher = tempfile.NamedTemporaryFile(mode='w', dir='.', prefix='celery_worker_launcher_',
                                           suffix='.sh', delete=False)
    atexit.register(lambda: os.remove(launcher.name))
    launcher.write(f'''
    export CEAM_LOGGING_DIRECTORY={worker_log_directory}
    {shutil.which('rq')} worker --url {broker_url} --name ${{JOB_ID}}.${{SGE_TASK_ID}} --burst -w "ceam_experiments.distributed_worker.ResilientWorker" --exception-handler "ceam_experiments.distributed_worker.retry_handler" ceam

    ''')
    launcher.close()

    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which('sh')
    jt.args = [launcher.name]
    sge_cluster = os.environ['SGE_CLUSTER_NAME']
    jt.jobEnvironment = {
                'LC_ALL': 'en_US.UTF-8',
                'LANG': 'en_US.UTF-8',
                'SGE_CLUSTER_NAME': sge_cluster,
            }
    jt.joinFiles = True
    jt.nativeSpecification = uge_specification(peak_memory, project)
    jt.outputPath = ':/dev/null'
    return jt


def start_cluster(drmaa_session, num_workers, peak_memory, worker_log_directory, project):
    hostname = socket.gethostname()
    port = get_random_free_port()
    _log.info('Starting Redis Broker at %s:%s', hostname, port)
    broker_process = launch_redis(port)
    broker_url = 'redis://{}:{}'.format(hostname, port)

    s = drmaa_session
    jt = init_job_template(s.createJobTemplate(), peak_memory, broker_url, worker_log_directory, project)
    if num_workers:
        job_ids = s.runBulkJobs(jt, 1, num_workers, 1)
        array_job_id = job_ids[0].split('.')[0]

        def kill_jobs():
            try:
                s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
            except drmaa.errors.InvalidJobException:
                # This is the case where all our workers have already shut down
                # on their own, which isn't actually an error.
                pass

        atexit.register(kill_jobs)

    queue = Queue('ceam', connection=redis.Redis(hostname, port))
    return queue





class RunContext:
    def __init__(self, args):
        # TODO This constructor has side effects (it creates directories under some circumstances) which is weird.
        # It should probably be split into two phases with the side effects in the second phase.

        self.cluster_project = args.project
        self.peak_memory = args.peak_memory
        self.number_already_completed = 0

        if args.result_directory is None:
            results_directory = get_default_output_directory(args.run_type)
        else:
            results_directory = args.result_directory

        if args.restart:
            self.results_writer = get_results_writer_for_restart(results_directory)
            self.keyspace = Keyspace.from_previous_run(self.results_writer.results_root)
            with open(os.path.join(self.results_writer.results_root, 'components.yaml')) as f:
                self.component_configuration = yaml.load(f)
            self.existing_outputs = pd.read_hdf(os.path.join(self.results_writer.results_root, 'output.hdf'))
        else:
            with open(args.component_configuration_file) as f:
                self.component_configuration = yaml.load(f)

            self.results_writer = get_results_writer(results_directory, args.component_configuration_file)
            self.keyspace = Keyspace.from_branch_configuration(args.num_input_draws, args.num_random_seeds,
                                                               args.branch_configuration_file)

            # Log some basic stuff about the simulation to be run.
            self.results_writer.dump_simulation_configuration(args.component_configuration_file)
            self.keyspace.persist(self.results_writer)
            self.existing_outputs = None

        self.worker_log_directory = configure_logging(self.results_writer.results_root, args.log, args.quiet)

        # Each state table is 50-200 MB, so we only write the final state table for small jobs.
        max_jobs = 100
        if args.run_type == 'validation' and len(self.keyspace) <= max_jobs:
            self.with_state_table = True
            self.results_writer.add_sub_directory('final_states', 'final_states')
        else:
            self.with_state_table = False
            if args.run_type == 'validation':
                _log.warning(f"You're launching {len(self.keyspace)} jobs.  This is too many to write final "
                             f"state tables. If you need information from the state table, reduce the "
                             f"number of draws or branches.")


def build_job_list(ctx):
    jobs = []
    number_already_completed = 0

    for (input_draw, random_seed, branch_config) in ctx.keyspace:
        parameters = {'simulation_configuration': ctx.component_configuration,
                      'config': branch_config,
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
            jobs.append((parameters, ctx.worker_log_directory, ctx.with_state_table))
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
        final_states = {}
        dirty = False

        sleep(5)
        finished_jobs = finished_registry.get_job_ids()
        for job_id in finished_jobs:
            job = queue.fetch_job(job_id)
            if ctx.with_state_table:
                result, final_state = [pd.read_json(r) for r in job.result]
                final_states[job.id] = final_state
            else:
                result = pd.read_json(job.result[0])
            results = results.append(result)
            dirty = True
            finished_registry.remove(job)

        if dirty:
            ctx.results_writer.write_output(results, 'output.hdf')
            for job_id, f in final_states.items():
                args_ = job_arguments[job_id]
                run_config = args_[0]
                branch_number = ctx.keyspace.get_branch_number(run_config['config'])
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

        percent_complete = 100*finished_jobs/(waiting_jobs+running_jobs+finished_jobs+failed_jobs)
        elapsed_time = time() - start_time

        if finished_jobs <= 100:
            remaining_time = '---'
        else:
            remaining_time = elapsed_time / (finished_jobs+failed_jobs) * (waiting_jobs+running_jobs)
            remaining_time = '{:.1f} minutes'.format(remaining_time / 60)

        # display run info, and a "heartbeat"
        heartbeat = (heartbeat + 1) % 4
        worker_count = len(ResilientWorker.all(queue=queue))
        _log.info(f'{finished_jobs+ctx.number_already_completed} completed and {failed_jobs} '
                  f'failed of {waiting_jobs+running_jobs+finished_jobs+failed_jobs+ctx.number_already_completed} '
                  f'({percent_complete:.1f}% completed)' +
                  f'/ Remaining time: {remaining_time} ' +
                  '.'*heartbeat + ' '*(4-heartbeat) +
                  f'    {worker_count} Workers' +
                  '           ')
