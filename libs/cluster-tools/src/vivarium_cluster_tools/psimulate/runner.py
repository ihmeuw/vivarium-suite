"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import yaml
from jobmon.core.configuration import JobmonConfig
from jobmon.core.exceptions import ConfigError as JobmonConfigError
from loguru import logger
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools import logs
from vivarium_cluster_tools.psimulate import (
    COMMANDS,
    branches,
    cluster,
    jobs,
    model_specification,
    paths,
    pip_env,
)
from vivarium_cluster_tools.psimulate.jobmon_config.workflow import build_workflow
from vivarium_cluster_tools.psimulate.notifications import send_slack_notification
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.performance_logger import (
    append_perf_data_to_central_logs,
)
from vivarium_cluster_tools.psimulate.results.writing import collect_metadata
from vivarium_cluster_tools.psimulate.workflow_config.builder import (
    build_workflow_from_config,
)
from vivarium_cluster_tools.psimulate.workflow_config.config import WorkflowConfig
from vivarium_cluster_tools.psimulate.workflow_config.serialization import (
    workflow_config_to_dict,
)
from vivarium_cluster_tools.psimulate.workflow_config.utilities import WORKFLOW_ARGS_FILENAME
from vivarium_cluster_tools.vipin.perf_report import report_performance

if TYPE_CHECKING:
    from jobmon.client.workflow import Workflow


def _bind_and_run_workflow(
    workflow: Workflow,
    output_root: Path,
    *,
    resume: bool = False,
) -> tuple[str, str]:
    """Bind a Jobmon workflow, log the monitoring URL, and run it.

    Parameters
    ----------
    workflow
        The Jobmon workflow to submit.
    output_root
        Output directory to mention in log messages.
    resume
        Whether to resume a previously started workflow.

    Returns
    -------
        A ``(wf_status, monitoring_url)`` tuple.  *wf_status* is the
        workflow status string from Jobmon (e.g. ``"D"`` for DONE).
        *monitoring_url* is the Jobmon GUI URL (may be empty).
    """
    workflow.bind()

    gui_url = JobmonConfig().get("http", "gui_url")
    monitoring_url = f"{gui_url}/#/workflow/{workflow.workflow_id}" if gui_url else ""

    logger.info(f"Submitting Jobmon workflow. Results will be written to {output_root}")
    if monitoring_url:
        logger.info(f"Monitor progress at: {monitoring_url}")

    # Match the workflow timeout to the remaining time on the SLURM runner
    # node so jobmon doesn't outlive (or underuse) the allocation.
    seconds_until_timeout = cluster.get_workflow_timeout_seconds()
    run_kwargs: dict[str, Any] = {"resume": resume}
    if seconds_until_timeout is not None:
        run_kwargs["seconds_until_timeout"] = seconds_until_timeout
    wf_status = workflow.run(**run_kwargs)
    if wf_status is None:
        raise RuntimeError("Jobmon workflow.run() returned None unexpectedly.")
    return wf_status, monitoring_url


def workflow_main(
    workflow_config: WorkflowConfig,
    verbose: int = 0,
    resume: bool = False,
) -> None:
    """Entry point for the psimulate workflow subcommand.

    Parameters
    ----------
    workflow_config
        The parsed and validated workflow configuration (with CLI overrides applied).
    verbose
        Verbosity level.
    resume
        Whether to resume a previously started workflow.
    """
    logger.info(f"Starting workflow: {workflow_config.name}")

    # Create output directory if it doesn't exist
    output_root = workflow_config.output_directory
    output_root.mkdir(parents=True, exist_ok=True)

    workflow_args_path = output_root / WORKFLOW_ARGS_FILENAME

    if resume:
        workflow_args = workflow_args_path.read_text().strip()
        logger.info(f"Resuming workflow with args: {workflow_args}")
    else:
        # Generate a unique workflow_args using a timestamp so each fresh
        # run is distinct even with the same config and output directory.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_hash = hashlib.md5(str(output_root).encode()).hexdigest()[:8]
        workflow_args = f"workflow_{workflow_config.name}_{output_hash}_{timestamp}"

    # Write the requested configuration to output directory
    write_workflow_configuration(output_root, workflow_config)

    # Build the workflow
    logger.debug("Building workflow.")
    workflow = build_workflow_from_config(workflow_config, workflow_args=workflow_args)

    # Persist workflow_args before running so --resume can find it
    workflow_args_path.write_text(workflow_args)

    wf_status, monitoring_url = _bind_and_run_workflow(workflow, output_root, resume=resume)

    send_slack_notification(
        workflow_name=workflow_config.name,
        status=wf_status,
        monitoring_url=monitoring_url,
        results_dir=str(output_root),
    )

    if wf_status != "D":
        raise RuntimeError(
            f"Workflow finished with status '{wf_status}' (expected 'D' for DONE)."
        )
    logger.info(f"Workflow completed successfully. Results in {output_root}")


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


def write_workflow_configuration(output_root: Path, workflow_config: WorkflowConfig) -> None:
    """Write workflow configuration to a YAML file in the output directory.

    Creates a ``configuration.yaml`` that can be reused directly with
    ``psimulate workflow -c configuration.yaml``.

    Parameters
    ----------
    output_root
        The root output directory for the workflow.
    workflow_config
        The parsed and validated workflow configuration.
    """
    config: dict[str, Any] = {"workflow": workflow_config_to_dict(workflow_config)}
    config_file = output_root / "configuration.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    logger.info(f"Run configuration written to {config_file}")


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
    root_hash = hashlib.md5(str(output_paths.root).encode()).hexdigest()[:8]
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

    wf_status, monitoring_url = _bind_and_run_workflow(
        workflow, output_paths.root, resume=restart
    )

    send_slack_notification(
        workflow_name=workflow_name,
        status=wf_status,
        monitoring_url=monitoring_url,
        results_dir=str(output_paths.root),
    )

    # Spit out a performance report for the workers.
    try_run_vipin(output_paths)

    # Count task outcomes from Jobmon's in-memory task statuses

    num_done_total = sum(1 for t in workflow.tasks.values() if t.final_status == "D")
    num_completed_this_run = num_done_total - num_jobs_completed
    num_jobs_attempted = len(job_parameters) - num_jobs_completed
    num_failed = num_jobs_attempted - num_completed_this_run
    num_successful = num_jobs_completed + num_completed_this_run

    if wf_status != "D":
        logger.info(
            f"Workflow finished with status '{wf_status}' (expected 'D' for DONE).",
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
