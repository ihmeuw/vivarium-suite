"""
===================================
Redis Queue and Registry Management
===================================

Centralized management of jobs and results.

"""
from collections import defaultdict
import math
import random
import time

from loguru import logger
import redis
import rq
from rq.registry import StartedJobRegistry, FinishedJobRegistry

from .distributed_worker import ResilientWorker
from .utilities import chunks


class QueueManager:

    retries_before_fail = 10
    backoff = 30

    def __init__(self, name, connection):
        self._name = name
        self._logger = logger.bind(queue=name)

        self._queue = rq.Queue('vivarium', connection=connection)
        self._wip = StartedJobRegistry('vivarium', connection=connection, job_class=self._queue.job_class)
        self._finished = FinishedJobRegistry('vivarium', connection=connection, job_class=self._queue.job_class)

        self._status = {'total': 0, 'pending': 0, 'running': 0, 'failed': 0, 'finished': 0, 'done': 0., 'workers': 0}
        self._failed = False
        self._completed = False

        self._retries = QueueManager.retries_before_fail
        self._backoff = QueueManager.backoff

    @property
    def name(self):
        return self._name

    @property
    def failed(self):
        return self._failed

    @property
    def completed(self):
        return self._completed

    @property
    def jobs_to_finish(self):
        return not (self.failed or self.completed)

    def enqueue(self, jobs):
        self._logger.info(f'Enqueuing jobs in queue {self.name}')
        for job in jobs:
            # TODO: might be nice to have tighter ttls but it's hard to predict how long our jobs
            # will take from model to model and the entire system is short lived anyway
            self._queue.enqueue('vivarium_cluster_tools.psimulate.distributed_worker.worker',
                                parameters=job,
                                ttl=60 * 60 * 24 * 2,
                                result_ttl=60 * 60,
                                job_timeout='7d')

    def get_results(self):
        self._logger.info(f'Checking queue {self.name}')
        finished_jobs = self._get_finished_jobs()
        start = time.time()
        results = []
        for job_id in finished_jobs:
            result = self._get_result(job_id)
            if result is not None:
                results.append(result)
        self._logger.info(f'Retrieved {len(results)} results from queue {self.name} in {time.time() - start:.2f}s')
        return results

    def update_and_report(self):
        self._update_status()
        template = (f"Queue {self.name} - Total jobs: {{total}}, % Done: {{done:.2f}}% "
                    f"Pending: {{pending}}, Running: {{running}}, Failed: {{failed}}, Finished: {{finished}} "
                    f"Workers: {{workers}}.")
        if not (self.completed or self.failed):
            self._logger.info(template.format(**self._status))
        return self._status

    def _update_status(self):
        if self.jobs_to_finish and self._retries > 0:
            self._logger.info(f'Updating status for queue {self.name}')
            # TODO: Sometimes there are duplicate job_ids, why?
            try:
                q_pending = len(set(self._queue.job_ids)) if self._queue else 0
                q_running = len(self._wip) if self._wip else 0
                q_to_write = len(self._finished) if self._finished else 0
                q_failed = len(self._queue.failed_job_registry) if self._queue else self._status['failed']
                q_finished = self._status['finished']
                q_total = q_pending + q_running + q_failed + q_to_write + q_finished
                q_workers = len(ResilientWorker.all(queue=self._queue)) if self._queue else 0

                self._status['pending'] = q_pending
                self._status['running'] = q_running + q_to_write
                self._status['failed'] = q_failed
                self._status['finished'] = q_finished
                self._status['total'] = q_total
                self._status['workers'] = q_workers
                self._status['done'] = q_finished / q_total * 100

                if sum([q_pending, q_running, q_to_write]) == 0:
                    self._mark_complete()
                elif sum([q_pending, q_workers, q_to_write]) == 0:
                    self._logger.info(f'Queue {self.name} ran out of workers with running jobs.  Marking finished.')
                    self._mark_complete()
            except redis.connection.ConnectionError:
                self._sleep_on_it()
                self._update_status()
        else:
            self._mark_failed()

    def _get_finished_jobs(self):
        if self._retries:
            try:
                return self._finished.get_job_ids()
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                return self._get_finished_jobs()
        else:
            self._mark_failed()
            return []

    def _get_result(self, job_id):
        job = self._get_job(job_id)
        result = None
        if job is not None:
            start = time.time()
            result = job.result
            end = time.time()
            self._logger.debug(f'Read {job_id} from msgpack from queue {self.name} in {end - start:.2f}s.')
            self._status['finished'] += 1
            self._remove_finished_job(job)
        return result

    def _get_job(self, job_id):
        if self._retries:
            try:
                start = time.time()
                job = self._queue.fetch_job(job_id)
                end = time.time()
                self._logger.debug(f'Fetched job {job_id} from queue {self.name} in {end - start:.2f}s')
                return job
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                return self._get_job(job_id)
        else:
            self._mark_failed()
            return None

    def _remove_finished_job(self, job):
        if self._retries:
            try:
                self._finished.remove(job)
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                self._remove_finished_job(job)
        else:
            self._mark_failed()

    def _sleep_on_it(self):
        backoff = self._backoff * random.random()
        self._logger.warning(f'Failed to connect to redis for queue {self.name}.  Retrying in {backoff}s.')
        self._retries -= 1
        time.sleep(backoff)

    def _mark_failed(self):
        # TODO: Probably should cleanup redis stuff, but not sure how yet.
        if not (self.failed or self.completed):
            self._logger.warning(f'Queue {self.name} has run out of retries.  Marking as failed.')
            self._status['failed'] += self._status['pending'] + self._status['running']
            self._status['pending'] = 0
            self._status['running'] = 0
            self._status['workers'] = 0
            self._failed = True

    def _mark_complete(self):
        if not (self.failed or self.completed):
            self._logger.info(f'All jobs on queue {self.name} complete.')
            self._status['finished'] += self._status['pending'] + self._status['running']
            self._status['pending'] = 0
            self._status['running'] = 0
            self._status['workers'] = 0
            self._completed = True


class RegistryManager:

    def __init__(self, redis_processes, already_completed):
        self._logger = logger.bind(queue='all')
        self._logger.info('Building registries.')
        self._queues = [QueueManager(i, connection=redis.Redis(hostname, port))
                        for i, (hostname, port) in enumerate(redis_processes)]
        self._previously_completed = already_completed

    @property
    def jobs_to_finish(self):
        return any([q.jobs_to_finish for q in self._queues])

    def enqueue(self, jobs):
        workers_per_queue = int(math.ceil(len(jobs) / len(self._queues)))
        for queue, jobs_chunk in zip(self._queues, chunks(jobs, workers_per_queue)):
            queue.enqueue(jobs_chunk)

    def get_results(self):
        to_check = [q for q in self._queues if q.jobs_to_finish]
        results = []
        for queue in to_check:
            results.extend(queue.get_results())
        return results

    def update_and_report(self):
        status = defaultdict(int)
        for queue in self._queues:
            queue_status = queue.update_and_report()
            for k, v in queue_status.items():
                status[k] += v
        status['done'] = status['finished'] / status['total'] * 100
        status['finished'] += self._previously_completed

        template = ("Queue all - Total jobs: {total}, % Done: {done:.2f}% "
                    "Pending: {pending}, Running: {running}, Failed: {failed}, Finished: {finished} "
                    "Workers: {workers}.")

        self._logger.info(template.format(**status))
        return status

    def __len__(self):
        return len(self._queues)
