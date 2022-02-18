"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""
import atexit
from pathlib import Path
from time import sleep, time
from typing import Dict

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
    redis_dbs,
    results,
)
from vivarium_cluster_tools.psimulate.worker import WORK_HORSE_PATHS
from vivarium_cluster_tools.vipin.perf_report import report_performance


def process_job_results(
    registry_manager: redis_dbs.RegistryManager,
    existing_outputs: pd.DataFrame,
    output_directory: Path,
    no_batch: bool,
):
    written_results = existing_outputs
    unwritten_results = []
    batch_size = 0 if no_batch else 200

    logger.info("Entering main processing loop.")
    start_time = time()
    try:
        while registry_manager.jobs_to_finish:
            sleep(5)
            unwritten_results.extend(registry_manager.get_results())

            if len(unwritten_results) > batch_size:
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
    cluster.exit_if_on_submit_host()

    # Generate programmatic representation of the output directory structure
    output_paths = paths.OutputPaths.from_entry_point_args(
        input_model_specification_path=input_paths.model_specification,
        result_directory=input_paths.result_directory,
        restart=restart,
        expand=bool(expand),
    )
    # Make output root and all subdirectories.
    output_paths.touch()
    # Hook for blowing away output directories if things go really
    # poorly and no results get written out.
    if not no_cleanup:
        atexit.register(paths.delete_on_catastrophic_failure, output_paths=output_paths)

    # Start sending logs to a file now that it exists.
    logs.configure_main_process_logging_to_file(output_paths.logging_root)
    # Either write a requirements.txt with the current environment
    # or verify the current environment matches the prior environment
    # used when doing a restart.
    programming_environment.validate(output_paths.environment_file)

    # Parse the branches configuration into a parameter space
    # and a flat representation of all parameters to be run.
    keyspace = branches.Keyspace.from_entry_point_args(
        input_branch_configuration_path=input_paths.branch_configuration,
        restart=restart,
        expand=expand,
        keyspace_path=output_paths.keyspace,
        branches_path=output_paths.branches,
    )
    # Throw that into our output directory. The keyspace output is
    # a cartesian product representation of the parameter space and
    # branches is a flat representation with the product expanded out.
    keyspace.persist(output_paths.keyspace, output_paths.branches)

    # Parse the model specification and resolve the artifact path
    # and then write to the output directory.
    model_spec = model_specification.parse(
        input_model_specification_path=input_paths.model_specification,
        artifact_path=input_paths.artifact,
        model_specification_path=output_paths.model_specification,
        restart=restart,
        keyspace=keyspace,
    )
    model_specification.persist(model_spec, output_paths.model_specification)

    # Load in any existing partial outputs if present.
    existing_outputs = load_existing_outputs(output_paths.results, restart)

    # Translate the keyspace into the list of jobs to actually run
    # after accounting for any partially present results.
    job_parameters, num_jobs_completed = jobs.build_job_list(
        model_specification_path=output_paths.model_specification,
        output_root=output_paths.root,
        keyspace=keyspace,
        existing_outputs=existing_outputs,
    )
    # Let the user know if something is fishy at this point.
    report_initial_status(num_jobs_completed, existing_outputs, keyspace)
    if len(job_parameters) == 0:
        logger.info("Nothing to do")
        return

    logger.info(f"Starting jobs. Results will be written to: {str(output_paths.root)}")
    # Spin up the job & result dbs and get back (hostname, port) pairs for all the dbs.
    redis_ports = redis_dbs.launch(
        num_processes=redis_processes,
        num_jobs=len(job_parameters),
        redis_logging_root=output_paths.logging_root,
    )
    # Generate a worker template that chooses a redis DB at random to connect to.
    # This should (approximately) evenly distribute the workers over the work.
    redis_urls = [f"redis://{hostname}:{port}" for hostname, port in redis_ports]
    worker_template = (
        f"import random\nredis_urls = {redis_urls}\nREDIS_URL = random.choice(redis_urls)\n\n"
    )
    # Dump the worker config to a file we can pass to the workers on startup.
    output_paths.worker_settings.write_text(worker_template)

    # Spin up a unified interface to all the redis databases
    registry_manager = redis_dbs.RegistryManager(redis_ports, num_jobs_completed)
    # Distribute all the remaining jobs across the job queues
    # in the redis databases.
    registry_manager.enqueue(
        jobs=job_parameters, workhorse_import_path=WORK_HORSE_PATHS["vivarium"]
    )

    # Cluster specification stuff to be cleaned up.
    native_specification["job_name"] = output_paths.root.parts[-2]
    native_specification = cluster.NativeSpecification(**native_specification)
    cluster.check_user_sge_config()

    # Start an rq worker for every job using the cluster scheduler. The workers
    # will start as soon as they get scheduled and start looking for work. They
    # run in burst mode which means they shut down if they can't find anything
    # to do. This means it's critical that we put the jobs on the queue before
    # the workers land, otherwise they'll just show up and shut down.
    cluster.start_cluster(
        len(job_parameters),
        output_paths.cluster_logging_root,
        output_paths.worker_logging_root,
        output_paths.worker_settings,
        native_specification,
    )

    # Enter the main monitoring and processing loop, which will check on
    # all the queues periodically, report status updates, and gather
    # and write results when they are available.
    process_job_results(
        registry_manager=registry_manager,
        existing_outputs=existing_outputs,
        output_directory=output_paths.root,
        no_batch=no_batch,
    )

    # Spit out a performance report for the workers.
    try_run_vipin(output_paths.worker_logging_root)

    logger.info(f"Jobs completed. Results written to: {str(output_paths.root)}")
