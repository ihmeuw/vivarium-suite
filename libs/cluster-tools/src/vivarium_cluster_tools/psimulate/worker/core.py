"""
==================
Distributed Worker
==================

RQ worker with custom retry handling.

"""
import os
import random
import time
from pathlib import Path

import redis
from loguru import logger
from rq import Queue
from rq.job import JobStatus
from rq.registry import FailedJobRegistry
from rq.worker import Worker

from vivarium_cluster_tools.psimulate.cluster import ENV_VARIABLES

RETRY_HANDLER_IMPORT_PATH = f"{__name__}.retry_handler"
WORKER_CLASS_IMPORT_PATH = f"{__name__}.ResilientWorker"


def retry_handler(job, *exc_info):
    retries = job.meta.get("remaining_retries", 2)

    if retries > 0:
        retries -= 1
        job.meta["remaining_retries"] = retries
        job.set_status(JobStatus.QUEUED)
        job.exc_info = exc_info
        job.save()
        q = Queue(name=job.origin, connection=job.connection)
        q.enqueue_job(job)
        logger.info(f"Retrying job {job.id}")
    else:
        logger.error(f"Failing job {job.id}")
        q = Queue(name=job.origin, connection=job.connection)
        failed_queue = FailedJobRegistry(queue=q)
        failed_queue.add(job, exc_string=exc_info)
    return False


class ResilientWorker(Worker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.acceptable_failure_count = 3

    def work(self, *args, **kwargs):
        worker_ = self.name
        logging_directory = Path(ENV_VARIABLES.VIVARIUM_LOGGING_DIRECTORY.value)
        logger.add(logging_directory / (str(worker_) + ".log"), level="DEBUG", serialize=True)
        kwargs["logging_level"] = "DEBUG"

        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).work(*args, **kwargs)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random() * 60
                logger.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                time.sleep(backoff)
        logger.error(f"Ran out of retries. Killing worker")

    def main_work_horse(self, job, queue):
        retries = 0
        while retries < 10:
            try:
                super(ResilientWorker, self).main_work_horse(job, queue)
                return
            except redis.exceptions.ConnectionError:
                backoff = random.random() * 60
                logger.error(f"Couldn't connect to redis. Retrying in {backoff}...")
                retries += 1
                time.sleep(backoff)
        logger.error(f"Ran out of retries. Killing work horse")

    def fork_work_horse(self, job, queue):
        """Spawns a work horse to perform the actual work and passes it a job."""
        child_pid = os.fork()
        ENV_VARIABLES.RQ_WORKER_ID.update(self.name)
        ENV_VARIABLES.RQ_JOB_ID.update(job.id)
        if child_pid == 0:
            self.main_work_horse(job, queue)
            os._exit(0)  # just in case
        else:
            self._horse_pid = child_pid
            self.procline("Forked {0} at {1}".format(child_pid, time.time()))
