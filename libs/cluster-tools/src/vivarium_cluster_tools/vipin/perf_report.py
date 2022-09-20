"""
=====================
Performance Reporting
=====================

Tools for summarizing and reporting performance information.

"""
import json
import re
from pathlib import Path
from typing import Tuple, Union

import numpy as np
import pandas as pd
import requests
from loguru import logger
from pandas import json_normalize

BASE_PERF_INDEX_COLS = ["host", "job_number", "task_number", "draw", "seed"]

# The number of scenario columns beyond which we shorten the scenarios to a single string
COMPOUND_SCENARIO_COL_COUNT = 2

# Scenario columns that are not useful describe a scenario or are duplicated
EXTRANEOUS_SCENARIO_COLS = [
    "scenario_run_configuration_run_id",
    "scenario_run_configuration_results_directory",
    "scenario_run_configuration_run_key_input_draw",
    "scenario_run_configuration_run_key_random_seed",
    "scenario_randomness_random_seed",
    "scenario_randomness_additional_seed",
    "scenario_input_data_input_draw_number",
]


class PerformanceSummary:
    """
    A class to implement a getter for data in the workers' performance logs.

    Given a Path, a PerformanceSummary class provides a generator to get at each
    entry in the workers' performance logs. The class also provides a method
    to get all entries in a pd.DataFrame. This class is intended as a singleton
    to provide data about a single Vivarium simulation run.

    Attributes
    ----------
    log_dir
        Path of log_dir

    """

    def __init__(self, log_dir: Path):
        self.log_dir: Path = log_dir
        self.errors: int = 0

    def get_summaries(self) -> dict:
        """Generator to get all performance summary log messages in PerformanceSummary"""
        for log in [
            f for f in self.log_dir.iterdir() if self.PERF_LOG_PATTERN.fullmatch(f.name)
        ]:
            with log.open("r") as f:
                count: int = 0
                for line in f.readlines():
                    count += 1
                    try:
                        message = json.loads(line)["record"]["message"]
                    except Exception as e:
                        logger.warning(
                            f"Exception: {e}. Malformed message in {log} line {count}, skipping..."
                        )
                        self.errors += 1
                        continue
                    m = self.TELEMETRY_PATTERN.fullmatch(str(message))
                    if m:
                        yield json_normalize(json.loads(message), sep="_")

    def to_df(self) -> pd.DataFrame:
        perf_data = []
        for item in self.get_summaries():
            perf_data.append(item)
        if len(perf_data) < 1:
            return pd.DataFrame()
        perf_df = pd.concat(perf_data)

        # Convert the Unix timestamps to datetimes
        for col in [col for col in perf_df.columns if col.startswith("event_")]:
            perf_df[col] = pd.to_datetime(perf_df[col], unit="s")

        # Remove trailing "_scenario" from normalized label
        perf_df.columns = perf_df.columns.str.replace("_scenario", "", regex=False)
        return perf_df

    TELEMETRY_PATTERN = re.compile(r"^{\"host\".+\"job_number\".+}$")
    PERF_LOG_PATTERN = re.compile(r"^perf\.([0-9]+)\.([0-9]+)\.log$")


def set_index_scenario_cols(perf_df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    """Get the columns useful to index performance data by."""
    index_cols = BASE_PERF_INDEX_COLS
    perf_df = perf_df.drop(EXTRANEOUS_SCENARIO_COLS, axis=1)
    scenario_cols = [col for col in perf_df.columns if col.startswith("scenario_")]
    index_cols.extend(scenario_cols)
    perf_df = perf_df.set_index(index_cols)
    return perf_df, scenario_cols


def add_squid_api_data(perf_df: pd.DataFrame):
    """Given a dataframe from PerformanceSummary.to_df, add Squid API data for the job.
    Squid API reference: https://hub.ihme.washington.edu/display/SCKB/How+to+use+Squid+API
    """
    try:
        job_numbers = perf_df["job_number"].unique()
        assert len(job_numbers) == 1
        squid_api_data = requests.get(
            f"http://squid.ihme.washington.edu/api/jobs?job_ids={job_numbers[0]}"
        ).json()
        squid_api_df = pd.DataFrame(squid_api_data["jobs"]).add_prefix("squid_api_")
        perf_df = perf_df.astype({"job_number": np.int64})
        perf_df = perf_df.merge(
            squid_api_df,
            left_on=["job_number"],
            right_on=["squid_api_job_id"],
        )
    except Exception as e:
        print(f"Squid API request failed with: {e}")
    return perf_df


def print_stat_report(perf_df: pd.DataFrame, scenario_cols: list):
    """Print some helpful stats from the performance data, grouped by scenario_cols"""
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_columns", None)
    pd.options.display.float_format = "{:.2f}".format

    do_compound = len(scenario_cols) > COMPOUND_SCENARIO_COL_COUNT

    perf_df = perf_df.reset_index()

    if do_compound:
        logger.info(
            f"compound scenario:\n({'/'.join([s.replace('scenario_', '') for s in scenario_cols])}):"
        )
        perf_df["compound_scenario"] = (
            perf_df[scenario_cols]
            .to_csv(header=None, index=False, sep="/")
            .strip("\n")
            .split("\n")
        )

    # Print execution times stats by scenario
    temp = (
        perf_df.set_index("compound_scenario" if do_compound else scenario_cols)
        .filter(like="exec_time_")
        .stack()
        .reset_index()
    )

    if do_compound:
        cols = ["compound_scenario", "measure", "value"]
    else:
        cols = scenario_cols
        cols.extend(["measure", "value"])

    temp.columns = cols
    cols.remove("value")

    report_df = temp.groupby(cols).describe()
    report_df.columns = report_df.columns.droplevel()
    report_df = report_df.drop(["count", "25%", "50%", "75%"], axis=1)
    report_df = report_df.reset_index()

    # Abbreviate execution time measures for printing
    report_df["measure"] = report_df["measure"].replace("^exec_time_", "", regex=True)
    report_df["measure"] = report_df["measure"].replace(
        "^simulant_initialization", "sim_init", regex=True
    )
    report_df["measure"] = report_df["measure"].replace("minutes$", "min", regex=True)
    report_df["measure"] = report_df["measure"].replace("seconds", "s", regex=True)

    report_df = report_df.set_index(cols).sort_index()
    logger.info(f"\n{report_df}")


def report_performance(
    input_directory: Union[Path, str],
    output_directory: Union[Path, str],
    output_hdf: bool,
    verbose: int,
):
    """Main method for vipin reporting. Gets job performance data, outputs to a file, and logs a report."""
    input_directory, output_directory = Path(input_directory), Path(output_directory)
    perf_summary = PerformanceSummary(input_directory)

    perf_df = perf_summary.to_df()
    if len(perf_df) < 1:
        logger.warning(f"No performance data found in {input_directory}.")
        return  # nothing left to do

    # Add jobapi data about the job to dataframe
    perf_df = add_squid_api_data(perf_df)

    # Set index to include branch configuration/scenario columns
    perf_df, scenario_cols = set_index_scenario_cols(perf_df)

    # Write to file
    out_file = output_directory / "log_summary"
    if output_hdf:
        out_file = out_file.with_suffix(".hdf")
        perf_df.to_hdf(out_file, key="worker_data")
    else:
        out_file = out_file.with_suffix(".csv")
        perf_df.to_csv(out_file)

    if verbose:
        print_stat_report(perf_df, scenario_cols)

    if perf_summary.errors > 0:
        logger.warning(
            f'{perf_summary.errors} log row{"s were" if perf_summary.errors > 1 else " was"} unreadable.'
        )
    logger.info(
        f'Performance summary {"hdf" if output_hdf else "csv"} can be found at {out_file}, with '
        f'{perf_df.shape[0]} row{"s" if perf_df.shape[0] > 1 else ""}.'
    )
    return
