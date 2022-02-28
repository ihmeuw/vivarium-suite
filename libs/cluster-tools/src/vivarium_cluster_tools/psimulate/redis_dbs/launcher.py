"""
=============================
Redis Database Initialization
=============================

Creates redis databases to store job parameters and results.

"""
import atexit
import math
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, TextIO, Tuple

from loguru import logger

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES

DEFAULT_NUM_REDIS_DBS = -1
DEFAULT_JOBS_PER_REDIS_INSTANCE = 1000


def launch_redis_processes(
    num_processes: int,
    num_jobs: int,
    redis_logging_root: Path,
) -> List[Tuple[str, int]]:
    num_processes = _get_num_redis_dbs(num_processes, num_jobs)

    hostname = ENV_VARIABLES.HOSTNAME.value
    redis_ports = []
    for i in range(num_processes):
        port = _get_random_free_port()
        logger.info(f"Starting Redis Broker at {hostname}:{port}")
        redis_log = (redis_logging_root / f"redis.p{port}.log").open("a")
        _launch_redis(port, stdout=redis_log, stderr=redis_log)
        redis_ports.append((hostname, port))
    time.sleep(5)  # Give the dbs a few seconds to spin up.
    return redis_ports


def _get_num_redis_dbs(num_processes: int, num_jobs: int) -> int:
    if num_processes == DEFAULT_NUM_REDIS_DBS:
        num_processes = int(math.ceil(num_jobs / DEFAULT_JOBS_PER_REDIS_INSTANCE))
    return num_processes


def _launch_redis(
    port: int, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr
) -> subprocess.Popen:
    stdout.write(f">>>>>>>> Starting log for redis-server on port {port}\n")
    stdout.flush()
    try:
        # inline config for redis server.
        redis_process = subprocess.Popen(
            [
                "redis-server",
                "--port",
                f"{port}",
                "--timeout",
                "2",
                "--loglevel",
                "debug",
                "--protected-mode",
                "no",
            ],
            stdout=stdout,
            stderr=stderr,
        )
    except FileNotFoundError:
        raise OSError(
            "In order for redis to launch you need both the redis client and the python bindings. "
            "You seem to be missing the redis client.  Do 'conda install redis' and try again. If "
            "failures continue you may need to download redis yourself, make it and add it to PATH."
        )
    atexit.register(redis_process.kill)
    return redis_process


def _get_random_free_port() -> int:
    # NOTE: this implementation is vulnerable to rare race conditions where some other
    # process gets the same port after we free our socket but before we use the port
    # number we got. Should be so rare in practice that it doesn't matter.
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port
