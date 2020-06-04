import sys
from typing import TextIO

from loguru import logger


def add_logging_sink(sink: TextIO, verbose: int, colorize: bool = False, serialize: bool = False):
    """Adds a logging sink to the global process logger.

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
    message_format = ('<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <green>{elapsed}</green> | '
                      '<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>')
    if verbose == 0:
        logger.add(sink, colorize=colorize, level="WARNING", format=message_format, serialize=serialize)
    elif verbose == 1:
        logger.add(sink, colorize=colorize, level="INFO", format=message_format, serialize=serialize)
    elif verbose >= 2:
        logger.add(sink, colorize=colorize, level="DEBUG", format=message_format, serialize=serialize)


def configure_logging_to_terminal(verbose: int):
    """Sets up logging to ``sys.stdout``.

    Parameters
    ----------
    verbose
        Verbosity of the logger.

    """
    logger.remove(0)  # Clear default configuration
    add_logging_sink(sys.stdout, verbose, colorize=True)


def decode_status(drmaa, job_status):
    decoder_map = {drmaa.JobState.UNDETERMINED: 'undetermined',
                   drmaa.JobState.QUEUED_ACTIVE: 'queued_active',
                   drmaa.JobState.SYSTEM_ON_HOLD: 'system_hold',
                   drmaa.JobState.USER_ON_HOLD: 'user_hold',
                   drmaa.JobState.USER_SYSTEM_ON_HOLD: 'user_system_hold',
                   drmaa.JobState.RUNNING: 'running',
                   drmaa.JobState.SYSTEM_SUSPENDED: 'system_suspended',
                   drmaa.JobState.USER_SUSPENDED: 'user_suspended',
                   drmaa.JobState.DONE: 'finished',
                   drmaa.JobState.FAILED: 'failed'}

    return decoder_map[job_status]
