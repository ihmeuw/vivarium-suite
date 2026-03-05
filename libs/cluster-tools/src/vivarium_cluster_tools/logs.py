"""
=================
Logging Utilities
=================

"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from loguru import logger


def add_logging_sink(
    sink: TextIO | str | Path, verbose: int, colorize: bool = False, serialize: bool = False
) -> None:
    message_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
        "- <level>{message}</level>"
    )
    if verbose == 0:
        logger.add(
            sink,
            colorize=colorize,
            level="INFO",
            format=message_format,
            serialize=serialize,
        )
    else:
        logger.add(
            sink, colorize=colorize, level="DEBUG", format=message_format, serialize=serialize
        )


def configure_main_process_logging_to_terminal(verbose: int) -> None:
    logger.remove()  # Clear default configuration
    add_logging_sink(sys.stdout, verbose, colorize=True)


def configure_main_process_logging_to_file(output_directory: Path) -> None:
    main_log = output_directory / "main.log"
    serial_log = output_directory / "main.log.json"
    add_logging_sink(main_log, verbose=2)
    add_logging_sink(serial_log, verbose=2, serialize=True)
