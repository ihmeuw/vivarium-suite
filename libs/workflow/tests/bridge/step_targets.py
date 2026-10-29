"""Step targets that the runner and bridge tests name by reference.

A runner subprocess imports this module as ``tests.bridge.step_targets`` from the
``PYTHONPATH`` that the tests give it, so it imports nothing from the test suite.
"""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

FAILURE_MESSAGE = "the step failed on purpose"
"""Message of the error that :func:`raise_step_failure` raises."""

STDOUT_BEFORE_FAILURE = "written to stdout before the failure"
"""Line that :func:`raise_step_failure` prints to standard output before it raises."""

STDERR_BEFORE_FAILURE = "written to stderr before the failure"
"""Line that :func:`raise_step_failure` prints to standard error before it raises."""

FD1_OUTPUT = "written to file descriptor 1"
"""Line that :func:`write_to_streams_and_descriptors` writes to file descriptor 1."""

FD2_OUTPUT = "written to file descriptor 2"
"""Line that :func:`write_to_streams_and_descriptors` writes to file descriptor 2."""

STDOUT_PRINT = "printed to sys.stdout"
"""Line that :func:`write_to_streams_and_descriptors` prints to ``sys.stdout``."""

STDERR_PRINT = "printed to sys.stderr"
"""Line that :func:`write_to_streams_and_descriptors` prints to ``sys.stderr``."""

_CHILD_COMMAND = "echo capture-out-2; echo capture-err-3 >&2"
"""Shell command whose output :func:`write_to_every_stream` puts between its own lines."""


class StepFailure(Exception):
    """Error that a failing step target raises."""


def _frame() -> pd.DataFrame:
    """Return a small frame for a step to save."""
    return pd.DataFrame({"n": [1, 2, 3]})


def raise_step_failure() -> pd.DataFrame:
    """Print a line to each of stdout and stderr, then fail with StepFailure."""
    print(STDOUT_BEFORE_FAILURE)
    print(STDERR_BEFORE_FAILURE, file=sys.stderr)
    raise StepFailure(FAILURE_MESSAGE)


def write_to_every_stream() -> pd.DataFrame:
    """Write numbered lines to both streams from Python, os.write and a child process."""
    print("capture-out-1")
    print("capture-err-1", file=sys.stderr)
    os.write(2, b"capture-err-2\n")
    subprocess.run(["/bin/sh", "-c", _CHILD_COMMAND], check=True)
    print("capture-out-3")
    print("capture-err-4", file=sys.stderr)
    return _frame()


def write_to_streams_and_descriptors() -> pd.DataFrame:
    """Print a line to sys.stdout and to sys.stderr, then os.write a line to fds 1 and 2."""
    print(STDOUT_PRINT)
    print(STDERR_PRINT, file=sys.stderr)
    os.write(1, f"{FD1_OUTPUT}\n".encode())
    os.write(2, f"{FD2_OUTPUT}\n".encode())
    return _frame()


def exit_with_status_zero() -> pd.DataFrame:
    """Call sys.exit(0) instead of returning a frame."""
    sys.exit(0)


def raise_keyboard_interrupt() -> pd.DataFrame:
    """Raise KeyboardInterrupt instead of returning a frame."""
    raise KeyboardInterrupt


def constant() -> pd.DataFrame:
    """Return a small fixed frame."""
    return _frame()


def stack(**upstreams: Path) -> pd.DataFrame:
    """Read each upstream step's frame and stack them, ordered by argument name."""
    frames = [pd.read_parquet(upstreams[name]) for name in sorted(upstreams)]
    return pd.concat(frames, ignore_index=True)


def record_pid(**upstreams: Path) -> pd.DataFrame:
    """Return the process ids that the upstream steps recorded, then this process's id."""
    frames = [pd.read_parquet(upstreams[name]) for name in sorted(upstreams)]
    frames.append(pd.DataFrame({"pid": [os.getpid()]}))
    return pd.concat(frames, ignore_index=True)
