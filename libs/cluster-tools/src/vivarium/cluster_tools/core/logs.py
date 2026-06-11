"""
=================
Logging Utilities
=================

"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from vivarium.engine.framework.logging import add_logging_sink


def configure_main_process_logging_to_terminal(verbose: int) -> None:
    logger.remove()  # Clear all existing sinks
    add_logging_sink(
        sys.stdout, verbosity=verbose, long_format=False, colorize=True, serialize=False
    )


def configure_main_process_logging_to_file(output_directory: Path) -> None:
    main_log = output_directory / "main.log"
    serial_log = output_directory / "main.log.json"
    add_logging_sink(
        main_log, verbosity=2, long_format=False, colorize=False, serialize=False
    )
    add_logging_sink(
        serial_log, verbosity=2, long_format=False, colorize=False, serialize=True
    )
