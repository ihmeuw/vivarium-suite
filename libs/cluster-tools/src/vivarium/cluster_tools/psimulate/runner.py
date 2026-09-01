"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from vivarium.cluster_tools.core import cluster, logs
from vivarium.cluster_tools.core.jobmon import client
from vivarium.cluster_tools.core.notifications import send_slack_notification
from vivarium.cluster_tools.psimulate import COMMANDS, paths, pip_env, simulation_tasks
from vivarium.cluster_tools.psimulate.jobmon_workflow import build_workflow
from vivarium.cluster_tools.psimulate.paths import OutputPaths
from vivarium.cluster_tools.psimulate.performance_logger import (
    append_perf_data_to_central_logs,
)
from vivarium.cluster_tools.utilities import hash_output_path
from vivarium.cluster_tools.vipin.perf_report import report_performance


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


def write_configuration(
    output_root: Path,
    command: str,
    input_paths: paths.InputPaths,
    native_specification: cluster.NativeSpecification,
    max_workers: int,
    max_attempts: int,
    backup_freq: float | None,
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
    backup_freq: float | None,
    extra_args: dict[str, Any],
    slack_channel: str | None = None,
    slack_tag: str | None = None,
    mute_slack: bool = False,
) -> None:
    logger.debug("Validating cluster environment.")
    cluster.validate_cluster_environment()

    output_paths = simulation_tasks.resolve_output_paths(
        command=command,
        input_paths=input_paths,
    )

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

    run = simulation_tasks.resolve_simulation_run(
        command=command,
        input_paths=input_paths,
        output_paths=output_paths,
        extra_args=extra_args,
    )
    if not run.finished_sim_metadata.empty and command not in [
        COMMANDS.restart,
        COMMANDS.expand,
    ]:
        raise RuntimeError(
            "Existing outputs detected. Please choose a different output directory or use the 'restart' or 'expand' command to continue from these outputs."
        )

    tool = client.make_tool()
    sim_tasks = simulation_tasks.build_simulation_tasks(
        tool,
        run,
        native_specification=native_specification,
        backup_freq=backup_freq,
        extra_args=extra_args,
        max_attempts=max_attempts,
    )
    job_parameters = sim_tasks.job_parameters
    num_jobs_completed = sim_tasks.num_jobs_completed
    # Let the user know if something is fishy at this point.
    total_num_jobs = len(run.keyspace)
    report_initial_status(num_jobs_completed, run.finished_sim_metadata, total_num_jobs)
    if not sim_tasks.tasks:
        logger.debug("No jobs to run, exiting.")
        return

    restart = command == COMMANDS.restart
    # For restart we reuse the original run's workflow_args so Jobmon can
    # resume the same workflow (skipping already-completed tasks).
    wf_command = COMMANDS.run if restart else command
    # Include a hash of the full output path to avoid workflow_args collisions
    # between concurrent pipelines that happen to share the same timestamp.
    root_hash = hash_output_path(output_paths.root)
    workflow_name = f"psimulate_{wf_command}_{output_paths.root.name}_{root_hash}"
    logger.debug("Building Jobmon workflow.")
    workflow = build_workflow(
        tool,
        workflow_name=workflow_name,
        tasks=sim_tasks.tasks,
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
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
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
