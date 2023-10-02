"""
===============
Vivarium Worker
===============

RQ worker executable for running simulation jobs.

"""
import json
import math
from pathlib import Path
from time import time
from traceback import format_exc

import pandas as pd
from loguru import logger
from rq import get_current_job
from vivarium.config_tree import ConfigTree
from vivarium.framework.engine import SimulationContext
from vivarium.framework.utilities import collapse_nested_dict

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.vipin.perf_counters import CounterSnapshot

VIVARIUM_WORK_HORSE_IMPORT_PATH = f"{__name__}.work_horse"


def work_horse(job_parameters: dict) -> pd.DataFrame:
    node = f"{ENV_VARIABLES.HOSTNAME.value}"
    job = f"{ENV_VARIABLES.JOB_ID.value}:{ENV_VARIABLES.TASK_ID.value}"

    job_parameters = JobParameters(**job_parameters)

    logger.info(f"Launching new job {job} on {node}")
    logger.info(f"Starting job: {job_parameters}")

    try:
        sim = setup_sim(job_parameters)

        start_time = pd.Timestamp(**sim.configuration.time.start.to_dict())
        end_time = pd.Timestamp(**sim.configuration.time.end.to_dict())
        step_size = pd.Timedelta(days=sim.configuration.time.step_size)
        num_steps = int(math.ceil((end_time - start_time) / step_size))

        start_snapshot = CounterSnapshot()
        event = {"start": time()}  # timestamps of application events
        logger.info("Beginning simulation setup.")
        sim.setup()
        event["simulant_initialization_start"] = time()
        exec_time = {
            "setup_minutes": (event["simulant_initialization_start"] - event["start"]) / 60
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

        logger.info(f"Starting main simulation loop with {num_steps} time steps")
        sim.run()
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
        metrics = sim.report(print_results=False)
        event["end"] = time()
        end_snapshot = CounterSnapshot()

        do_sim_epilogue(start_snapshot, end_snapshot, event, exec_time, job_parameters)

        idx = pd.MultiIndex.from_tuples(
            [(job_parameters.input_draw, job_parameters.random_seed)],
            names=["input_draw_number", "random_seed"],
        )
        output_metrics = pd.DataFrame(metrics, index=idx)
        for k, v in collapse_nested_dict(job_parameters.branch_configuration):
            output_metrics[k] = v
        return output_metrics

    except Exception:
        logger.exception("Unhandled exception in worker")
        job = get_current_job()
        job.meta["root_exception"] = format_exc()
        job.save_meta()
        raise
    finally:
        logger.info(f"Exiting job: {job_parameters}")


def setup_sim(job_parameters: JobParameters) -> SimulationContext:
    """Set up a simulation context with the branch/job-specific configuration parameters."""
    configuration = ConfigTree(
        job_parameters.branch_configuration, layers=["branch_base", "branch_expanded"]
    )

    configuration.update(
        parameter_update_format(job_parameters),
        layer="branch_expanded",
        source="branch_config",
    )
    job_parameters.branch_configuration.update(configuration.to_dict())
    sim = SimulationContext(job_parameters.model_specification, configuration=configuration)
    logger.info("Simulation configuration:")
    logger.info(str(sim.configuration))

    return sim


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


def parameter_update_format(job_parameters: JobParameters) -> dict:
    return {
        "run_configuration": {
            "run_id": str(get_current_job().id) + "_" + str(time()),
            "results_directory": job_parameters.results_path,
            "run_key": job_parameters.job_specific,
        },
        "randomness": {
            "random_seed": job_parameters.random_seed,
            "additional_seed": job_parameters.input_draw,
        },
        "input_data": {
            "input_draw_number": job_parameters.input_draw,
        },
    }
