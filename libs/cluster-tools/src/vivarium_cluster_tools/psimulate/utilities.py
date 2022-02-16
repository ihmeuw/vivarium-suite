"""
===================
psimulate Utilities
===================

Utilities for psimulate runs.

"""
from datetime import datetime
from pathlib import Path
from shutil import rmtree
from typing import Dict, Sequence, Tuple

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate import globals as vct_globals


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
