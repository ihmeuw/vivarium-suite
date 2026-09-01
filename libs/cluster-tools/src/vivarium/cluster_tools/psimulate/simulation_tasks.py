"""
==========================
psimulate Simulation Tasks
==========================

The shared pipeline that turns entry-point arguments into a list of Jobmon
tasks, one per ``(input_draw, random_seed, branch)`` combination.

Both entry points that launch parallel simulations build their tasks from
here: ``psimulate run`` / ``restart`` / ``expand`` / ``test``, which wraps
the tasks in a workflow of their own, and ``dagger``'s ``simulation`` step,
which wires them into a multi-step DAG.

The pipeline is three calls rather than one so that each caller can do its
own run-level work at the points where the two genuinely differ: between
laying out the output directory and resolving what the run contains
(``psimulate`` writes its ``configuration.yaml``, starts file logging, and
validates the environment there), and between resolving the run and
building its tasks (``psimulate`` rejects a directory that already holds
results, where ``dagger`` prompts the user instead).

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd
from loguru import logger
from vivarium.engine.framework.utilities import collapse_nested_dict

from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
from vivarium.cluster_tools.psimulate import COMMANDS, branches, jobs, model_specification
from vivarium.cluster_tools.psimulate.jobmon_workflow import get_task_list
from vivarium.cluster_tools.psimulate.paths import InputPaths, OutputPaths
from vivarium.cluster_tools.psimulate.results.writing import collect_metadata

if TYPE_CHECKING:
    from jobmon.client.api import Tool
    from jobmon.client.task import Task


class SimulationRun(NamedTuple):
    """A parallel simulation run resolved against its output directory."""

    command: str
    """The psimulate command that resolved the run."""
    output_paths: OutputPaths
    """The run's output directory layout."""
    keyspace: branches.Keyspace
    """The parameter space the run covers."""
    finished_sim_metadata: pd.DataFrame
    """Metadata for the simulations a previous invocation already finished."""


class SimulationTasks(NamedTuple):
    """The Jobmon tasks for a parallel simulation run."""

    tasks: list[Task]
    """One Jobmon task per simulation still to be run."""
    num_jobs_completed: int
    """How many of the keyspace's simulations are already complete."""


def resolve_output_paths(
    *,
    command: str,
    input_paths: InputPaths,
    launch_time: str | None = None,
) -> OutputPaths:
    """Lay out a run's output directory tree and create it.

    Parameters
    ----------
    command
        The psimulate command being run.
    input_paths
        The resolved input file paths.
    launch_time
        Optional timestamp (``YYYY_MM_DD_HH_MM_SS``) naming the run's
        directories. Defaults to the current time.

    Returns
    -------
        The run's output directory layout.
    """
    output_paths = OutputPaths.from_entry_point_args(
        command=command,
        input_artifact_path=input_paths.artifact,
        result_directory=input_paths.result_directory,
        input_model_spec_path=input_paths.model_specification,
        launch_time=launch_time,
    )
    logger.debug("Setting up output directory and all subdirectories.")
    output_paths.touch()
    return output_paths


def resolve_simulation_run(
    *,
    command: str,
    input_paths: InputPaths,
    output_paths: OutputPaths,
    extra_args: dict[str, Any],
) -> SimulationRun:
    """Resolve the parameter space and model specification a run covers.

    Writes the keyspace, the expanded branches, and the resolved model
    specification into the output directory, then collects the metadata of
    any simulations a previous invocation already finished. ``restart`` and
    ``expand`` read the keyspace and model specification persisted by the
    original ``run`` instead of re-parsing the inputs, so a resumed run is
    reproducible even if the input files have since changed.

    Parameters
    ----------
    command
        The psimulate command being run.
    input_paths
        The resolved input file paths.
    output_paths
        The run's output directory layout, from
        :func:`resolve_output_paths`.
    extra_args
        Additional command-specific arguments (e.g. ``num_draws``,
        ``num_seeds``, ``num_workers``).

    Returns
    -------
        The resolved run.
    """
    if command in (COMMANDS.restart, COMMANDS.expand):
        _validate_resumable(output_paths)

    logger.debug(
        "Parsing input arguments into model specification and branches and writing to disk."
    )
    # The keyspace output is a cartesian product representation of the
    # parameter space; branches is a flat representation with the product
    # expanded out.
    if command == COMMANDS.load_test:
        keyspace = branches.Keyspace.for_load_test(extra_args["num_workers"])
    else:
        keyspace = branches.Keyspace.from_entry_point_args(
            input_branch_configuration_path=input_paths.branch_configuration,
            keyspace_path=output_paths.keyspace,
            branches_path=output_paths.branches,
            extras=extra_args,
        )
    keyspace.persist(output_paths.keyspace, output_paths.branches)

    model_spec = model_specification.parse(
        command=command,
        input_model_specification_path=input_paths.model_specification,
        artifact_path=input_paths.artifact,
        model_specification_path=output_paths.model_specification,
        results_root=output_paths.root,
        keyspace=keyspace,
    )
    model_specification.persist(model_spec, output_paths.model_specification)

    logger.debug("Loading existing outputs if present.")
    return SimulationRun(
        command=command,
        output_paths=output_paths,
        keyspace=keyspace,
        finished_sim_metadata=collect_metadata(
            output_paths.metadata_dir, output_paths.results_dir
        ),
    )


def report_initial_status(
    num_jobs_completed: int, finished_sim_metadata: pd.DataFrame, total_num_jobs: int
) -> None:
    """Log how much of the keyspace a previous run finished, and sanity-check it.

    Parameters
    ----------
    num_jobs_completed
        How many of the keyspace's simulations are already complete.
    finished_sim_metadata
        Metadata for the simulations a previous invocation finished.
    total_num_jobs
        The size of the keyspace.

    Raises
    ------
    RuntimeError
        If the previous run holds results the current configuration would not
        have produced, meaning the code or the outputs have since changed.
    """
    if num_jobs_completed:
        logger.debug(
            f"{num_jobs_completed} of {total_num_jobs} jobs completed in previous run."
        )
    extra_jobs_completed = num_jobs_completed - len(finished_sim_metadata)
    # NOTE: there can never be more rows in `finished_sim_metadata` than `num_jobs_completed`
    # because `num_jobs_completed` was calculated by comparing the keyspace to `finished_sim_metadata`.
    if extra_jobs_completed:
        raise RuntimeError(
            f"There are {extra_jobs_completed} jobs from the previous run which would not have been created "
            "with the configuration saved with that run. That either means that code "
            "has changed between then and now or that the outputs or configuration data "
            "have been modified."
        )


def _validate_resumable(output_paths: OutputPaths) -> None:
    """Raise if a run directory lacks the state a resume reads back."""
    missing = [
        path
        for path in (
            output_paths.keyspace,
            output_paths.branches,
            output_paths.model_specification,
        )
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"{output_paths.root} cannot be resumed: it is missing "
            f"{', '.join(path.name for path in missing)}. A run directory created "
            "before these files were persisted is not resumable; start a new run."
        )


def build_simulation_tasks(
    tool: Tool,
    run: SimulationRun,
    *,
    native_specification: NativeSpecification,
    backup_freq: float | None,
    extra_args: dict[str, Any],
    max_attempts: int = 3,
    env_prefix: str | None = None,
    template_name: str = "psimulate",
) -> SimulationTasks:
    """Build the Jobmon tasks for a resolved simulation run.

    Writes the backup lookup table the workers use to recover a partially
    completed simulation, then creates one Jobmon task per simulation still
    to be run.

    For ``restart`` the full job list is built and Jobmon's native resume
    skips the already-completed tasks; every other command filters the
    completed simulations out here.

    Parameters
    ----------
    tool
        Jobmon Tool used to register the task template and create tasks.
    run
        The run resolved by :func:`resolve_simulation_run`.
    native_specification
        Cluster resource specification for each simulation task.
    backup_freq
        Interval in seconds between simulation backups, or ``None`` to
        disable them.
    extra_args
        Additional command-specific arguments passed through to the workers.
    max_attempts
        Maximum number of attempts Jobmon will make for each task.
    env_prefix
        Optional absolute path to the prefix of the environment the workers
        should run in. Defaults to the launching environment.
    template_name
        Name to register the Jobmon ``TaskTemplate`` under. Must be unique
        per Tool/Workflow, so a caller building several simulation groups in
        one workflow must pass a distinct value per group.

    Returns
    -------
        The tasks and how many of the keyspace's simulations are already
        complete.
    """
    logger.debug("Parsing arguments into worker job parameters.")
    restart = run.command == COMMANDS.restart
    job_list_metadata = pd.DataFrame() if restart else run.finished_sim_metadata
    job_parameters, num_jobs_completed = jobs.build_job_list(
        model_specification_path=run.output_paths.model_specification,
        output_root=run.output_paths.root,
        keyspace=run.keyspace,
        finished_sim_metadata=job_list_metadata,
        backup_freq=backup_freq,
        backup_dir=run.output_paths.backup_dir,
        backup_metadata_path=run.output_paths.backup_metadata_path,
        worker_logging_root=run.output_paths.worker_logging_root,
        extras=extra_args,
    )
    # For restart the real completed count comes from the collected metadata,
    # not from build_job_list, which saw an empty DataFrame.
    if restart:
        num_jobs_completed = len(run.finished_sim_metadata)

    # Check the prior run for consistency before writing anything, so an
    # inconsistent directory is rejected without leaving task metadata behind.
    report_initial_status(num_jobs_completed, run.finished_sim_metadata, len(run.keyspace))

    if not job_parameters:
        return SimulationTasks(tasks=[], num_jobs_completed=num_jobs_completed)
    logger.debug(f"Found {len(job_parameters)} jobs to run.")

    if backup_freq is not None:
        write_backup_metadata(
            backup_metadata_path=run.output_paths.backup_metadata_path,
            job_parameters_list=job_parameters,
        )

    return SimulationTasks(
        tasks=get_task_list(
            tool=tool,
            command=run.command,
            job_parameters_list=job_parameters,
            metadata_dir=run.output_paths.metadata_dir,
            results_dir=run.output_paths.results_dir,
            worker_logging_root=run.output_paths.worker_logging_root,
            native_specification=native_specification,
            max_attempts=max_attempts,
            env_prefix=env_prefix,
            template_name=template_name,
        ),
        num_jobs_completed=num_jobs_completed,
    )


def write_backup_metadata(
    backup_metadata_path: Path, job_parameters_list: list[jobs.JobParameters]
) -> None:
    """Append the job-to-backup-file lookup table the workers read to resume.

    Parameters
    ----------
    backup_metadata_path
        CSV to append to, created if it does not yet exist.
    job_parameters_list
        The jobs to record, one row each.
    """
    lookup_table = []
    for params in job_parameters_list:
        job_dict: dict[str, Any] = {
            "input_draw": params.input_draw,
            "random_seed": params.random_seed,
            "job_id": params.task_id,
        }
        branch_config = collapse_nested_dict(params.branch_configuration)
        for k, v in branch_config:
            job_dict[k] = v
        lookup_table.append(job_dict)

    df = pd.DataFrame(lookup_table)
    df.to_csv(
        backup_metadata_path,
        index=False,
        mode="a",
        header=not os.path.exists(backup_metadata_path),
    )
