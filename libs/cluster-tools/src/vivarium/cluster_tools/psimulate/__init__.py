"""
=========
psimulate
=========

Parallel runner for :mod:`vivarium.engine` jobs.

"""
from typing import NamedTuple


class __Commands(NamedTuple):
    run: str
    restart: str
    expand: str
    load_test: str


COMMANDS = __Commands(*__Commands._fields)

RESUME_COMMANDS = (COMMANDS.restart, COMMANDS.expand)
"""The commands that continue an existing run rather than starting a new one."""

del NamedTuple
del __Commands

TASK_RUNNER_MODULE: str = "vivarium.cluster_tools.psimulate.worker.task_runner"
