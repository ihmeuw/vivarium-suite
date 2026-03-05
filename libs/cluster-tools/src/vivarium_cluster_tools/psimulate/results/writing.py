"""
================
Results Writing
================

Simple per-task result writing. The workflow script serializes metadata JSON files for the worker to pick up.
Each worker writes one parquet file per metric directly to the results directory.

Directory structure::

    results/
        metadata/
            {task_id}.json
        {metric_name}/
            {task_id}.parquet

Reading all results for a metric is simply ``pd.read_parquet(results_dir / metric_name)``,
which automatically combines all parquet files in the directory.

Task completion is determined by the existence of result parquet files.
Metadata for completed tasks is read from the metadata JSON
files in the metadata directory.

"""

import json
from pathlib import Path

import pandas as pd
from loguru import logger
from vivarium.framework import utilities

from vivarium_cluster_tools.psimulate.jobs import JobParameters


def write_metadata(
    metadata_dir: Path,
    job_parameters: JobParameters,
) -> None:
    """Write a metadata JSON file for a single task.

    The metadata file serializes the job parameters for the workhorse script to pick up,
    and also serves as the reference for restart and expand metadata.

    Parameters
    ----------
    metadata_dir
        Directory to write the metadata file.
    job_parameters
        The job parameters for this task.
    """
    spec_path = metadata_dir / f"{job_parameters.task_id}.json"
    with open(spec_path, "w") as f:
        json.dump(job_parameters.to_dict(), f, default=str)


def write_task_results(
    results_dir: Path,
    job_parameters: JobParameters,
    results_dict: dict[str, pd.DataFrame],
) -> None:
    """Write a single task's results directly to the results directory.

    Parameters
    ----------
    results_dir
        The results directory (e.g., ``output_root/results``).
    job_parameters
        The job parameters for this task.
    results_dict
        Dictionary mapping metric names to results DataFrames.
    """
    # Write one parquet per metric, injecting job-specific columns
    for metric, df in results_dict.items():
        metric_dir = results_dir / metric
        metric_dir.mkdir(parents=True, exist_ok=True)
        for key, val in utilities.collapse_nested_dict(job_parameters.job_specific):
            col_name = key.split(".")[-1]
            df.insert(df.shape[1] - 1, col_name, val)
        df.to_parquet(metric_dir / f"{job_parameters.task_id}.parquet")


def get_completed_task_ids(results_dir: Path) -> set[str]:
    """Get task IDs that have result parquet files.

    Scans all subdirectories of ``results_dir`` for ``.parquet`` files
    and extracts the task IDs from their filenames (stems).

    Parameters
    ----------
    results_dir
        The results directory.

    Returns
    -------
        Set of task IDs with at least one result parquet file.
    """
    if not results_dir.exists():
        return set()
    task_ids: set[str] = set()
    for subdir in results_dir.iterdir():
        if subdir.is_dir():
            for parquet_file in subdir.glob("*.parquet"):
                task_ids.add(parquet_file.stem)
    return task_ids


def collect_metadata(metadata_dir: Path, results_dir: Path) -> pd.DataFrame:
    """Collect metadata for completed tasks.

    Determines which tasks completed by scanning for result parquet files
    in ``results_dir``, then reads the corresponding metadata JSON files
    from ``metadata_dir`` to build the metadata DataFrame.

    Parameters
    ----------
    metadata_dir
        The directory containing pre-written metadata JSON files
        (one per task, written by the workflow builder).
    results_dir
        The results directory containing metric subdirectories with
        parquet files.

    Returns
    -------
        Combined metadata DataFrame with flattened job-specific parameters,
        or an empty DataFrame if no completed tasks exist.
    """
    completed_task_ids = get_completed_task_ids(results_dir)
    if not completed_task_ids:
        return pd.DataFrame()

    rows = []
    for task_id in sorted(completed_task_ids):
        metadata_path = metadata_dir / f"{task_id}.json"
        if not metadata_path.exists():
            logger.warning(
                f"Metadata JSON for completed task {task_id} not found at {metadata_path}"
            )
            continue
        with open(metadata_path) as f:
            job_params = json.load(f)
        # Build flattened job_specific dict matching what already_complete() expects
        job_specific = {
            **job_params.get("branch_configuration", {}),
            "input_draw": job_params["input_draw"],
            "random_seed": job_params["random_seed"],
        }
        row = dict(utilities.collapse_nested_dict(job_specific))
        rows.append(row)
    return pd.DataFrame(rows)
