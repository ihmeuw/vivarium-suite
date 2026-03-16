"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""

import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
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
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.performance_logger import (
    append_perf_data_to_central_logs,
)
from vivarium_cluster_tools.psimulate.results.writing import collect_metadata
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
    workflow_name = f"psimulate_{wf_command}_{output_paths.root.name}"
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

    # Bind the workflow to get its ID before running, so we can display the
    # monitoring URL immediately rather than waiting for run() to finish.
    workflow.bind()

    gui_url = JobmonConfig().get("http", "gui_url")
    monitoring_url = f"{gui_url}/#/workflow/{workflow.workflow_id}" if gui_url else ""

    logger.info(
        f"Submitting Jobmon workflow. Results will be written to {str(output_paths.root)}",
    )
    if monitoring_url:
        logger.info(f"Monitor progress at: {monitoring_url}")

    wf_status = workflow.run(resume=restart)

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
