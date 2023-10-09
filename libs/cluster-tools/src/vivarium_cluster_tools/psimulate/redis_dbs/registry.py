"""
===================================
Redis Queue and Registry Management
===================================

Unified interface to multiple Redis Databases.

"""
import math
import random
import time
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Tuple, Union

import redis
import rq
from loguru import logger
from rq.job import Job
from rq.registry import FinishedJobRegistry, StartedJobRegistry


class QueueManager:

    retries_before_fail = 10
    backoff = 30

    def __init__(self, name: str, connection: redis.Redis):
        self._name = name
        self._logger = logger.bind(queue=name)

        self._queue = rq.Queue("vivarium", connection=connection)
        self._wip = StartedJobRegistry(
            "vivarium", connection=connection, job_class=self._queue.job_class
        )
        self._finished = FinishedJobRegistry(
            "vivarium", connection=connection, job_class=self._queue.job_class
        )

        self._status = {
            "total": 0,
            "pending": 0,
            "running": 0,
            "failed": 0,
            "finished": 0,
            "done": 0.0,
            "workers": 0,
        }
        self._failed = False
        self._completed = False

        self._retries = QueueManager.retries_before_fail
        self._backoff = QueueManager.backoff

    @property
    def name(self) -> str:
        return self._name

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def jobs_to_finish(self) -> bool:
        return not (self.failed or self.completed)

    def enqueue(self, jobs: List[dict], workhorse_import_path: str) -> None:
        self._logger.info(f"Enqueuing jobs in queue {self.name}")
        for job in jobs:
            # TODO: might be nice to have tighter ttls but it's hard to predict
            #    how long our jobs will take from model to model and the entire
            #    system is short lived anyway
            self._queue.enqueue(
                workhorse_import_path,
                job_parameters=job,
                ttl=60 * 60 * 24 * 2,
                result_ttl=60 * 60,
                job_timeout="7d",
            )

    def get_results(self) -> List:
        self._logger.info(f"Checking queue {self.name}")
        finished_jobs = self._get_finished_jobs()
        start = time.time()
        results = []
        for job_id in finished_jobs:
            result = self._get_result(job_id)
            if result is not None:
                results.append(result)
        self._logger.info(
            f"Retrieved {len(results)} results from queue {self.name} in {time.time() - start:.2f}s"
        )
        return results

    def update_and_report(self) -> Dict[str, int]:
        self._update_status()
        template = (
            f"Queue {self.name} - Total jobs: {{total}}, % Done: {{done:.2f}}% "
            f"Pending: {{pending}}, Running: {{running}}, Failed: {{failed}}, Finished: {{finished}} "
            f"Workers: {{workers}}."
        )
        if not (self.completed or self.failed):
            self._logger.info(template.format(**self._status))
        return self._status

    def _update_status(self) -> None:
        if self.jobs_to_finish and self._retries > 0:
            self._logger.info(f"Updating status for queue {self.name}")
            # TODO: Sometimes there are duplicate job_ids, why?
            try:
                # Account for timing discrepancies in accounting and the
                # fact that a job might legit be in more than one of these.
                finished_ids = set(self._finished.get_job_ids()) if self._queue else set()

                running_ids = set(self._wip.get_job_ids()) if self._queue else set()
                # "Finished" for our purposes means the job is done
                # and the results have been retrieved, whereas for rq
                # finished means the job is done and the results have
                # not been retrieved. Once retrieved, jobs disappear
                # from the finished job registry and we'd see all our
                # account eventually just go to zero.
                running_ids = running_ids | finished_ids

                pending_ids = set(self._queue.job_ids) if self._queue else set()
                pending_ids = pending_ids - running_ids

                failed_ids = (
                    set(self._queue.failed_job_registry.get_job_ids())
                    if self._queue
                    else set()
                )
                failed_ids = failed_ids - (running_ids | pending_ids)

                q_workers = len(rq.Worker.all(queue=self._queue)) if self._queue else 0

                self._status["pending"] = len(pending_ids)
                self._status["running"] = len(running_ids) + len(finished_ids)
                self._status["failed"] = len(failed_ids)
                self._status["finished"] = self._status["finished"]
                self._status["total"] = (
                    len(pending_ids)
                    + len(running_ids)
                    + len(failed_ids)
                    + self._status["finished"]
                )
                self._status["workers"] = q_workers
                self._status["done"] = 100 * self._status["finished"] / self._status["total"]

                if len(pending_ids) + len(running_ids) == 0:
                    self._mark_complete()
                elif len(pending_ids) + len(finished_ids) + q_workers == 0:
                    self._logger.info(
                        f"Queue {self.name} ran out of workers with running jobs.  Marking finished."
                    )
                    self._mark_complete()
            except redis.connection.ConnectionError:
                self._sleep_on_it()
                self._update_status()
        else:
            self._mark_failed()

    def _get_finished_jobs(self) -> List[str]:
        if self._retries:
            try:
                return self._finished.get_job_ids()
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                return self._get_finished_jobs()
        else:
            self._mark_failed()
            return []

    def _get_result(self, job_id: str) -> Any:
        job = self._get_job(job_id)
        result = None
        if job is not None:
            start = time.time()
            result = job.result
            end = time.time()
            self._logger.debug(
                f"Deserialized {job_id} result from queue {self.name} in {end - start:.2f}s."
            )
            self._status["finished"] += 1
            self._remove_finished_job(job)
        return result

    def _get_job(self, job_id: str) -> Union[None, Job]:
        if self._retries:
            try:
                start = time.time()
                job = self._queue.fetch_job(job_id)
                end = time.time()
                self._logger.debug(
                    f"Fetched job {job_id} from queue {self.name} in {end - start:.2f}s"
                )
                return job
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                return self._get_job(job_id)
        else:
            self._mark_failed()
            return None

    def _remove_finished_job(self, job: Job) -> None:
        if self._retries:
            try:
                self._finished.remove(job)
            except redis.exceptions.ConnectionError:
                self._sleep_on_it()
                self._remove_finished_job(job)
        else:
            self._mark_failed()

    def _sleep_on_it(self) -> None:
        backoff = self._backoff * random.random()
        self._logger.warning(
            f"Failed to connect to redis for queue {self.name}.  Retrying in {backoff}s."
        )
        self._retries -= 1
        time.sleep(backoff)

    def _mark_failed(self) -> None:
        # TODO: Probably should cleanup redis stuff, but not sure how yet.
        if not (self.failed or self.completed):
            self._logger.warning(
                f"Queue {self.name} has run out of retries.  Marking as failed."
            )
            self._status["failed"] += self._status["pending"] + self._status["running"]
            self._status["pending"] = 0
            self._status["running"] = 0
            self._status["workers"] = 0
            self._failed = True

    def _mark_complete(self) -> None:
        if not (self.failed or self.completed):
            self._logger.info(f"All jobs on queue {self.name} complete.")
            self._status["finished"] += self._status["pending"] + self._status["running"]
            self._status["pending"] = 0
            self._status["running"] = 0
            self._status["workers"] = 0
            self._completed = True


class RegistryManager:
    def __init__(self, redis_processes: List[Tuple[str, int]], num_already_completed: int):
        self._logger = logger.bind(queue="all")
        self._logger.info("Building registries.")
        self._queues = [
            QueueManager(str(i), connection=redis.Redis(hostname, port))
            for i, (hostname, port) in enumerate(redis_processes)
        ]
        self._previously_completed = num_already_completed

    @property
    def jobs_to_finish(self) -> bool:
        return any([q.jobs_to_finish for q in self._queues])

    def enqueue(self, jobs: List[Dict], workhorse_import_path: str) -> None:
        for queue, jobs_chunk in zip(self._queues, self.allocate_jobs(jobs)):
            queue.enqueue(jobs_chunk, workhorse_import_path)

    def allocate_jobs(self, jobs: List[Dict]) -> Iterator[List[Dict]]:
        """Allocate jobs to queues in a round robin fashion."""
        num_queues = len(self._queues)
        for mod in range(num_queues):
            yield [job for i, job in enumerate(jobs) if i % num_queues == mod]

    def get_results(self) -> List:
        to_check = [q for q in self._queues if q.jobs_to_finish]
        results = []
        for queue in to_check:
            results.extend(queue.get_results())
        return results

    def update_and_report(self) -> Dict[str, Union[int, float]]:
        status = defaultdict(int)
        for queue in self._queues:
            queue_status = queue.update_and_report()
            for k, v in queue_status.items():
                status[k] += v
        status["done"] = status["finished"] / status["total"] * 100
        status["finished"] += self._previously_completed

        template = (
            "Queue all - Total jobs: {total}, % Done: {done:.2f}% "
            "Pending: {pending}, Running: {running}, Failed: {failed}, Finished: {finished} "
            "Workers: {workers}."
        )

        self._logger.info(template.format(**status))
        return status

    def __len__(self) -> int:
        return len(self._queues)
