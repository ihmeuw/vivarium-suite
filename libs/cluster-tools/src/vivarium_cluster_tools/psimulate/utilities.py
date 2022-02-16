"""
===================
psimulate Utilities
===================

Utilities for psimulate runs.

"""
import sys
from datetime import datetime
from pathlib import Path
from shutil import rmtree
from typing import Dict, Sequence, Tuple

from loguru import logger

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate import globals as vct_globals


def add_logging_sink(sink, verbose: int, colorize=False, serialize=False):
    message_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
        "- <level>{message}</level>"
    )
    if verbose == 0:

        def quiet_filter(record):
            return record.get("extra", {}).get("queue", None) == "all"

        logger.add(
            sink,
            colorize=colorize,
            level="INFO",
            format=message_format,
            filter=quiet_filter,
            serialize=serialize,
        )
    elif verbose == 1:
        logger.add(
            sink, colorize=colorize, level="INFO", format=message_format, serialize=serialize
        )
    elif verbose >= 2:
        logger.add(
            sink, colorize=colorize, level="DEBUG", format=message_format, serialize=serialize
        )


def configure_master_process_logging_to_terminal(verbose: int):
    logger.remove(0)  # Clear default configuration
    add_logging_sink(sys.stdout, verbose, colorize=True)


def configure_master_process_logging_to_file(output_directory: Path):
    master_log = output_directory / "master.log"
    serial_log = output_directory / "master.log.json"
    add_logging_sink(master_log, verbose=2)
    add_logging_sink(serial_log, verbose=2, serialize=True)


def get_output_directory(
    model_specification_file: str = None, output_directory: str = None, restart: bool = False
) -> Path:
    if restart:
        output_directory = Path(output_directory)
    else:
        root = (
            Path(output_directory)
            if output_directory
            else Path(vct_globals.DEFAULT_OUTPUT_DIRECTORY)
        )
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        model_specification_name = Path(model_specification_file).stem
        output_directory = root / model_specification_name / launch_time
    return output_directory


def set_permissions(output_dir: Path):
    """Call to achieve side effect of relaxing permissions to 775
    on output dir and the parent"""
    permissions = 0o775
    output_dir.parent.chmod(permissions)
    output_dir.chmod(permissions)


def setup_directories(
    model_specification_file: str, result_directory: str, restart: bool, expand: bool
) -> Tuple[Path, Dict[str, Path]]:
    output_directory = get_output_directory(
        model_specification_file, result_directory, restart
    )

    if restart and not expand:
        command = "restart"
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    elif restart and expand:
        command = "expand"
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    else:
        command = "run"
        launch_time = output_directory.stem

    logging_directory = output_directory / "logs" / f"{launch_time}_{command}"

    logging_dirs = {
        "main": logging_directory,
        "sge": logging_directory / "sge_logs",
        "worker": logging_directory / "worker_logs",
    }

    vct_utils.mkdir(output_directory, exists_ok=True, parents=True)
    for d in logging_dirs.values():
        vct_utils.mkdir(d, parents=True)

    return output_directory, logging_dirs


def chunks(l: Sequence, n: int):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i : i + n]


def check_for_empty_results_dir(output_dir: Path):
    """Remove the results directory including runner and worker logs if the simulation produced no results (i.e.,
    it failed)."""
    if not (output_dir / "output.hdf").exists():
        rmtree(output_dir)
