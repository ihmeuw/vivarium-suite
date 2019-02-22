import click
import yaml

from .branches import Keyspace
from .runner import main, restart

MAX_JOBS_WITH_STATE_TABLE = 100


@click.group()
def psimulate():
    pass


@psimulate.command()
@click.option('--project', '-P', type=click.Option('proj_cost_effect', 'proj_csu'), default='proj_cost_effect')
@click.option('--result_directory', '-o', default='/share/scratch/users/{username}/vivarium_results/')
@click.argument('branch_configuration', type=click.Path(exists=True, dir_okay=False))
@click.argument('simulation_configuration', type=click.Path(exists=True, dir_okay=False))
def run(simulation_configuration, branch_configuration, result_directory, project):
    main(simulation_configuration, branch_configuration, result_directory, project)


@psimulate.command()
@click.option('--project', '-P', type=click.Option('proj_cost_effect', 'proj_csu'), default='proj_cost_effect')
@click.argument('results_root', type=click.Path(exists=True, file_okay=False, writable=True))
def restart(results_root, project):
    main(None, None, results_root, project, restart=True)
