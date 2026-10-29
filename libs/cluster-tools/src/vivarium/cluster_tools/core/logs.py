"""
=================
Logging Utilities
=================

"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

_SHORT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <green>{elapsed}</green> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>\n{exception}"
)


def configure_main_process_logging_to_terminal(verbose: int) -> None:
    logger.remove()  # Clear all existing sinks
    _add_logging_sink(sys.stdout, verbosity=verbose, colorize=True, serialize=False)


def configure_main_process_logging_to_file(output_directory: Path) -> None:
    main_log = output_directory / "main.log"
    serial_log = output_directory / "main.log.json"
    _add_logging_sink(main_log, verbosity=2, colorize=False, serialize=False)
    _add_logging_sink(serial_log, verbosity=2, colorize=False, serialize=True)


def _add_logging_sink(
    sink: Path | TextIO, verbosity: int, colorize: bool, serialize: bool
) -> int:
    """Add a short-format loguru sink at the level that ``verbosity`` maps to."""
    return logger.add(
        sink,
        colorize=colorize,
        level=_get_log_level(verbosity),
        format=_short_format,
        serialize=serialize,
    )


def _short_format(record: Record) -> str:
    """Return the short log-line format: time, elapsed time, location and message."""
    return _SHORT_FORMAT


def _get_log_level(verbosity: int) -> str:
    """Map a ``-v`` count to WARNING (0 or less), INFO (1) or DEBUG (2 or more)."""
    if verbosity <= 0:
        return "WARNING"
    elif verbosity == 1:
        return "INFO"
    else:
        return "DEBUG"
