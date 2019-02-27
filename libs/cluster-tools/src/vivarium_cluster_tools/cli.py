import click
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
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu']), default='proj_cost_effect',
              help='The cluster project under which to run the simulation.')
@click.option('--peak-memory', '-m', type=int, default=3,
              help='The estimated maximum memory usage in GB of an individual simulate job. The simulations will be '
                   'run with this as a limit.')
def run(simulation_configuration, branch_configuration, result_directory, project, peak_memory):
    """Run a parallel simulation. The simulation itself is defined by a SIMULATION_CONFIGURATION yaml file
    and the parameter changes across runs are defined by a BRANCH_CONFIGURATION yaml file.

    If a results directory is provided, a subdirectory will be created with the same name as the
    SIMULATION_CONFIGURATION if one does not exist. Results will be written to a further subdirectory
    named after the start time of the simulation run.

    If a results directory is not provided the base results_directory is taken to be
    /share/scratch/users/{$USER}/vivarium_results."""
    main(simulation_configuration, branch_configuration, result_directory, project, peak_memory)


@psimulate.command()
@click.argument('results-root', type=click.Path(exists=True, file_okay=False, writable=True))
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu']), default='proj_cost_effect',
              help='The cluster project under which to run the simulation.')
@click.option('--peak-memory', '-m', type=int, default=3,
              help='The estimated maximum memory usage of an individual simulate job. The simulations will be run '
                   'with this as a limit.')
def restart(results_root, project, peak_memory):
    """Restart a simulation defined by a results directory RESULTS_ROOT. Restarting will not erase existing results,
    but will start workers to perform the remaining simulations.  RESULTS_ROOT is expected to be an output directory
    from a previous `psimulate run`."""

    main(None, None, results_root, project, peak_memory, restart=True)
