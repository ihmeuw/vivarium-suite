"""
==================
Results Processing
==================

Tools for processing and writing results.

"""

import time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from loguru import logger

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate.paths import OutputPaths

INITIAL_SAMPLE_ROWS = 1000


class OutputFileMap:
    """Tracks current output file number per metric during results writing.

    This is a mutable state mapping that gets updated in place as output files rotate.

    Attributes
    ----------
    results_dir
        Directory containing metric subdirectories with chunked output files.
    output_file_size
        Target size in bytes for each output file.
    metrics
        Dictionary mapping metric name to current file number. Defaults to 0 for new metrics.
    """

    def __init__(
        self,
        results_dir: Path,
        output_file_size: int,
        metrics: dict[str, int] | None = None,
    ) -> None:
        self.results_dir = results_dir
        self.output_file_size = output_file_size
        self.metrics: defaultdict[str, int] = defaultdict(int)
        self._bytes_per_row: dict[str, float] = {}
        if metrics is not None:
            self.metrics.update(metrics)

    @classmethod
    def from_existing_results(
        cls, results_dir: Path, output_file_size: int
    ) -> "OutputFileMap":
        """Create OutputFileMap by scanning existing chunked output files.

        Parameters
        ----------
        results_dir
            Directory containing metric subdirectories with chunked output files.
        output_file_size
            Target size in bytes for each output file.

        Returns
        -------
            OutputFileMap initialized with current output file numbers per metric.
        """
        metrics: dict[str, int] = {}

        if not results_dir.exists():
            return cls(results_dir, output_file_size, metrics)

        for metric_dir in results_dir.iterdir():
            if not metric_dir.is_dir():
                continue

            existing_output_files = sorted(metric_dir.glob("[0-9]*.parquet"))
            if existing_output_files:
                metrics[metric_dir.name] = int(existing_output_files[-1].stem)

        return cls(results_dir, output_file_size, metrics)

    def get_path(self, metric: str) -> Path:
        """Get full path to current chunk file for a metric."""
        output_file_num = self.metrics[metric]
        return self.results_dir / metric / f"{output_file_num:04d}.parquet"

    def __getitem__(self, metric: str) -> int:
        """Get current file number for a metric."""
        return self.metrics[metric]

    def __setitem__(self, metric: str, output_file_num: int) -> None:
        """Set file number for a metric."""
        self.metrics[metric] = output_file_num

    def __contains__(self, metric: str) -> bool:
        """Check if metric is tracked."""
        return metric in self.metrics

    def bytes_per_row(self, metric: str) -> float:
        """Get estimated bytes per row for a metric.

        While still filling the initial output file (0000.parquet), we dynamically
        recalculate from actual file size. Once that file is full (i.e., we've
        moved to file 0001 or higher), the estimate is locked in.

        Parameters
        ----------
        metric
            The metric name.

        Returns
        -------
            Estimated bytes per row.
        """
        # If we've already locked in an estimate, use it
        if metric in self._bytes_per_row:
            return self._bytes_per_row[metric]

        output_file_0_path = self.results_dir / metric / "0000.parquet"

        # Calculate dynamically from data in file 0
        if output_file_0_path.exists():
            rows: int = pq.read_metadata(output_file_0_path).num_rows
            if rows > 0:
                bytes_per_row = output_file_0_path.stat().st_size / rows
                if self.metrics[metric] > 0:
                    # If we've moved past file 0, lock in the estimate
                    self._bytes_per_row[metric] = bytes_per_row
                return bytes_per_row

        # No data yet - use initial estimate
        return self.output_file_size / INITIAL_SAMPLE_ROWS


def write_results_batch(
    output_paths: OutputPaths,
    existing_metadata: pd.DataFrame,
    unwritten_metadata: list[pd.DataFrame],
    unwritten_results: list[dict[str, pd.DataFrame]],
    batch_size: int,
    output_file_map: OutputFileMap,
    output_file_size: int,
) -> tuple[pd.DataFrame, list[pd.DataFrame], list[dict[str, pd.DataFrame]]]:
    """Write batch of results and finished simulation metadata to disk.

    Results are written to chunked parquet files within per-metric directories
    (e.g., ``results_dir/metric_name/0000.parquet``). When a file
    exceeds ``output_file_size`` bytes, a new file is started.

    To read all results for a metric, use ``pd.read_parquet(results_dir / metric_name)``,
    which automatically combines all parquet files in the directory.

    Parameters
    ----------
    output_paths
        Container class with output filepaths as attributes.
    existing_metadata
        Metadata for finished simulations that has already been written to disk.
    unwritten_metadata
        Metadata for finished simulations that has not yet been written to disk.
    unwritten_results
        Results for finished simulations that have not yet been written to disk.
    batch_size
        Number of results to write in this batch.
    output_file_map
        Dictionary mapping metric names to current file numbers. Updated in place.
    output_file_size
        Maximum file size in bytes. When exceeded, a new file is started.

    Returns
    -------
        A tuple of:
        - The updated metadata that was written to disk as part of this batch.
        - The unwritten metadata remaining to write in subsequent batches.
        - The unwritten results remaining to write in subsequent batches.
    """
    logger.info(f"Writing batch of {batch_size} results.")
    new_metadata_to_write, unwritten_metadata = (
        unwritten_metadata[:batch_size],
        unwritten_metadata[batch_size:],
    )
    new_results_to_write, unwritten_results = (
        unwritten_results[:batch_size],
        unwritten_results[batch_size:],
    )
    metadata_to_write = _concat_metadata(existing_metadata, new_metadata_to_write)
    results_to_write = _combine_batch_results(new_results_to_write)

    start = time.time()
    # Write results to files per metric
    for metric, new_df in results_to_write.items():
        (output_paths.results_dir / metric).mkdir(exist_ok=True)
        _write_output_file_per_metric(
            new_data=new_df,
            output_file_map=output_file_map,
            metric=metric,
            output_file_size=output_file_size,
        )
    # Metadata is small enough to overwrite entirely each time
    _safe_write(metadata_to_write, output_paths.finished_sim_metadata)
    end = time.time()
    logger.info(f"Updated results in {end - start:.4f}s.")
    return metadata_to_write, unwritten_metadata, unwritten_results


def _write_output_file_per_metric(
    new_data: pd.DataFrame,
    output_file_map: OutputFileMap,
    metric: str,
    output_file_size: int,
) -> None:
    """Write new data to chunk file(s), splitting across files if needed.

    Data may be split across multiple chunked output files to respect output_file_size limits.
    Each file will be approximately output_file_size bytes or smaller.
    """
    remaining = new_data.reset_index(drop=True)

    while not remaining.empty:
        output_file_path = output_file_map.get_path(metric)
        output_file_exists = output_file_path.exists()

        if output_file_exists:
            current_size = output_file_path.stat().st_size
            remaining_space = max(0, output_file_size - current_size)
        else:
            remaining_space = output_file_size

        rows_that_fit = int(remaining_space / output_file_map.bytes_per_row(metric))
        if rows_that_fit < 1:
            single_row_too_large = output_file_map.bytes_per_row(metric) > output_file_size
            file_empty = not output_file_exists or remaining_space == output_file_size

            if single_row_too_large and file_empty:
                # If the file size is smaller than estimated bytes per row,
                # we have no choice but to write one row per output file
                logger.warning(
                    f"Estimated bytes per row for metric '{metric}' "
                    f"({output_file_map.bytes_per_row(metric):.2f} bytes) "
                    f"exceeds output file size ({output_file_size} bytes). "
                    f"Writing one row per output file."
                )
                rows_that_fit = 1
            else:
                # No space left in current output file, rotate to next chunk
                output_file_map[metric] += 1
                continue

        to_write = remaining.iloc[:rows_that_fit].reset_index(drop=True)
        remaining = remaining.iloc[rows_that_fit:].reset_index(drop=True)

        # Combine with existing data if present
        if output_file_exists:
            combined = _concat_preserve_types(
                [pd.read_parquet(output_file_path).reset_index(drop=True), to_write]
            )
        else:
            combined = to_write

        _safe_write(combined, output_file_path)


def _concat_metadata(
    old_metadata: pd.DataFrame, new_metadata: list[pd.DataFrame]
) -> pd.DataFrame:
    """Concatenate the new metadata to the old."""
    start = time.time()

    to_concat_old = [old_metadata.reset_index(drop=True)] if not old_metadata.empty else []
    to_concat = to_concat_old + [df.reset_index(drop=True) for df in new_metadata]

    updated = _concat_preserve_types(to_concat)

    end = time.time()
    logger.info(f"Concatenated {len(new_metadata)} metadata in {end - start:.2f}s.")
    return updated


def _combine_batch_results(
    new_results: list[dict[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    """Concatenate only the new batch results (not existing results on disk).

    This function combines the results from the current batch into a single
    DataFrame per metric, without loading any existing results from disk.
    """
    start = time.time()
    results = {}
    metrics = {key for new in new_results for key in new.keys()}
    for metric in metrics:
        to_concat = [
            df.reset_index(drop=True)
            for result in new_results
            for met, df in result.items()
            if met == metric
        ]
        results[metric] = _concat_preserve_types(to_concat)

    end = time.time()
    logger.info(f"Concatenated {len(new_results)} results in {end - start:.2f}s.")
    return results


def _concat_preserve_types(df_list: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate datasets and preserve all ``numpy`` dtypes.

    This does not preserve any pandas-specific dtypes, e.g. categories become
    objects). We assume that all dataframes in the list have identical columns
    and dtypes to the first.
    """
    dtype_mapping = {col: df_list[0][col].dtype for col in df_list[0].columns}
    for df in df_list:
        for col, dtype in dtype_mapping.items():
            df[col] = df[col].astype(dtype)
    return pd.concat(df_list, axis=0).reset_index(drop=True)


@vct_utils.backoff_and_retry(backoff_seconds=30, num_retries=3, log_function=logger.warning)
def _safe_write(df: pd.DataFrame, output_path: Path) -> None:
    """Write a dataframe to disk, retrying if there are any issues.

    Parameters
    ----------
    df
        DataFrame to write to disk.
    output_path
        Path to write the DataFrame to.

    Raises
    ------
    NotImplementedError
        If the file extension is not supported.
    """
    # Writing to some file types over and over balloons the file size so
    # write to new file and move it over to avoid
    temp_output_path = output_path.with_name(
        output_path.stem + "_update" + output_path.suffix
    )
    if output_path.suffix == ".parquet":
        df.to_parquet(temp_output_path)
    elif output_path.suffix == ".csv":
        df.to_csv(temp_output_path, index=False)
    else:
        raise NotImplementedError(f"Writing to {output_path.suffix} is not supported.")
    temp_output_path.replace(output_path)
