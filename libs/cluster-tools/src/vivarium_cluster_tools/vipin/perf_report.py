import json
import re
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
import requests
from loguru import logger
from pandas.io.json import json_normalize


class PerformanceLog:
    """
    A class to implement a getter for data in the workers' performance log.

    Given a Path, a PerformanceLog clase provides a generator to get at each
    entry in the workers' performance log. The class also provides a method
    to get all entries in a pd.DataFrame. Because there is a single log for
    a run's performance, this class is intended as a singleton.

    Attributes
    ----------
    file : Path
        Path of log file

    Methods
    -------
    get_summaries():
        Generator to retrieve entries in the performance log as dict objects.
    to_df():
        Returns the performance log data as a pd.DataFrame.
    """

    def __init__(self, file: Path):
        self.file = file

    def get_summaries(self) -> dict:
        """Generator to get all performance summary log messages in PerformanceLog"""
        with self.file.open('r') as f:
            for line in f.readlines():
                message = json.loads(line)['record']['message']
                m = self.TELEMETRY_PATTERN.fullmatch(str(message))
                if m:
                    yield json_normalize(json.loads(message), sep='_')

    def to_df(self) -> pd.DataFrame:
        perf_data = []
        for item in self.get_summaries():
            perf_data.append(item)
        perf_df = pd.concat(perf_data)

        # Convert the Unix timestamps to datetimes
        for col in [col for col in perf_df.columns if col.startswith("event_")]:
            perf_df[col] = pd.to_datetime(perf_df[col], unit='s')

        # Simplify name of scenario normalized JSON label
        # TODO: add expansion of scenario values
        scenario_label = [col for col in perf_df.columns if 'scenario' in col]
        perf_df = perf_df.rename(columns={scenario_label[0]: 'scenario'})

        return perf_df

    TELEMETRY_PATTERN = re.compile(r'^{\"host\".+\"job_number\".+}$')


def report_performance(input_directory: Union[Path, str], output_directory: Union[Path, str], output_hdf: bool):
    input_directory, output_directory = Path(input_directory), Path(output_directory)
    perf_log = PerformanceLog(input_directory / 'performance.log')

    perf_df = perf_log.to_df()

    # Get jobapi data about the job
    try:
        job_numbers = perf_df['job_number'].unique()
        assert (len(job_numbers) == 1)
        jobapi_data = requests.get("http://jobapi.ihme.washington.edu/fair/queryjobids",
                                   params=[('job_number', job_numbers[0]), ('limit', 50000)]).json()
        jobapi_df = pd.DataFrame(jobapi_data["data"])
        perf_df = perf_df.astype({'job_number': np.int64, 'task_number': np.int64})
        perf_df = perf_df.merge(jobapi_df, on=['job_number', 'task_number'])
    except Exception as e:
        logger.warning(f'Job API request failed with {e}')

    perf_df = perf_df.set_index(['host', 'job_number', 'task_number', 'draw', 'seed', 'scenario'])

    logger.info("\nStatistics by scenario:")
    for col in [col for col in perf_df.columns if col.startswith('exec_time_')]:
        logger.info(f'\n>>> {col}:\n{perf_df.groupby("scenario")[col].agg(["mean", "std", "min", "max"])}')

    # Write to file
    out_file = output_directory / 'log_summary'
    if output_hdf:
        out_file = out_file.with_suffix(".hdf")
        perf_df.to_hdf(out_file, key='worker_data')
    else:
        out_file = out_file.with_suffix(".csv")
        perf_df.to_csv(out_file)
    logger.info(f'Performance summary {"hdf" if output_hdf else "csv"} can be found at {out_file}.')
