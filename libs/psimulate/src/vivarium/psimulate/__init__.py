"""
=========
psimulate
=========

Parallel runner for :mod:`vivarium.engine` jobs.

"""
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple

try:
    __version__ = version("vivarium-psimulate")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"


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

TASK_RUNNER_MODULE: str = "vivarium.psimulate.worker.task_runner"
