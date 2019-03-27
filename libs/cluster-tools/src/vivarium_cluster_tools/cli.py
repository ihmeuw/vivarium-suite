from bdb import BdbQuit

import click
from loguru import logger

from .runner import main

MAX_JOBS_WITH_STATE_TABLE = 100


@click.group()
def psimulate():
    """psimulate is a command line utility for running many simulations in parallel.  You may initiate a new run with
    `run` or restart a run from where it was stopped by using `restart`."""
    pass


@psimulate.command()
@click.argument('simulation_configuration', type=click.Path(exists=True, dir_okay=False))
@click.argument('branch_configuration', type=click.Path(exists=True, dir_okay=False))
@click.option('--result-directory', '-o', default=None,
              help='The directory to write results to. A folder will be created in this directory with the same name '
                   'as the configuration file.')
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu', 'proj_cost_effect_diarrhea']), default='proj_cost_effect',
              help='The cluster project under which to run the simulation.')
@click.option('--peak-memory', '-m', type=int, default=3,
              help='The estimated maximum memory usage in GB of an individual simulate job. The simulations will be '
                   'run with this as a limit.')
@click.option('--pdb', 'with_debugger', is_flag=True, help='Drop into python debugger if an error occurs.')
def run(simulation_configuration, branch_configuration, result_directory, project, peak_memory, with_debugger):
    """Run a parallel simulation. The simulation itself is defined by a SIMULATION_CONFIGURATION yaml file
    and the parameter changes across runs are defined by a BRANCH_CONFIGURATION yaml file.

    If a results directory is provided, a subdirectory will be created with the same name as the
    SIMULATION_CONFIGURATION if one does not exist. Results will be written to a further subdirectory
    named after the start time of the simulation run.

    If a results directory is not provided the base results_directory is taken to be
    /share/scratch/users/{$USER}/vivarium_results."""
    try:
        main(simulation_configuration, branch_configuration, result_directory, project, peak_memory)
    except (BdbQuit, KeyboardInterrupt):
        raise
    except Exception as e:
        if with_debugger:
            import pdb
            import traceback
            traceback.print_exc()
            pdb.post_mortem()
        else:
            logger.exception("Uncaught exception {}".format(e))
            raise


@psimulate.command()
@click.argument('results-root', type=click.Path(exists=True, file_okay=False, writable=True))
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu', 'proj_cost_effect_diarrhea']), default='proj_cost_effect',
              help='The cluster project under which to run the simulation.')
@click.option('--peak-memory', '-m', type=int, default=3,
              help='The estimated maximum memory usage of an individual simulate job. The simulations will be run '
                   'with this as a limit.')
@click.option('--pdb', 'with_debugger', is_flag=True, help='Drop into python debugger if an error occurs.')
def restart(results_root, project, peak_memory, with_debugger):
    """Restart a parallel simulation defined by a results directory RESULTS_ROOT. Restarting will not erase existing
    results, but will start workers to perform the remaining simulations.  RESULTS_ROOT is expected to be an output
    directory from a previous `psimulate run`."""
    try:
        main(None, None, results_root, project, peak_memory, restart=True)
    except (BdbQuit, KeyboardInterrupt):
        raise
    except Exception as e:
        if with_debugger:
            import pdb
            import traceback
            traceback.print_exc()
            pdb.post_mortem()
        else:
            logger.exception("Uncaught exception {}".format(e))
            raise
