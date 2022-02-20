"""
==================
Results Processing
==================

Tools for processing and writing results.

"""
import time
from pathlib import Path
from typing import List, Tuple, Union

import numpy as np
import pandas as pd
from loguru import logger

from vivarium_cluster_tools import utilities as vct_utils


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
    results_to_write = _concat_results(written_results, new_results_to_write)

    start = time.time()
    _safe_write_results(results_to_write, output_directory / "output.hdf")
    end = time.time()
    logger.info(f"Updated output.hdf in {end - start:.4f}s.")
    return results_to_write, unwritten_results


def _concat_results(
    old_results: pd.DataFrame, new_results: List[pd.DataFrame]
) -> pd.DataFrame:
    # Skips all the pandas index checking because columns are in the same order.
    start = time.time()

    to_concat = [d.reset_index(drop=True) for d in new_results]
    if not old_results.empty:
        to_concat += [old_results.reset_index(drop=True)]

    results = _concat_preserve_types(to_concat)

    end = time.time()
    logger.info(f"Concatenated {len(new_results)} results in {end - start:.2f}s.")
    return results


def _concat_preserve_types(df_list: List[pd.DataFrame]) -> pd.DataFrame:
    """Concatenation preserves all ``numpy`` dtypes but does not preserve any
    pandas specific dtypes (e.g., categories become objects."""
    dtypes = df_list[0].dtypes
    columns_by_dtype = [list(dtype_group.index) for _, dtype_group in dtypes.groupby(dtypes)]

    splits = []
    for columns in columns_by_dtype:
        slices = [df.filter(columns) for df in df_list]
        splits.append(pd.DataFrame(data=np.concatenate(slices), columns=columns))
    return pd.concat(splits, axis=1)


@vct_utils.backoff_and_retry(backoff_seconds=30, num_retries=3, log_function=logger.warning)
def _safe_write_results(results: pd.DataFrame, output_path: Union[str, Path]) -> None:
    # Writing to a hdf over and over balloons the file size so
    # write to new file and move it over to avoid
    temp_output_path = output_path.with_name(output_path.name + "update")
    results.to_hdf(temp_output_path, "data")
    temp_output_path.replace(output_path)
