from __future__ import annotations

from pathlib import Path
from typing import TextIO

from vivarium.engine.framework.logging import add_logging_sink as _add_logging_sink
from vivarium.engine.framework.logging import (
    configure_logging_to_terminal as _configure_logging_to_terminal,
)


def add_logging_sink(
    sink: TextIO | str | Path,
    verbose: int,
    colorize: bool = False,
    serialize: bool = False,
) -> None:
    """Add a logging sink to the global process logger.

    Parameters
    ----------
    sink
        Either a file or system file descriptor like ``sys.stdout``.
    verbose
        Verbosity of the logger.
    colorize
        Whether to use the colorization options from :mod:`loguru`.
    serialize
        Whether the logs should be converted to JSON before they're dumped
        to the logging sink.
    """
    _add_logging_sink(
        sink,
        verbosity=verbose,
        long_format=False,
        colorize=colorize,
        serialize=serialize,
    )


def configure_logging_to_terminal(verbose: int) -> None:
    """Set up logging to ``sys.stdout``.

    Parameters
    ----------
    verbose
        Verbosity of the logger.
    """
    _configure_logging_to_terminal(verbosity=verbose, long_format=False)


def decode_status(drmaa, job_status):
    decoder_map = {
        drmaa.JobState.UNDETERMINED: "undetermined",
        drmaa.JobState.QUEUED_ACTIVE: "queued_active",
        drmaa.JobState.SYSTEM_ON_HOLD: "system_hold",
        drmaa.JobState.USER_ON_HOLD: "user_hold",
        drmaa.JobState.USER_SYSTEM_ON_HOLD: "user_system_hold",
        drmaa.JobState.RUNNING: "running",
        drmaa.JobState.SYSTEM_SUSPENDED: "system_suspended",
        drmaa.JobState.USER_SUSPENDED: "user_suspended",
        drmaa.JobState.DONE: "finished",
        drmaa.JobState.FAILED: "failed",
    }

    return decoder_map[job_status]
