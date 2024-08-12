"""
================
psimulate Runner
================

The main process loop for `psimulate` runs.

"""

import os
import shutil
from collections import defaultdict
from pathlib import Path
from time import sleep, time
from typing import Dict, Optional, Union

import pandas as pd
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
    redis_dbs,
)
from vivarium_cluster_tools.psimulate import results as psim_results
from vivarium_cluster_tools.psimulate import worker
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.performance_logger import (
    append_perf_data_to_central_logs,
)
from vivarium_cluster_tools.vipin.perf_report import report_performance


def process_job_results(
    registry_manager: redis_dbs.RegistryManager,
    existing_metadata: pd.DataFrame,
    existing_results: Dict[str, pd.DataFrame],
    output_paths: OutputPaths,
    no_batch: bool,
) -> Dict[str, Union[int, float]]:
    unwritten_metadata = []
    unwritten_results = []
    batch_size = 0 if no_batch else 200
    status: Dict[str, Union[int, float]] = defaultdict(int)

    logger.info("Entering main processing loop.")
    start_time = time()
    try:
        while registry_manager.jobs_to_finish:
            sleep(5)
            for metadata, results in registry_manager.get_results():
                unwritten_metadata.append(metadata)
                unwritten_results.append(results)

            if len(unwritten_results) > batch_size:
                (
                    existing_metadata,
                    unwritten_metadata,
                    existing_results,
                    unwritten_results,
                ) = psim_results.write_results_batch(
                    output_paths,
                    existing_metadata,
                    existing_results,
                    unwritten_metadata,
                    unwritten_results,
                    batch_size,
                )

            status = registry_manager.update_and_report()
            logger.info(f"Unwritten results: {len(unwritten_results)}")
            logger.info(f"Elapsed time: {(time() - start_time)/60:.1f} minutes.")
    finally:
        batch_size = 500
        while unwritten_results:
            (
                existing_metadata,
                unwritten_metadata,
                existing_results,
                unwritten_results,
            ) = psim_results.write_results_batch(
                output_paths,
                existing_metadata,
                existing_results,
                unwritten_metadata,
                unwritten_results,
                batch_size=batch_size,
            )
            logger.info(f"Unwritten results: {len(unwritten_results)}")
            logger.info(f"Elapsed time: {(time() - start_time) / 60:.1f} minutes.")

    return status


def load_existing_output_metadata(metadata_path: Path, restart: bool) -> pd.DataFrame:
    try:
        existing_output_metadata = pd.read_csv(metadata_path)
    except FileNotFoundError:
        existing_output_metadata = pd.DataFrame()
    assert (
        existing_output_metadata.empty or restart
    ), "How do you have existing outputs on an initial run?"
    return existing_output_metadata


def load_existing_results(result_path: Path, restart: bool) -> Dict[str, pd.DataFrame]:
    filepaths = result_path.glob("*.parquet")
    results = {filepath.stem: pd.read_parquet(filepath) for filepath in filepaths}
    if results and not restart:
        raise RuntimeError(
            f"This is an initial run but results aready exist at {result_path}"
        )
    return results


def report_initial_status(
    num_jobs_completed: int, finished_sim_metadata: pd.DataFrame, total_num_jobs: int
) -> None:
    if num_jobs_completed:
        logger.info(
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
        append_perf_data_to_central_logs(perf_df, output_paths)
    except Exception as e:
        logger.warning(f"Appending performance data to central logs failed with: {e}")


def write_backup_metadata(
    backup_metadata_path: Path, parameters_by_job: dict[str, dict]
) -> None:
    lookup_table = []
    for job_id, params in parameters_by_job.items():
        job_dict = {
            "input_draw": params["input_draw"],
            "random_seed": params["random_seed"],
            "job_id": job_id,
        }
        branch_config = collapse_nested_dict(params["branch_configuration"])
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
    max_workers: Optional[int],
    redis_processes: int,
    no_batch: bool,
    backup_freq: int,
    extra_args: dict,
) -> None:
    logger.info("Validating cluster environment.")
    cluster.validate_cluster_environment()

    # Generate programmatic representation of the output directory structure
    output_paths = paths.OutputPaths.from_entry_point_args(
        command=command,
        input_artifact_path=input_paths.artifact,
        result_directory=input_paths.result_directory,
        input_model_spec_path=input_paths.model_specification,
    )
    logger.info("Setting up output directory and all subdirectories.")
    output_paths.touch()

    logger.info("Setting up logging to files.")
    # Start sending logs to a file now that it exists.
    logs.configure_main_process_logging_to_file(output_paths.logging_root)
    logger.info("Validating programming environment.")
    # Either write a requirements.txt with the current environment
    # or verify the current environment matches the prior environment
    # used when doing a restart.
    pip_env.validate(output_paths.environment_file)

    logger.info(
        "Parsing input arguments into model specification and branches and writing to disk."
    )
    # Parse the branches configuration into a parameter space
    # and a flat representation of all parameters to be run.
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

    logger.info("Loading existing outputs if present.")
    # Load in any existing partial outputs if present.
    finished_sim_metadata = load_existing_output_metadata(
        metadata_path=output_paths.finished_sim_metadata,
        restart=command in [COMMANDS.restart, COMMANDS.expand],
    )

    logger.info("Parsing arguments into worker job parameters.")
    # Translate the keyspace into the list of jobs to actually run
    # after accounting for any partially present results.
    job_parameters, num_jobs_completed = jobs.build_job_list(
        command=command,
        model_specification_path=output_paths.model_specification,
        output_root=output_paths.root,
        keyspace=keyspace,
        finished_sim_metadata=finished_sim_metadata,
        backup_freq=backup_freq,
        backup_dir=output_paths.backup_dir,
        backup_metadata_path=output_paths.backup_metadata_path,
        extras=extra_args,
    )
    # Let the user know if something is fishy at this point.
    total_num_jobs = len(keyspace)
    report_initial_status(num_jobs_completed, finished_sim_metadata, total_num_jobs)
    if len(job_parameters) == 0:
        logger.info("No jobs to run, exiting.")
        return
    else:
        logger.info(f"Found {len(job_parameters)} jobs to run.")

    num_workers = len(job_parameters)
    if max_workers:
        num_workers = min(max_workers, num_workers)

    logger.info("Spinning up Redis DBs and connecting to main process.")
    # Spin up the job & result dbs and get back (hostname, port) pairs for all the dbs.
    redis_ports = redis_dbs.launch(
        num_processes=redis_processes,
        num_workers=num_workers,
        redis_logging_root=output_paths.logging_root,
    )
    # Spin up a unified interface to all the redis databases
    registry_manager = redis_dbs.RegistryManager(redis_ports, num_workers, num_jobs_completed)
    logger.info("Enqueuing jobs on Redis queues.")
    # Distribute all the remaining jobs across the job queues
    # in the redis databases.
    registry_manager.enqueue(
        jobs=job_parameters, workhorse_import_path=worker.WORK_HORSE_PATHS[command]
    )

    if backup_freq is not None:
        write_backup_metadata(
            backup_metadata_path=output_paths.backup_metadata_path,
            parameters_by_job=registry_manager.get_params_by_job(),
        )
    # Generate a worker template that chooses a redis DB at random to connect to.
    # This should (approximately) evenly distribute the workers over the work.
    redis_urls = [f"redis://{hostname}:{port}" for hostname, port in redis_ports]
    worker_template = (
        f"import random\nredis_urls = {redis_urls}\nREDIS_URL = random.choice(redis_urls)\n\n"
    )
    # Dump the worker config to a file we can pass to the workers on startup.
    output_paths.worker_settings.write_text(worker_template)
    worker_launch_script = worker.build_launch_script(
        worker_settings_file=output_paths.worker_settings,
        worker_log_directory=output_paths.worker_logging_root,
    )
    logger.info(f"Submitting redis workers to the cluster.")
    # Start an rq worker for every job using the cluster scheduler, up to an
    # optional user-specified limit. The workers will start as soon as they get
    # scheduled and start looking for work. They run in burst mode which means
    # they shut down if they can't find anything to do. This means it's
    # critical that we put the jobs on the queue before the workers land,
    # otherwise they'll just show up and shut down.

    cluster.submit_worker_jobs(
        num_workers=num_workers,
        worker_launch_script=worker_launch_script,
        cluster_logging_root=output_paths.cluster_logging_root,
        native_specification=native_specification,
    )

    logger.info(
        "Entering monitoring and results processing loop. "
        f"Results will be written to {str(output_paths.root)}"
    )
    # Enter the main monitoring and processing loop, which will check on
    # all the queues periodically, report status updates, and gather
    # and write results when they are available.
    existing_results = load_existing_results(
        result_path=output_paths.results_dir,
        restart=command in [COMMANDS.restart, COMMANDS.expand],
    )
    status = process_job_results(
        registry_manager=registry_manager,
        existing_metadata=finished_sim_metadata,
        existing_results=existing_results,
        output_paths=output_paths,
        no_batch=no_batch,
    )

    # Spit out a performance report for the workers.
    try_run_vipin(output_paths)

    # Emit warning if any jobs failed
    if status["failed"] > 0:
        logger.warning(
            f"*** NOTE: There {'was' if status['failed'] == 1 else 'were'} "
            f"{status['failed']} failed job{'' if status['failed'] == 1 else 's'}. ***"
        )
    else:
        logger.info(f"Removing sim backup directory {output_paths.backup_dir}")
        shutil.rmtree(output_paths.backup_dir)

    logger.info(
        f"{status['successful'] - num_jobs_completed} of {status['total']} jobs "
        f"completed successfully from this {command}.\n"
        f"({status['successful']} of {total_num_jobs} total jobs completed successfully overall)\n"
        f"Results written to: {str(output_paths.results_dir)}"
    )
