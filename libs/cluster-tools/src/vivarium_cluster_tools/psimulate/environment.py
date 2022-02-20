"""
=====================
Environment Variables
=====================

Environment variables used or created throughout psimulate.

"""
import os
from typing import NamedTuple


class EnvVariable:
    """Convenience wrapper around an environment variable."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> str:
        return os.environ[self.name]

    @property
    def exists(self) -> bool:
        return self.name in os.environ

    def update(self, value: str) -> None:
        os.environ[self.name] = value


class __EnvVariables(NamedTuple):
    HOSTNAME: EnvVariable
    JOB_NAME: EnvVariable
    JOB_ID: EnvVariable
    TASK_ID: EnvVariable
    VIVARIUM_LOGGING_DIRECTORY: EnvVariable
    RQ_WORKER_ID: EnvVariable
    RQ_JOB_ID: EnvVariable
    DRMAA_LIB_PATH: EnvVariable
    PYTHONPATH: EnvVariable


ENV_VARIABLES = __EnvVariables(
    HOSTNAME=EnvVariable("HOSTNAME"),
    JOB_NAME=EnvVariable("JOB_NAME"),
    JOB_ID=EnvVariable("JOB_ID"),
    TASK_ID=EnvVariable("SGE_TASK_ID"),
    VIVARIUM_LOGGING_DIRECTORY=EnvVariable("VIVARIUM_LOGGING_DIRECTORY"),
    RQ_WORKER_ID=EnvVariable("RQ_WORKER_ID"),
    RQ_JOB_ID=EnvVariable("RQ_JOB_ID"),
    DRMAA_LIB_PATH=EnvVariable("DRMAA_LIBRARY_PATH"),
    PYTHONPATH=EnvVariable("PYTHONPATH"),
)
