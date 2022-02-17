"""
==================
Results Management
==================

Tools for interacting with output directories and writing results.

"""
import time
from datetime import datetime
from pathlib import Path
from shutil import rmtree
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from loguru import logger

from vivarium_cluster_tools import utilities as vct_utils


def get_output_directory(
    model_specification_file: Optional[Path], output_directory: Path, restart: bool
) -> Path:
    if not restart:
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        output_directory = output_directory / model_specification_file.stem / launch_time
    return output_directory


def setup_directories(
    model_specification_file: Optional[Path], result_directory: Path, restart: bool, expand: bool
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


def check_for_empty_results_dir(output_dir: Path):
    """Remove the results directory including runner and worker logs if the simulation produced no results (i.e.,
    it failed)."""
    if not (output_dir / "output.hdf").exists():
        rmtree(output_dir)


def concat_preserve_types(df_list: List[pd.DataFrame]) -> pd.DataFrame:
    """Concatenation preserves all ``numpy`` dtypes but does not preserve any
    pandas specific dtypes (e.g., categories become objects."""
    dtypes = df_list[0].dtypes
    columns_by_dtype = [list(dtype_group.index) for _, dtype_group in dtypes.groupby(dtypes)]

    splits = []
    for columns in columns_by_dtype:
        slices = [df.filter(columns) for df in df_list]
        splits.append(pd.DataFrame(data=np.concatenate(slices), columns=columns))
    return pd.concat(splits, axis=1)


def concat_results(
    old_results: pd.DataFrame, new_results: List[pd.DataFrame]
) -> pd.DataFrame:
    # Skips all the pandas index checking because columns are in the same order.
    start = time.time()

    to_concat = [d.reset_index(drop=True) for d in new_results]
    if not old_results.empty:
        to_concat += [old_results.reset_index(drop=True)]

    results = concat_preserve_types(to_concat)

    end = time.time()
    logger.info(f"Concatenated {len(new_results)} results in {end - start:.2f}s.")
    return results


def write_results_batch(
    output_directory: Path,
    written_results: pd.DataFrame,
    unwritten_results: List[pd.DataFrame],
    batch_size: int = 50,
) -> Tuple[pd.DataFrame, List[pd.DataFrame]]:
    new_results_to_write, unwritten_results = (
        unwritten_results[:batch_size],
        unwritten_results[batch_size:],
    )
    results_to_write = concat_results(written_results, new_results_to_write)

    start = time.time()
    safe_write_results(results_to_write, output_directory / "output.hdf")
    end = time.time()
    logger.info(f"Updated output.hdf in {end - start:.4f}s.")
    return results_to_write, unwritten_results


@vct_utils.backoff_and_retry(backoff_seconds=30, num_retries=3, log_function=logger.warning)
def safe_write_results(results: pd.DataFrame, output_path: Union[str, Path]) -> None:
    # Writing to a hdf over and over balloons the file size so
    # write to new file and move it over to avoid
    temp_output_path = output_path.with_name(output_path.name + "update")
    results.to_hdf(temp_output_path, "data")
    temp_output_path.replace(output_path)
