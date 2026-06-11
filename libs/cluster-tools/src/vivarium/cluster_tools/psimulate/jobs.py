"""
==============
psimulate Jobs
==============

"""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

import numpy as np
import pandas as pd
from vivarium.engine.framework.utilities import collapse_nested_dict

from vivarium.cluster_tools.psimulate import branches


class BackupConfiguration(TypedDict):
    """Typed contract for simulation backup settings."""

    backup_dir: str | Path
    backup_freq: float | None
    backup_metadata_path: str | Path


def generate_task_id(
    input_draw: int, random_seed: int, branch_configuration: dict[str, Any]
) -> str:
    """Generate a deterministic task ID from job-specific parameters.

    Uses SHA-256 hash of canonical JSON serialization of the job-specific
    parameters (input_draw, random_seed, branch_configuration).

    Parameters
    ----------
    input_draw
        The input draw number.
    random_seed
        The random seed.
    branch_configuration
        The branch configuration dictionary.

    Returns
    -------
        A hex string of the first 8 bytes (16 hex chars) of the SHA-256 hash.
    """
    canonical = json.dumps(
        {
            "input_draw": input_draw,
            "random_seed": random_seed,
            "branch_configuration": branch_configuration,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class JobParameters(NamedTuple):
    """Parameters for a single distributed simulation job."""

    model_specification: str
    branch_configuration: dict[str, Any]
    input_draw: int
    random_seed: int
    results_path: str
    worker_logging_root: str
    backup_configuration: BackupConfiguration
    extras: dict[str, Any]

    @property
    def task_id(self) -> str:
        """Deterministic task ID derived from job-specific parameters."""
        return generate_task_id(self.input_draw, self.random_seed, self.branch_configuration)

    @property
    def shared(self) -> dict[str, Any]:
        """Parameters shared by all jobs in a psimulate run."""
        return {
            "model_specification": self.model_specification,
            "results_path": self.results_path,
            "worker_logging_root": self.worker_logging_root,
            "backup_configuration": self.backup_configuration,
        }

    @property
    def job_specific(self) -> dict[str, Any]:
        """Parameters that vary by job in a psimulate run."""
        return {
            **self.branch_configuration,
            "input_draw": self.input_draw,
            "random_seed": self.random_seed,
        }

    @property
    def sim_config(self) -> dict[str, Any]:
        """Parameters for the simulation configuration."""
        config = defaultdict(dict, deepcopy(self.branch_configuration))
        config["randomness"]["random_seed"] = self.random_seed
        config["input_data"]["input_draw_number"] = self.input_draw
        return dict(config)

    def to_dict(self) -> dict[str, Any]:
        # I will never understand why this is a private
        # method of named tuples.
        return self._asdict()

    def __repr__(self) -> str:
        return (
            f"({self.input_draw}, {self.random_seed}, "
            f"{self.model_specification}, {self.branch_configuration})"
        )


def build_job_parameters_from_keyspace(
    keyspace: branches.Keyspace,
    *,
    model_specification_path: Path,
    output_root: Path,
    worker_logging_root: Path,
    backup_configuration: BackupConfiguration | None = None,
    extras: dict[str, Any] | None = None,
) -> list[JobParameters]:
    """Build a JobParameters list from a keyspace without filtering.

    Creates one :class:`JobParameters` per (input_draw, random_seed, branch)
    combination in *keyspace*.

    Parameters
    ----------
    keyspace
        The simulation keyspace to iterate.
    model_specification_path
        Path to the model specification file.
    output_root
        Root output directory for the simulation.
    worker_logging_root
        Directory for worker log output.
    backup_configuration
        Optional backup configuration. Defaults to empty.
    extras
        Optional extra arguments dict. Defaults to empty.
    """
    job_parameters: list[JobParameters] = []
    for input_draw, random_seed, branch_config in keyspace:
        job_parameters.append(
            JobParameters(
                model_specification=str(model_specification_path),
                branch_configuration=branch_config,
                input_draw=int(input_draw),
                random_seed=int(random_seed),
                results_path=str(output_root),
                worker_logging_root=str(worker_logging_root),
                backup_configuration=backup_configuration
                if backup_configuration is not None
                else BackupConfiguration(
                    backup_dir="", backup_freq=None, backup_metadata_path=""
                ),
                extras=extras or {},
            )
        )
    return job_parameters


def build_job_list(
    model_specification_path: Path,
    output_root: Path,
    keyspace: branches.Keyspace,
    finished_sim_metadata: pd.DataFrame,
    backup_freq: int | None,
    backup_dir: Path,
    backup_metadata_path: Path,
    worker_logging_root: Path,
    extras: dict[str, Any],
) -> tuple[list[JobParameters], int]:
    all_jobs = build_job_parameters_from_keyspace(
        keyspace,
        model_specification_path=model_specification_path,
        output_root=output_root,
        worker_logging_root=worker_logging_root,
        backup_configuration=BackupConfiguration(
            backup_dir=backup_dir,
            backup_freq=backup_freq,
            backup_metadata_path=backup_metadata_path,
        ),
        extras=extras,
    )

    jobs: list[JobParameters] = []
    number_already_completed = 0
    for parameters in all_jobs:
        if already_complete(parameters, finished_sim_metadata):
            number_already_completed += 1
        else:
            jobs.append(parameters)

    np.random.default_rng().shuffle(jobs)  # type: ignore [arg-type]
    return jobs, number_already_completed


def already_complete(
    job_parameters: JobParameters, finished_sim_metadata: pd.DataFrame
) -> bool:
    if finished_sim_metadata.empty:
        return False

    job_parameter_list = collapse_nested_dict(job_parameters.job_specific)
    mask = pd.Series(True, index=finished_sim_metadata.index)
    for k, v in job_parameter_list:
        if isinstance(v, float):
            mask &= np.isclose(finished_sim_metadata[k], v)
        else:
            mask &= finished_sim_metadata[k] == v
    return bool(np.any(mask))
