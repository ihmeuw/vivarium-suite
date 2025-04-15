"""
===============
Vivarium Worker
===============

RQ worker executable for running simulation jobs.

"""

import json
import math
import os
from copy import deepcopy
from pathlib import Path
from time import sleep, time
from traceback import format_exc

import dill
import pandas as pd
from layered_config_tree import LayeredConfigTree
from loguru import logger
from rq import get_current_job
from vivarium.framework.engine import SimulationContext
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.vipin.perf_counters import CounterSnapshot

VIVARIUM_WORK_HORSE_IMPORT_PATH = f"{__name__}.work_horse"


class ParallelSimulationContext(SimulationContext):
    """A parallel simulation context.

    This is identical to ``SimulationContext`` except that it does not write out
    the results to disk in order to allow them to be batch-written.
    """

    def _write_results(self, results: dict[str, pd.DataFrame]) -> None:
        pass


def work_horse(job_parameters: dict) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    node = f"{ENV_VARIABLES.HOSTNAME.value}"
    job = f"{ENV_VARIABLES.JOB_ID.value}:{ENV_VARIABLES.TASK_ID.value}"

    job_parameters = JobParameters(**job_parameters)

    logger.info(f"Launching new job {job} on {node}")
    logger.info(f"Starting job: {job_parameters}")

    try:
        start_snapshot = CounterSnapshot()
        event = {"start": time()}  # timestamps of application events
        logger.info("Beginning simulation setup.")
        backup = get_backup(job_parameters)
        if backup:
            logger.info(f"Restarting simulation from saved backup")
            sim = backup
            event["simulation_start"] = time()
            exec_time = {
                "setup_minutes": (event["simulation_start"] - event["start"]) / 60
            }  # execution event
            logger.info(
                f'Simulation setup completed in {exec_time["setup_minutes"]:.3f} minutes.'
            )
        else:
            sim = ParallelSimulationContext(
                job_parameters.model_specification,
                configuration=job_parameters.sim_config,
                logging_verbosity=job_parameters.extras["sim_verbosity"],
            )
            logger.info("Simulation configuration:")
            logger.info(str(sim.configuration))
            sim.setup()
            event["simulant_initialization_start"] = time()
            exec_time = {
                "setup_minutes": (event["simulant_initialization_start"] - event["start"])
                / 60
            }  # execution event
            logger.info(
                f'Simulation setup completed in {exec_time["setup_minutes"]:.3f} minutes.'
            )

            sim.initialize_simulants()
            event["simulation_start"] = time()
            exec_time["simulant_initialization_minutes"] = (
                event["simulation_start"] - event["simulant_initialization_start"]
            ) / 60
            logger.info(
                f'Simulant initialization completed in {exec_time["simulant_initialization_minutes"]:.3f} minutes.'
            )
        num_steps = sim.get_number_of_steps_remaining()
        logger.info(f"Starting main simulation loop with {num_steps} time steps")
        backup_path = (
            job_parameters.backup_configuration["backup_dir"] / str(get_current_job().id)
        ).with_suffix(".pkl")
        sim.run(
            backup_freq=job_parameters.backup_configuration["backup_freq"],
            backup_path=backup_path,
        )
        event["results_start"] = time()
        exec_time["main_loop_minutes"] = (
            event["results_start"] - event["simulation_start"]
        ) / 60
        exec_time["step_mean_seconds"] = (
            event["results_start"] - event["simulation_start"]
        ) / num_steps
        logger.info(
            f'Simulation main loop completed in {exec_time["main_loop_minutes"]:.3f} minutes.'
        )
        logger.info(f'Average step length was {exec_time["step_mean_seconds"]:.3f} seconds.')

        sim.finalize()
        sim.report(print_results=False)
        event["end"] = time()
        end_snapshot = CounterSnapshot()
        do_sim_epilogue(start_snapshot, end_snapshot, event, exec_time, job_parameters)
        results = sim.get_results()  # Dict[measure, results dataframe]
        finished_results_metadata = format_and_record_details(job_parameters, results)
        remove_backups(backup_path)

        return finished_results_metadata, results

    except Exception:
        logger.exception("Unhandled exception in worker")
        job = get_current_job()
        job.meta["root_exception"] = format_exc()
        job.save_meta()
        raise
    finally:
        logger.info(f"Exiting job: {job_parameters}")


def do_sim_epilogue(
    start: CounterSnapshot,
    end: CounterSnapshot,
    event: dict,
    exec_time: dict,
    parameters: JobParameters,
) -> None:
    exec_time["results_minutes"] = (event["end"] - event["results_start"]) / 60
    logger.info(f'Results reporting completed in {exec_time["results_minutes"]:.3f} minutes.')
    exec_time["total_minutes"] = (event["end"] - event["start"]) / 60
    logger.info(f'Total simulation run time {exec_time["total_minutes"]:.3f} minutes.')

    perf_log = logger.add(
        Path(ENV_VARIABLES.VIVARIUM_LOGGING_DIRECTORY.value)
        / f"perf.{ENV_VARIABLES.JOB_ID.value}.{ENV_VARIABLES.TASK_ID.value}.log",
        level="DEBUG",
        serialize=True,
    )
    logger.debug(
        json.dumps(
            {
                "host": ENV_VARIABLES.HOSTNAME.value,
                "job_number": ENV_VARIABLES.JOB_ID.value,
                "task_number": ENV_VARIABLES.TASK_ID.value,
                "run_id": get_current_job().id,
                "draw": parameters.input_draw,
                "seed": parameters.random_seed,
                "scenario": parameters.branch_configuration,
                "event": event,
                "exec_time": exec_time,
                "counters": (end - start).to_dict(),
            }
        )
    )
    logger.remove(perf_log)


def format_and_record_details(
    job_parameters: JobParameters, results: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Add finished simulation details to results and metadata."""
    finished_results_metadata = pd.DataFrame(index=[0])
    for key, val in collapse_nested_dict(job_parameters.job_specific):
        col_name = key.split(".")[-1]
        for df in results.values():
            # insert the new columns second from the right and use the
            # last part of the key as the column name
            df.insert(df.shape[1] - 1, col_name, val)
        finished_results_metadata[key] = val
    return finished_results_metadata


def get_backup(job_parameters: JobParameters) -> SimulationContext | None:
    backup_dir = job_parameters.backup_configuration["backup_dir"]
    metadata_path = job_parameters.backup_configuration["backup_metadata_path"]
    try:
        pickle_metadata = pd.read_csv(metadata_path)
        format_val = lambda v: v if isinstance(v, (int, float)) else f'"{v}"'
        query_conditions = " & ".join(
            [
                f"`{k}` == {format_val(v)}"
                for k, v in collapse_nested_dict(job_parameters.job_specific)
            ]
        )

        # Use the query method to find rows that match the lookup parameters
        run_ids = pickle_metadata.query(query_conditions)["job_id"].to_list()
        possible_pickles = [
            Path(backup_dir / run_id).with_suffix(".pkl") for run_id in run_ids
        ]
        existing_pickles = [pickle for pickle in possible_pickles if pickle.exists()]
        if existing_pickles:
            last_pickle = max(existing_pickles, key=os.path.getctime)
            if len(existing_pickles) > 1:
                logger.warning(
                    f"Multiple backups found for {job_parameters}. Using the most recent and deleting the rest."
                )
                for stale_file in set(existing_pickles) - {last_pickle}:
                    os.remove(stale_file)
            with open(last_pickle, "rb") as f:
                sim = dill.load(f)
            current_job_id = get_current_job().id
            logger.info(f"Renaming backup file {last_pickle} to {current_job_id}.pkl")
            if job_parameters.backup_configuration["backup_freq"] is not None:
                # Sleep to prevent FS latency when loading the pickle
                sleep(5)
                os.rename(last_pickle, (backup_dir / str(current_job_id)).with_suffix(".pkl"))
            return sim
    except (OSError, FileNotFoundError):
        logger.info(
            "Missing backup or backup directory. Restarting simulation from beginning."
        )
        return None
    except Exception as e:
        logger.warning(f"Load from backup failed with Exception: {e}")
        logger.warning("Restarting simulation from beginning.")
        return None


def remove_backups(backup_path: Path) -> None:
    try:
        os.remove(backup_path)
    except FileNotFoundError:
        pass
