"""
==============
psimulate Jobs
==============

"""
from pathlib import Path
from typing import List, NamedTuple, Tuple

import numpy as np
import pandas as pd
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate import COMMANDS, branches


class JobParameters(NamedTuple):
    """Parameters for a single distributed simulation job."""

    model_specification: str
    branch_configuration: dict
    input_draw: int
    random_seed: int
    results_path: str
    extras: dict

    @property
    def shared(self) -> dict:
        """Parameters shared by all jobs in a psimulate run."""
        return {
            "model_specification": self.model_specification,
            "results_path": self.results_path,
        }

    @property
    def job_specific(self) -> dict:
        """Parameters that vary by job in a psimulate run."""
        return {
            "input_draw": self.input_draw,
            "random_seed": self.random_seed,
            **self.branch_configuration,
        }

    def to_dict(self) -> dict:
        # I will never understand why this is a private
        # method of named tuples.
        return self._asdict()

    def __repr__(self) -> str:
        return (
            f"({self.input_draw}, {self.random_seed}, "
            f"{self.model_specification}, {self.branch_configuration})"
        )


def build_job_list(
    command: str,
    model_specification_path: Path,
    output_root: Path,
    keyspace: branches.Keyspace,
    existing_outputs: pd.DataFrame,
    extras: dict,
) -> Tuple[List[dict], int]:
    jobs = []
    number_already_completed = 0

    if command in [COMMANDS.run, COMMANDS.restart, COMMANDS.expand]:
        for (input_draw, random_seed, branch_config) in keyspace:
            parameters = JobParameters(
                model_specification=str(model_specification_path),
                branch_configuration=branch_config,
                input_draw=int(input_draw),
                random_seed=int(random_seed),
                results_path=str(output_root),
                extras={},
            )

            if already_complete(parameters, existing_outputs):
                number_already_completed += 1
            else:
                jobs.append(parameters.to_dict())
    else:
        assert command == COMMANDS.load_test
        for i in range(extras["num_workers"]):
            parameters = JobParameters(
                model_specification=str(model_specification_path),
                branch_configuration={},
                input_draw=0,
                random_seed=i,
                results_path=str(output_root),
                extras={"test_type": extras["test_type"]},
            )
            jobs.append(parameters.to_dict())

    np.random.shuffle(jobs)
    return jobs, number_already_completed


def already_complete(job_parameters: JobParameters, existing_outputs: pd.DataFrame) -> bool:
    if existing_outputs.empty:
        return False

    job_parameter_list = collapse_nested_dict(job_parameters.branch_configuration)
    job_parameter_list.extend(
        [
            ("run_configuration.run_key.input_draw", job_parameters.input_draw),
            ("run_configuration.run_key.random_seed", job_parameters.random_seed),
        ]
    )

    mask = pd.Series(True, index=existing_outputs.index)
    for k, v in job_parameter_list:
        if isinstance(v, float):
            mask &= np.isclose(existing_outputs[k], v)
        else:
            mask &= existing_outputs[k] == v
    return np.any(mask)
