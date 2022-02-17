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
from typing import Dict, List

import numpy as np
import pandas as pd
import yaml
from loguru import logger
from vivarium.framework.artifact import parse_artifact_path_config
from vivarium.framework.configuration import (
    ConfigurationError,
    build_model_specification,
)
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools import logs
from vivarium_cluster_tools.psimulate import cluster
from vivarium_cluster_tools.psimulate import globals as vct_globals
from vivarium_cluster_tools.psimulate import paths, programming_environment, results
from vivarium_cluster_tools.psimulate.branches import Keyspace
from vivarium_cluster_tools.psimulate.registry import RegistryManager
from vivarium_cluster_tools.vipin.perf_report import report_performance


class RunContext:
    def __init__(
        self,
        input_paths: paths.InputPaths,
        output_paths: paths.OutputPaths,
        restart: bool,
        expand: Dict[str, int],
        no_batch: bool,
    ):
        self.number_already_completed = 0
        self.output_directory = output_paths.root
        self.no_batch = no_batch
        self.sge_log_directory = output_paths.cluster_logging_root
        self.worker_log_directory = output_paths.worker_logging_root

        if restart:
            self.keyspace = Keyspace.from_previous_run(self.output_directory)
            self.existing_outputs = pd.read_hdf(output_paths.results)
            if expand:
                self.keyspace.add_draws(expand["num_draws"])
                self.keyspace.add_seeds(expand["num_seeds"])
                self.keyspace.persist(self.output_directory)
        else:
            model_specification = build_model_specification(input_paths.model_specification)

            self.keyspace = Keyspace.from_branch_configuration(
                input_paths.branch_configuration
            )
            if input_paths.artifact:
                if vct_globals.FULL_ARTIFACT_PATH_KEY in self.keyspace:
                    raise ConfigurationError(
                        "Artifact path cannot be specified both in the branch specification file"
                        " and as a command line argument.",
                        str(input_paths.artifact),
                    )
                if not input_paths.artifact.exists():
                    raise FileNotFoundError(
                        f"Cannot find artifact at path {str(input_paths.artifact)}"
                    )
                artifact_path = input_paths.artifact
            elif (
                vct_globals.ARTIFACT_PATH_KEY
                in model_specification.configuration[vct_globals.INPUT_DATA_KEY]
            ):
                artifact_path = parse_artifact_path_config(model_specification.configuration)

            if artifact_path:
                model_specification.configuration[vct_globals.INPUT_DATA_KEY].update(
                    {vct_globals.ARTIFACT_PATH_KEY: input_paths.artifact}, source=__file__
                )

            with open(
                self.output_directory / vct_globals.MODEL_SPEC_FILENAME, "w"
            ) as config_file:
                yaml.dump(model_specification.to_dict(), config_file)

            self.existing_outputs = None

            # Log some basic stuff about the simulation to be run.
            self.keyspace.persist(self.output_directory)
        self.model_specification = self.output_directory / vct_globals.MODEL_SPEC_FILENAME


def build_job_list(ctx: RunContext) -> List[dict]:
    jobs = []
    number_already_completed = 0

    for (input_draw, random_seed, branch_config) in ctx.keyspace:
        parameters = {
            "model_specification_file": str(ctx.model_specification),
            "branch_configuration": branch_config,
            "input_draw": int(input_draw),
            "random_seed": int(random_seed),
            "results_path": ctx.output_directory,
        }

        do_schedule = True
        if ctx.existing_outputs is not None:
            mask = ctx.existing_outputs.input_draw == int(input_draw)
            mask &= ctx.existing_outputs.random_seed == int(random_seed)
            if branch_config:
                for k, v in collapse_nested_dict(branch_config):
                    if isinstance(v, float):
                        mask &= np.isclose(ctx.existing_outputs[k], v)
                    else:
                        mask &= ctx.existing_outputs[k] == v
            do_schedule = not np.any(mask)

        if do_schedule:
            jobs.append(parameters)
        else:
            number_already_completed += 1

    if number_already_completed:
        logger.info(
            f"{number_already_completed} of {len(ctx.keyspace)} jobs completed in previous run."
        )
        if number_already_completed != len(ctx.existing_outputs):
            logger.warning(
                "There are jobs from the previous run which would not have been created "
                "with the configuration saved with the run. That either means that code "
                "has changed between then and now or that the outputs or configuration data "
                "have been modified. This may represent a serious error so give it some thought."
            )

    ctx.number_already_completed = number_already_completed
    np.random.shuffle(jobs)
    return jobs


def process_job_results(registry_manager: RegistryManager, ctx: RunContext):
    start_time = time()

    if ctx.existing_outputs is not None:
        written_results = ctx.existing_outputs
    else:
        written_results = pd.DataFrame()
    unwritten_results = []

    logger.info("Entering main processing loop.")
    batch_size = 200
    try:
        while registry_manager.jobs_to_finish:
            sleep(5)
            unwritten_results.extend(registry_manager.get_results())
            if ctx.no_batch and unwritten_results:
                written_results, unwritten_results = results.write_results_batch(
                    ctx.output_directory,
                    written_results,
                    unwritten_results,
                    len(unwritten_results),
                )
            elif len(unwritten_results) > batch_size:
                written_results, unwritten_results = results.write_results_batch(
                    ctx.output_directory,
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
                ctx.output_directory,
                written_results,
                unwritten_results,
                batch_size=batch_size,
            )
            logger.info(f"Unwritten results: {len(unwritten_results)}")
            logger.info(f"Elapsed time: {(time() - start_time) / 60:.1f} minutes.")


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

    output_paths = paths.OutputPaths.from_entry_point_args(
        input_model_specification_path=input_paths.model_specification,
        result_directory=input_paths.result_directory,
        restart=restart,
        expand=bool(expand),
    )

    if not no_cleanup:
        atexit.register(paths.delete_on_catastrophic_failure, output_paths=output_paths)

    atexit.register(lambda: logger.remove())

    native_specification["job_name"] = output_paths.root.parts[-2]
    native_specification = cluster.NativeSpecification(**native_specification)

    logs.configure_main_process_logging_to_file(output_paths.logging_root)
    programming_environment.validate(output_paths.environment_file)

    ctx = RunContext(
        input_paths,
        output_paths,
        restart,
        expand,
        no_batch,
    )
    cluster.check_user_sge_config()
    jobs = build_job_list(ctx)

    if len(jobs) == 0:
        logger.info("Nothing to do")
        return

    logger.info("Starting jobs. Results will be written to: {}".format(ctx.output_directory))

    if redis_processes == -1:
        redis_processes = int(math.ceil(len(jobs) / cluster.DEFAULT_JOBS_PER_REDIS_INSTANCE))

    worker_template, redis_ports = cluster.launch_redis_processes(
        redis_processes, output_paths.logging_root,
    )
    output_paths.worker_settings.write_text(worker_template)

    registry_manager = RegistryManager(redis_ports, ctx.number_already_completed)
    registry_manager.enqueue(jobs)

    cluster.start_cluster(
        len(jobs),
        ctx.sge_log_directory,
        ctx.worker_log_directory,
        output_paths.worker_settings,
        native_specification,
    )

    process_job_results(registry_manager, ctx)

    try_run_vipin(output_paths.worker_logging_root)

    logger.info("Jobs completed. Results written to: {}".format(ctx.output_directory))
