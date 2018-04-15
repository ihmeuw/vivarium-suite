import click
import yaml

from .branches import Keyspace
from .runner import parallel_run, parallel_relaunch
from .utils import get_results_root, get_log_path

MAX_JOBS_WITH_STATE_TABLE = 100


@click.group()
def psimulate():
    pass


@click.command()
@click.option('--project', '-P', type=click.Option('proj_cost_effect', 'proj_csu'), default='proj_cost_effect')
@click.option('--master_log_path', '-l', default='{results_root}/master.log')
@click.option('--quiet', '-q', is_flag=True, help='Only log errors in the master log.')
@click.option('--result_directory', '-o', default='/share/scratch/users/{username}/vivarium_results/')
@click.option('--output_state_table', '-V', is_flag=True)
@click.option('--peak_memory', default=3.0,
              help='Maximum memory that each worker can use. Determines slots per worker.')
@click.option('--max_retries', type=click.IntRange(0, 10, clamp=True), default=1,
              help='Maximum times to retry a failed job.')
@click.option('--num_random_seeds', type=click.INT)
@click.option('--num_input_draws', '-d', type=click.IntRange(0, 1000))
@click.argument('branch_configuration', type=click.Path(exists=True, dir_okay=False))
@click.argument('simulation_configuration', type=click.Path(exists=True, dir_okay=False))
def run(simulation_configuration, branch_configuration, num_input_draws, num_random_seeds,
        max_retries, peak_memory, output_state_table, result_directory, quiet, master_log_path, project):

    with open(simulation_configuration) as f:
        simulation_configuration = yaml.load(f)
    keyspace = Keyspace.from_branch_configuration(num_input_draws, num_random_seeds, branch_configuration)

    workload_configuration = {'simulation_configuration': simulation_configuration,
                              'keyspace': keyspace}

    if output_state_table and len(keyspace) > MAX_JOBS_WITH_STATE_TABLE:
        output_state_table = click.prompt(f"You asked to write final state tables with {len(keyspace)} "
                                          "branches. This will output a large quantity of data. Are you "
                                          "sure you'd like to proceed?", type=click.BOOL)

    worker_configuration = {'max_retries': max_retries,
                            'peak_memory': peak_memory,
                            'project': project,
                            'output_state_table': output_state_table,}

    results_root = get_results_root(result_directory, simulation_configuration)
    master_log_path = get_log_path(results_root, master_log_path)

    output_configuration = {'existing_outputs': None,
                            'results_root': results_root,
                            'quiet': quiet,
                            'master_log_path': master_log_path}

    parallel_run(workload_configuration, worker_configuration, output_configuration)


@click.command()
@click.argument('results_root', type=click.Path(exists=True, file_okay=False, writable=True))
def relaunch(results_root):
    parallel_relaunch(results_root)
