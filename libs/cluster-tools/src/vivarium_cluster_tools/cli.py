import click
from .runner import main

MAX_JOBS_WITH_STATE_TABLE = 100


@click.group()
def psimulate():
    pass


@psimulate.command()
@click.argument('simulation_configuration', type=click.Path(exists=True, dir_okay=False))
@click.argument('branch_configuration', type=click.Path(exists=True, dir_okay=False))
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu']), default='proj_cost_effect')
@click.option('--peak-memory', '-m', type=int, default=3)
@click.option('--result_directory', '-o', default=None)
def run(simulation_configuration, branch_configuration, result_directory, project, peak_memory):
    main(simulation_configuration, branch_configuration, result_directory, project, peak_memory)


@psimulate.command()
@click.argument('results_root', type=click.Path(exists=True, file_okay=False, writable=True))
@click.option('--project', '-P', type=click.Choice(['proj_cost_effect', 'proj_csu']), default='proj_cost_effect')
@click.option('--peak-memory', '-m', type=int, default=3)
def restart(results_root, project, peak_memory):
    main(None, None, results_root, project, peak_memory, restart=True)
