"""
=====================
Environment Variables
=====================

Environment variables used or created throughout psimulate.

"""
import os
import socket
from typing import Callable, NamedTuple


class EnvVariable:
    """Convenience wrapper around an environment variable."""

    def __init__(
        self, name: str, finder: Callable[[str], str] = lambda name: os.environ[name]
    ):
        self._name = name
        self._finder = finder

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> str:
        return self._finder(self.name)

    def update(self, value: str) -> None:
        os.environ[self.name] = value
        self._finder = lambda name: os.environ[name]


class __EnvVariables(NamedTuple):
    HOSTNAME: EnvVariable
    JOB_ID: EnvVariable
    TASK_ID: EnvVariable
    VIVARIUM_LOGGING_DIRECTORY: EnvVariable
    RQ_WORKER_ID: EnvVariable
    RQ_JOB_ID: EnvVariable
    DRMAA_LIB_PATH: EnvVariable
    PYTHONPATH: EnvVariable


ENV_VARIABLES = __EnvVariables(
    HOSTNAME=EnvVariable("HOSTNAME", finder=lambda name: socket.gethostname()),
    JOB_ID=EnvVariable("SLURM_ARRAY_JOB_ID"),
    TASK_ID=EnvVariable("SLURM_ARRAY_TASK_ID"),
    VIVARIUM_LOGGING_DIRECTORY=EnvVariable("VIVARIUM_LOGGING_DIRECTORY"),
    RQ_WORKER_ID=EnvVariable("RQ_WORKER_ID"),
    RQ_JOB_ID=EnvVariable("RQ_JOB_ID"),
    DRMAA_LIB_PATH=EnvVariable("DRMAA_LIBRARY_PATH"),
    PYTHONPATH=EnvVariable("PYTHONPATH"),
)
