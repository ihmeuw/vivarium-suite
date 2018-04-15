import pandas as pd
import redis
from rq import Queue, get_failed_queue
from rq.registry import StartedJobRegistry, FinishedJobRegistry

from vivarium.framework.results_writer import ResultsWriter

from .worker import ResilientWorker


import logging
_log = logging.getLogger(__name__)

JOB_NAME = 'vivarium'
JOB = 'vivarium_cluster_tools.worker.job'


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
