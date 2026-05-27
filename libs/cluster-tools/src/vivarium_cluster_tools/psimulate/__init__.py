"""
=========
psimulate
=========

Parallel runner for :mod:`vivarium` jobs.

"""
from typing import NamedTuple


class __Commands(NamedTuple):
    run: str
    restart: str
    expand: str
    load_test: str


COMMANDS = __Commands(*__Commands._fields)

del NamedTuple
del __Commands

TASK_RUNNER_MODULE: str = "vivarium_cluster_tools.psimulate.worker.task_runner"
