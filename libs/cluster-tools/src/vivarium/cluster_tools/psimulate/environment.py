"""
=====================
Environment Variables
=====================

Environment variables used or created throughout psimulate.

"""

import os
import socket
from collections.abc import Callable
from typing import NamedTuple


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
    JOBMON_TASK_ID: EnvVariable
    JOBMON_WORKFLOW_RUN_ID: EnvVariable


ENV_VARIABLES = __EnvVariables(
    HOSTNAME=EnvVariable("HOSTNAME", finder=lambda name: socket.gethostname()),
    JOBMON_TASK_ID=EnvVariable("JOBMON_TASK_ID"),
    JOBMON_WORKFLOW_RUN_ID=EnvVariable("JOBMON_WORKFLOW_RUN_ID"),
)
