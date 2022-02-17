"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""
import atexit
import math
from pathlib import Path
from time import sleep, time
from typing import Dict, Optional

import pandas as pd
from loguru import logger

from vivarium_cluster_tools import logs
from vivarium_cluster_tools.psimulate import (
    branches,
    cluster,
    jobs,
    model_specification,
    paths,
    programming_environment,
    registry,
    results,
)
from vivarium_cluster_tools.vipin.perf_report import report_performance


def process_job_results(
    registry_manager: registry.RegistryManager,
    existing_outputs: Optional[pd.DataFrame],
    output_directory: Path,
    no_batch: bool,
):
    start_time = time()

    if existing_outputs is not None:
        written_results = existing_outputs
    else:
        written_results = pd.DataFrame()
    unwritten_results = []

    logger.info("Entering main processing loop.")
    batch_size = 200
    try:
        while registry_manager.jobs_to_finish:
            sleep(5)
            unwritten_results.extend(registry_manager.get_results())
            if no_batch and unwritten_results:
                written_results, unwritten_results = results.write_results_batch(
                    output_directory,
                    written_results,
                    unwritten_results,
                    len(unwritten_results),
                )
            elif len(unwritten_results) > batch_size:
                written_results, unwritten_results = results.write_results_batch(
                    output_directory,
                    written_results,
                    unwritten_results,
                    batch_size,
                )

            registry_manager.update_and_report()
            logger.info(f"Unwritten results: {len(unwritten_results)}")
            logger.info(f"Elapsed time: {(time() - start_time)/60:.1f} minutes.")
    finally:
        batch_size = 500
        while unwritten_results:
            written_results, unwritten_results = results.write_results_batch(
                output_directory,
                written_results,
                unwritten_results,
                batch_size=batch_size,
            )
            logger.info(f"Unwritten results: {len(unwritten_results)}")
            logger.info(f"Elapsed time: {(time() - start_time) / 60:.1f} minutes.")


def load_existing_outputs(result_path: Path, restart: bool) -> pd.DataFrame:
    try:
        existing_outputs = pd.read_hdf(result_path)
    except FileNotFoundError:
        existing_outputs = pd.DataFrame()
    assert (
        existing_outputs.empty or restart
    ), "How do you have existing outputs on an initial run?"
    return existing_outputs


def report_initial_status(
    num_jobs_completed: int, existing_outputs: pd.DataFrame, keyspace: branches.Keyspace
) -> None:
    if num_jobs_completed:
        logger.info(
            f"{num_jobs_completed} of {len(keyspace)} jobs completed in previous run."
        )
    if num_jobs_completed != len(existing_outputs):
        extra_jobs_completed = num_jobs_completed - len(existing_outputs)
        logger.warning(
            f"There are {extra_jobs_completed} jobs from the previous run which would not have been created "
            "with the configuration saved with the run. That either means that code "
            "has changed between then and now or that the outputs or configuration data "
            "have been modified. This may represent a serious error so give it some thought."
        )


def try_run_vipin(log_path: Path):
    try:
        report_performance(
            input_directory=log_path, output_directory=log_path, output_hdf=False, verbose=1
        )
    except Exception as e:
        logger.warning(f"Performance reporting failed with: {e}")


def main(
    input_paths: paths.InputPaths,
    native_specification: dict,
    redis_processes: int,
    restart: bool = False,
    expand: Dict[str, int] = None,
    no_batch: bool = False,
    no_cleanup: bool = False,
):
    cluster.exit_if_on_submit_host(cluster.get_hostname())

    # Generate programmatic representation of the output directory structure
    output_paths = paths.OutputPaths.from_entry_point_args(
        input_model_specification_path=input_paths.model_specification,
        result_directory=input_paths.result_directory,
        restart=restart,
        expand=bool(expand),
    )
    # Make output root and all subdirectories.
    output_paths.touch()

    logs.configure_main_process_logging_to_file(output_paths.logging_root)
    programming_environment.validate(output_paths.environment_file)

    keyspace = branches.Keyspace.from_entry_point_args(
        input_branch_configuration_path=input_paths.branch_configuration,
        restart=restart,
        expand=expand,
        keyspace_path=output_paths.keyspace,
        branches_path=output_paths.branches,
    )
    keyspace.persist(output_paths.keyspace, output_paths.branches)

    model_spec = model_specification.parse(
        input_model_specification_path=input_paths.model_specification,
        artifact_path=input_paths.artifact,
        model_specification_path=output_paths.model_specification,
        restart=restart,
        keyspace=keyspace,
    )
    model_specification.persist(model_spec, output_paths.model_specification)

    existing_outputs = load_existing_outputs(output_paths.results, restart)

    job_parameters, num_jobs_completed = jobs.build_job_list(
        model_specification_path=output_paths.model_specification,
        output_root=output_paths.root,
        keyspace=keyspace,
        existing_outputs=existing_outputs,
    )
    report_initial_status(num_jobs_completed, existing_outputs, keyspace)
    if len(job_parameters) == 0:
        logger.info("Nothing to do")
        return

    if not no_cleanup:
        atexit.register(paths.delete_on_catastrophic_failure, output_paths=output_paths)

    atexit.register(lambda: logger.remove())

    native_specification["job_name"] = output_paths.root.parts[-2]
    native_specification = cluster.NativeSpecification(**native_specification)

    cluster.check_user_sge_config()

    logger.info(f"Starting jobs. Results will be written to: {str(output_paths.root)}")

    if redis_processes == -1:
        redis_processes = int(
            math.ceil(len(job_parameters) / cluster.DEFAULT_JOBS_PER_REDIS_INSTANCE)
        )

    worker_template, redis_ports = cluster.launch_redis_processes(
        redis_processes,
        output_paths.logging_root,
    )
    output_paths.worker_settings.write_text(worker_template)

    registry_manager = registry.RegistryManager(redis_ports, num_jobs_completed)
    registry_manager.enqueue(jobs)

    cluster.start_cluster(
        len(job_parameters),
        output_paths.cluster_logging_root,
        output_paths.worker_logging_root,
        output_paths.worker_settings,
        native_specification,
    )

    process_job_results(
        registry_manager=registry_manager,
        existing_outputs=existing_outputs,
        output_directory=output_paths.root,
        no_batch=no_batch,
    )

    try_run_vipin(output_paths.worker_logging_root)

    logger.info(f"Jobs completed. Results written to: {str(output_paths.root)}")
