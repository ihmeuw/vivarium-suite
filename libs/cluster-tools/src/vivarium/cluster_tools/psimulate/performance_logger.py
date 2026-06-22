import glob
import json
from pathlib import Path

import pandas as pd
from loguru import logger

from vivarium.cluster_tools.psimulate.paths import (
    CENTRAL_PERFORMANCE_LOGS_DIRECTORY,
    OutputPaths,
)
from vivarium.cluster_tools.utilities import NUM_ROWS_PER_CENTRAL_LOG_FILE

# Central log files are named ``log_summary_<NNNN>.csv``.
LOG_FILE_PREFIX = "log_summary_"

# The pinned column set and order for every central performance log file. Pinned because
# the append in append_child_job_data is positional and headerless, so any drift in a
# run's column set or order would silently corrupt the file. Extend this list
# deliberately (and let a new file roll) when the recorded data genuinely changes.
CENTRAL_LOG_SCHEMA = [
    "host",
    "job_number",
    "task_number",
    "draw",
    "seed",
    "job_hash",
    "event_start",
    "event_simulant_initialization_start",
    "event_simulation_start",
    "event_results_start",
    "event_end",
    "exec_time_setup_minutes",
    "exec_time_simulant_initialization_minutes",
    "exec_time_main_loop_minutes",
    "exec_time_step_mean_seconds",
    "exec_time_results_minutes",
    "exec_time_total_minutes",
    "counters_cpu_ctx_switches",
    "counters_cpu_interrupts",
    "counters_cpu_soft_interrupts",
    "counters_cpu_syscalls",
    "counters_freq_current",
    "counters_freq_min",
    "counters_freq_max",
    "counters_disk_read_count",
    "counters_disk_write_count",
    "counters_disk_read_bytes",
    "counters_disk_write_bytes",
    "counters_disk_read_time",
    "counters_disk_write_time",
    "counters_disk_read_merged_count",
    "counters_disk_write_merged_count",
    "counters_disk_busy_time",
    "counters_net_bytes_sent",
    "counters_net_bytes_recv",
    "counters_net_packets_sent",
    "counters_net_packets_recv",
    "counters_net_errin",
    "counters_net_errout",
    "counters_net_dropin",
    "counters_net_dropout",
    "counters_time",
    "scenario_parameters",
    "artifact_name",
]


def transform_perf_df_for_appending(
    perf_df: pd.DataFrame, output_paths: OutputPaths
) -> pd.DataFrame:
    """Transform performance DataFrame for appending to central logs.

    Take performance dataframe from performance report and 1) turn index into columns so
    we can write to csv, 2) add artifact name column, and 3) aggregate scenario information
    into one column.

    Parameters
    ----------
    perf_df
        DataFrame pulled from performance report with index values uniquely identifying each child
        job and column values containing their performance data.
    output_paths
        OutputPaths object containing information about the results directory.

    Returns
    -------
        The transformed DataFrame with a simple RangeIndex, the index values as columns, a
        new artifact name column, and a new scenario parameters column. It still carries its
        raw, producer-defined columns; append_child_job_data reindexes it to
        CENTRAL_LOG_SCHEMA before writing.
    """
    central_perf_df = perf_df.reset_index()
    # add artifact name to central_perf_df
    # TODO: [MIC-4859] refer to key from job_parameters from worker/vivarium_work_horse.py
    #  instead of 'scenario'
    artifact_path_col = "scenario_input_data_artifact_path"
    if (
        artifact_path_col in central_perf_df.columns
    ):  # if we parallelized across artifact paths
        central_perf_df["artifact_name"] = central_perf_df[artifact_path_col].apply(
            lambda filepath: Path(filepath).stem
        )
    else:  # else get from output directory
        central_perf_df["artifact_name"] = output_paths.artifact_name

    ## aggregate scenario information into one column
    all_scenario_cols = [
        col for col in central_perf_df.columns if col.startswith("scenario_")
    ]

    scenario_parameters = central_perf_df[all_scenario_cols].to_dict(orient="records")
    central_perf_df["scenario_parameters"] = pd.Series(scenario_parameters).apply(json.dumps)

    central_perf_df = central_perf_df.drop(all_scenario_cols, axis=1)

    return central_perf_df


def _align_to_schema(performance_data: pd.DataFrame) -> pd.DataFrame:
    """Reindex performance data to the central log schema, warning on any mismatch.

    Drop any columns absent from CENTRAL_LOG_SCHEMA and fill any it is missing with
    NaN, so the result always matches the pinned column set and order. A mismatch
    means the producing code has drifted from the schema; log a warning naming the
    offending columns so the drift surfaces and CENTRAL_LOG_SCHEMA can be updated
    deliberately rather than letting the central logs grow an open-ended set.
    """
    unexpected = [
        column for column in performance_data.columns if column not in CENTRAL_LOG_SCHEMA
    ]
    missing = [
        column for column in CENTRAL_LOG_SCHEMA if column not in performance_data.columns
    ]
    if unexpected or missing:
        logger.warning(
            "Performance data columns do not match the central log schema; "
            f"dropping unexpected columns {unexpected} and filling missing columns "
            f"{missing} with NaN. If this reflects an intended schema change, update "
            "CENTRAL_LOG_SCHEMA in performance_logger.py."
        )
    return performance_data.reindex(columns=CENTRAL_LOG_SCHEMA)


def _create_empty_log_file(file_path: str) -> None:
    """Write a central log file containing only the canonical schema header."""
    pd.DataFrame(columns=CENTRAL_LOG_SCHEMA).to_csv(file_path, index=False)


def _next_log_file_path(current_file_path: str) -> str:
    """Return the path of the log file that follows the given one in sequence.

    Assumes the argument is the highest-numbered log file, so the returned path does not
    yet exist; callers pass the most recent file, never an arbitrary one.
    """
    next_index = int(Path(current_file_path).stem.removeprefix(LOG_FILE_PREFIX)) + 1
    return str(
        CENTRAL_PERFORMANCE_LOGS_DIRECTORY
        / f"{LOG_FILE_PREFIX}{str(next_index).zfill(4)}.csv"
    )


def _header_matches_schema(file_path: str) -> bool:
    """Return whether an existing log file's header is exactly the central log schema.

    An empty or unreadable file counts as a non-match so the caller rolls past it.
    """
    try:
        header = pd.read_csv(file_path, nrows=0).columns.tolist()
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return False
    return header == CENTRAL_LOG_SCHEMA


def append_child_job_data(child_job_performance_data: pd.DataFrame) -> str:
    """Append child job data and return name of first file containing this data.

    Parameters
    ----------
    child_job_performance_data
        DataFrame pulled from transform_perf_df_for_appending.

    Returns
    -------
       The first file in our central logs containing child job data.
    """
    log_files = glob.glob(
        CENTRAL_PERFORMANCE_LOGS_DIRECTORY.as_posix() + f"/{LOG_FILE_PREFIX}*.csv"
    )

    if log_files:
        most_recent_file_path = sorted(log_files)[-1]
    else:
        # No central logs exist yet: bootstrap the first file with the canonical header.
        most_recent_file_path = str(
            CENTRAL_PERFORMANCE_LOGS_DIRECTORY / f"{LOG_FILE_PREFIX}0000.csv"
        )
        _create_empty_log_file(most_recent_file_path)

    # The append below is positional, so it is only safe when the target file's header
    # is already the canonical schema. If the most recent file predates the schema, roll
    # to a fresh file rather than corrupting it with rows in a different column order.
    if not _header_matches_schema(most_recent_file_path):
        logger.warning(
            f"Most recent central log file {most_recent_file_path} does not match the "
            "current schema; starting a new log file rather than appending to it."
        )
        most_recent_file_path = _next_log_file_path(most_recent_file_path)
        _create_empty_log_file(most_recent_file_path)

    first_file_with_data = most_recent_file_path

    # The most recent file now matches the schema exactly (validated above or freshly
    # created), so reindexing incoming data to CENTRAL_LOG_SCHEMA order makes the
    # positional append land correctly.
    child_job_performance_data = _align_to_schema(child_job_performance_data)

    while len(child_job_performance_data) != 0:
        child_job_performance_data = child_job_performance_data.reset_index(drop=True)
        with open(most_recent_file_path) as f:
            # Subtract 1 for the header row
            num_existing_rows = sum(1 for _ in f) - 1

        available_rows = NUM_ROWS_PER_CENTRAL_LOG_FILE - num_existing_rows
        rows_to_append = min(len(child_job_performance_data), available_rows)

        data_to_append = child_job_performance_data[:rows_to_append]
        data_to_append.to_csv(most_recent_file_path, mode="a", header=False, index=False)
        child_job_performance_data.drop(data_to_append.index, inplace=True)

        if rows_to_append == available_rows:
            most_recent_file_path = _next_log_file_path(most_recent_file_path)
            _create_empty_log_file(most_recent_file_path)

    return first_file_with_data


def generate_runner_job_data(
    job_number: int, output_paths: OutputPaths, first_file_with_data: str
) -> pd.DataFrame:
    """Create runner job data to append to central logs.

    Parameters
    ----------
    job_number
        The job number for our runner job.
    output_paths
        OutputPaths object containing information about the results directory.
    first_file_with_data
        The first file in our central logs containing child job data
        launched by our runner job.
    """
    runner_data = pd.DataFrame({"job_number": job_number}, index=[0])
    runner_data["project_name"] = output_paths.project_name
    runner_data["root_path"] = output_paths.root_path
    runner_data["original_run_date"] = output_paths.original_run_date
    runner_data["run_date"] = output_paths.run_date
    runner_data["run_type"] = output_paths.run_type
    runner_data["log_summary_file_path"] = first_file_with_data
    runner_data["original_log_file_path"] = (
        output_paths.worker_logging_root / "log_summary.csv"
    ).as_posix()

    return runner_data


def append_perf_data_to_central_logs(
    perf_df: pd.DataFrame, output_paths: OutputPaths
) -> None:
    """Append performance data to the central logs.

    This consists of child job data and runner data. The child job data will contain
    performance information and identifying information for each child job and the
    runner data will contain data about the runner job that launched these child jobs.

    Parameters
    ----------
    perf_df
        DataFrame pulled from performance report.
    output_paths
        OutputPaths object containing information about the results directory.
    """
    if not output_paths.logging_to_central_results_directory:
        logger.warning(
            f"Log path {output_paths.worker_logging_root} not in central results directory. Skipping appending central performance logs."
        )
        return

    child_job_performance_data = transform_perf_df_for_appending(perf_df, output_paths)
    job_number = int(child_job_performance_data["job_number"].unique().squeeze())
    first_file_with_data = append_child_job_data(child_job_performance_data)

    runner_data = generate_runner_job_data(job_number, output_paths, first_file_with_data)
    runner_data_file = CENTRAL_PERFORMANCE_LOGS_DIRECTORY / "runner_data.csv"
    runner_data.to_csv(runner_data_file, mode="a", header=False, index=False)
