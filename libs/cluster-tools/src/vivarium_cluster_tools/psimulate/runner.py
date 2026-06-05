"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger
from vivarium.engine.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.core import cluster, logs
from vivarium_cluster_tools.core.jobmon import client
from vivarium_cluster_tools.core.notifications import send_slack_notification
from vivarium_cluster_tools.psimulate import (
    COMMANDS,
    branches,
    jobs,
    model_specification,
    paths,
    pip_env,
)
from vivarium_cluster_tools.psimulate.jobmon_workflow import build_workflow
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.performance_logger import (
    append_perf_data_to_central_logs,
)
from vivarium_cluster_tools.psimulate.results.writing import collect_metadata
from vivarium_cluster_tools.utilities import hash_output_path
from vivarium_cluster_tools.vipin.perf_report import report_performance


def report_initial_status(
    num_jobs_completed: int, finished_sim_metadata: pd.DataFrame, total_num_jobs: int
) -> None:
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


def try_run_vipin(output_paths: OutputPaths) -> None:
    log_path = output_paths.worker_logging_root
    try:
        perf_df = report_performance(
            input_directory=log_path, output_directory=log_path, output_hdf=False, verbose=1
        )
    except Exception as e:
        logger.warning(f"Performance reporting failed with: {e}")
        return

    try:
        if perf_df is not None and len(perf_df) > 0:
            append_perf_data_to_central_logs(perf_df, output_paths)
    except Exception as e:
        logger.warning(f"Appending performance data to central logs failed with: {e}")


def write_backup_metadata(
    backup_metadata_path: Path, job_parameters_list: list[jobs.JobParameters]
) -> None:
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


def write_configuration(
    output_root: Path,
    command: str,
    input_paths: paths.InputPaths,
    native_specification: cluster.NativeSpecification,
    max_workers: int,
    max_attempts: int,
    backup_freq: int | None,
    extra_args: dict[str, Any],
) -> None:
    """Write the resolved run configuration to a YAML file in the output directory.

    This creates a ``configuration.yaml`` file that records all of the
    parameters used for the run.  The file is written in a format that is
    directly usable with ``psimulate <command> --run-config configuration.yaml``
    so that previous runs can be easily reproduced.

    Parameters
    ----------
    output_root
        The root output directory for the simulation run.
    command
        The psimulate sub-command (e.g. ``"run"``, ``"restart"``, ``"expand"``).
    input_paths
        The resolved input file paths.
    native_specification
        The cluster resource specification.
    max_workers
        Maximum number of concurrent workers.
    max_attempts
        Maximum number of Jobmon task attempts.
    backup_freq
        Interval in seconds between saving backups, or ``None`` to disable.
    extra_args
        Additional command-specific arguments (e.g. ``sim_verbosity``,
        ``num_draws``, ``num_seeds``).

    """
    config: dict[str, Any] = {}

    # Input paths – keys match the names accepted by --run-config
    if command == COMMANDS.run:
        if input_paths.model_specification is not None:
            config["model_specification"] = str(input_paths.model_specification)
        if input_paths.branch_configuration is not None:
            config["branch_configuration"] = str(input_paths.branch_configuration)
        config["result_directory"] = str(input_paths.result_directory)
        if input_paths.artifact is not None:
            config["artifact_path"] = str(input_paths.artifact)
    else:
        # restart / expand – the result directory *is* the results_root
        config["results_root"] = str(input_paths.result_directory)

    # Cluster resources
    config["project"] = native_specification.project
    config["queue"] = native_specification.queue
    config["peak_memory"] = native_specification.peak_memory
    config["max_runtime"] = native_specification.max_runtime
    if native_specification.hardware:
        config["hardware"] = ",".join(native_specification.hardware)

    # Execution parameters
    config["max_workers"] = max_workers
    config["max_attempts"] = max_attempts
    if backup_freq is not None:
        # backup_freq is stored in seconds; convert back to minutes for the CLI.
        # Written as a string so Click's MinutesOrNone type can parse it.
        config["backup_freq"] = str(backup_freq / 60.0)

    # Command-specific extras
    if "sim_verbosity" in extra_args:
        config["sim_verbosity"] = str(extra_args["sim_verbosity"])
    if command == COMMANDS.expand:
        if extra_args.get("num_draws"):
            config["add_draws"] = extra_args["num_draws"]
        if extra_args.get("num_seeds"):
            config["add_seeds"] = extra_args["num_seeds"]

    config_file = output_root / "configuration.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    logger.info(f"Run configuration written to {config_file}")


def main(
    command: str,
    input_paths: paths.InputPaths,
    native_specification: cluster.NativeSpecification,
    max_workers: int,
    max_attempts: int,
    backup_freq: int | None,
    extra_args: dict[str, Any],
) -> None:
    logger.debug("Validating cluster environment.")
    cluster.validate_cluster_environment()

    # Generate programmatic representation of the output directory structure
    output_paths = paths.OutputPaths.from_entry_point_args(
        command=command,
        input_artifact_path=input_paths.artifact,
        result_directory=input_paths.result_directory,
        input_model_spec_path=input_paths.model_specification,
    )
    logger.debug("Setting up output directory and all subdirectories.")
    output_paths.touch()

    logger.debug("Writing run configuration to output directory.")
    write_configuration(
        output_root=output_paths.root,
        command=command,
        input_paths=input_paths,
        native_specification=native_specification,
        max_workers=max_workers,
        max_attempts=max_attempts,
        backup_freq=backup_freq,
        extra_args=extra_args,
    )

    logger.debug("Setting up logging to files.")
    # Start sending logs to a file now that it exists.
    logs.configure_main_process_logging_to_file(output_paths.logging_root)
    logger.debug("Validating programming environment.")
    # Either write a requirements.txt with the current environment
    # or verify the current environment matches the prior environment
    # used when doing a restart.
    pip_env.validate(output_paths.environment_file)

    logger.debug(
        "Parsing input arguments into model specification and branches and writing to disk."
    )
    # Parse the branches configuration into a parameter space
    # and a flat representation of all parameters to be run.
    if command == COMMANDS.load_test:
        keyspace = branches.Keyspace.for_load_test(extra_args["num_workers"])
    else:
        keyspace = branches.Keyspace.from_entry_point_args(
            input_branch_configuration_path=input_paths.branch_configuration,
            keyspace_path=output_paths.keyspace,
            branches_path=output_paths.branches,
            extras=extra_args,
        )
    # Throw that into our output directory. The keyspace output is
    # a cartesian product representation of the parameter space and
    # branches is a flat representation with the product expanded out.
    keyspace.persist(output_paths.keyspace, output_paths.branches)

    # Parse the model specification and resolve the artifact path
    # and then write to the output directory.
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
    # Collect existing metadata from per-task CSV files in results/metadata/
    finished_sim_metadata = collect_metadata(
        output_paths.metadata_dir, output_paths.results_dir
    )
    if not finished_sim_metadata.empty and command not in [COMMANDS.restart, COMMANDS.expand]:
        raise RuntimeError(
            "Existing outputs detected. Please choose a different output directory or use the 'restart' or 'expand' command to continue from these outputs."
        )

    logger.debug("Parsing arguments into worker job parameters.")
    # For restart, we build the full job list (no filtering) and let Jobmon's
    # native resume skip already-completed tasks.  For other commands, we
    # filter out completed jobs ourselves.
    restart = command == COMMANDS.restart
    job_list_metadata = pd.DataFrame() if restart else finished_sim_metadata
    job_parameters, num_jobs_completed = jobs.build_job_list(
        model_specification_path=output_paths.model_specification,
        output_root=output_paths.root,
        keyspace=keyspace,
        finished_sim_metadata=job_list_metadata,
        backup_freq=backup_freq,
        backup_dir=output_paths.backup_dir,
        backup_metadata_path=output_paths.backup_metadata_path,
        worker_logging_root=output_paths.worker_logging_root,
        extras=extra_args,
    )
    # For restart, we know the real completed count from collect_metadata,
    # not from build_job_list (which saw an empty DataFrame).
    if restart:
        num_jobs_completed = len(finished_sim_metadata)
    # Let the user know if something is fishy at this point.
    total_num_jobs = len(keyspace)
    report_initial_status(num_jobs_completed, finished_sim_metadata, total_num_jobs)
    if len(job_parameters) == 0:
        logger.debug("No jobs to run, exiting.")
        return
    else:
        logger.debug(f"Found {len(job_parameters)} jobs to run.")

    if backup_freq is not None:
        write_backup_metadata(
            backup_metadata_path=output_paths.backup_metadata_path,
            job_parameters_list=job_parameters,
        )

    # Build the Jobmon workflow.
    # For restart we reuse the original run's workflow_args so Jobmon can
    # resume the same workflow (skipping already-completed tasks).
    wf_command = COMMANDS.run if restart else command
    # Include a hash of the full output path to avoid workflow_args collisions
    # between concurrent pipelines that happen to share the same timestamp.
    root_hash = hash_output_path(output_paths.root)
    workflow_name = f"psimulate_{wf_command}_{output_paths.root.name}_{root_hash}"
    logger.debug("Building Jobmon workflow.")
    workflow = build_workflow(
        workflow_name=workflow_name,
        command=command,
        job_parameters_list=job_parameters,
        output_paths=output_paths,
        native_specification=native_specification,
        max_workers=max_workers,
        max_attempts=max_attempts,
    )

    wf_status, monitoring_url = client.bind_and_run_workflow(
        workflow,
        output_paths.root,
        resume=restart,
        seconds_until_timeout=cluster.get_workflow_timeout_seconds(),
    )

    send_slack_notification(
        workflow_name=workflow_name,
        status=wf_status,
        command_label=f"psimulate {command}",
        monitoring_url=monitoring_url,
        results_dir=str(output_paths.root),
    )

    # Spit out a performance report for the workers.
    try_run_vipin(output_paths)

    # Count task outcomes from Jobmon's in-memory task statuses
    num_done_total = client.count_completed_tasks(workflow)
    num_completed_this_run = num_done_total - num_jobs_completed
    num_jobs_attempted = len(job_parameters) - num_jobs_completed
    num_failed = num_jobs_attempted - num_completed_this_run
    num_successful = num_jobs_completed + num_completed_this_run

    if wf_status != client.JOBMON_STATUS_DONE:
        logger.info(
            f"Workflow finished with status '{wf_status}' "
            f"(expected '{client.JOBMON_STATUS_DONE}' for DONE).",
        )

    # Emit warning if any jobs failed
    if num_failed > 0:
        logger.info(
            f"*** NOTE: There {'was' if num_failed == 1 else 'were'} "
            f"{num_failed} failed job{'' if num_failed == 1 else 's'}. ***",
        )
    else:
        logger.debug(f"Removing sim backup directory {output_paths.backup_dir}")
        shutil.rmtree(output_paths.backup_dir, ignore_errors=True)

    logger.info(
        f"{num_completed_this_run} of {num_jobs_attempted} jobs "
        f"completed successfully from this {command}.\n"
        f"({num_successful} of {total_num_jobs} total jobs completed successfully overall)\n"
        f"Results written to: {str(output_paths.results_dir)}",
    )
