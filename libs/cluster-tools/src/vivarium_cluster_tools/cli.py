import click

from vivarium_cluster_tools import runner, utilities, globals as vct_globals


shared_options = [
    click.option('--project', '-P',
                 type=click.Choice(vct_globals.CLUSTER_PROJECTS),
                 default=vct_globals.DEFAULT_CLUSTER_PROJECT,
                 help='The cluster project under which to run the simulation.'),
    click.option('--peak-memory', '-m',
                 type=int,
                 default=3,
                 help=('The estimated maximum memory usage in GB of an individual simulate job. '
                       'The simulations will be run with this as a limit.')),
    click.option('--pdb', 'with_debugger',
                 is_flag=True,
                 help='Drop into python debugger if an error occurs.'),
    click.option('--redis',
                 type=int,
                 default=-1,
                 help=(f'Number of redis databases to use.  Defaults to a redis instance for every '
                       f'{vct_globals.DEFAULT_JOBS_PER_REDIS_INSTANCE} jobs.')),
    click.option('-v', 'verbose',
                 count=True,
                 help='Configure logging verbosity.')
]


def pass_shared_options(func):
    # add all the shared options to the command
    for option in shared_options:
        func = option(func)
    return func


@click.group()
def psimulate():
    """A command line utility for running many simulations in parallel.

    You may initiate a new run with the ``run`` sub-command or restart a run
    from where it was stopped by using the ``restart`` sub-command.

    """
    pass


@psimulate.command()
@click.argument('model_specification', type=click.Path(exists=True, dir_okay=False))
@click.argument('branch_configuration', type=click.Path(exists=True, dir_okay=False))
@click.option('--result-directory', '-o', default=None,
              help='The directory to write results to. A folder will be created in this directory with the same name '
                   'as the configuration file.')
@pass_shared_options
def run(model_specification, branch_configuration, result_directory, **options):
    """Run a parallel simulation.

    The simulation itself is defined by a MODEL_SPECIFICATION yaml file
    and the parameter changes across runs are defined by a BRANCH_CONFIGURATION
    yaml file.

    If a results directory is provided, a subdirectory will be created with the
    same name as the MODEL_SPECIFICATION if one does not exist. Results will be
    written to a further subdirectory named after the start time of the
    simulation run.

    If a results directory is not provided the base results_directory is taken
    to be /share/costeffectiveness/results.

    """
    utilities.configure_master_process_logging_to_terminal(options['verbose'])
    main = utilities.handle_exceptions(runner.main, options['with_debugger'])

    main(model_specification, branch_configuration, result_directory,
         options['project'], options['peak_memory'],
         redis_processes=options['redis'])


@psimulate.command()
@click.argument('results-root', type=click.Path(exists=True, file_okay=False, writable=True))
@pass_shared_options
def restart(results_root, **options):
    """Restart a parallel simulation from a previous run at RESULTS_ROOT.

    Restarting will not erase existing results, but will start workers to
    perform the remaining simulations.  RESULTS_ROOT is expected to be an
    output directory from a previous ``psimulate run`` invocation.

    """
    utilities.configure_master_process_logging_to_terminal(options['verbose'])
    main = utilities.handle_exceptions(runner.main, options['with_debugger'])

    main(None, None, results_root,
         options['project'], options['peak_memory'],
         redis_processes=options['redis'], restart=True)


@psimulate.command()
@click.argument('results-root', type=click.Path(exists=True, file_okay=False, writable=True))
@click.option('--add-draws', type=int, default=0, help='The number of input draws to add to a previous run.')
@click.option('--add-seeds', type=int, default=0, help='The number of random seeds to add to a previous run.')
@pass_shared_options
def expand(results_root, **options):
    """Expand a previous run at RESULTS_ROOT by adding input draws and/or
    random seeds.

    Expanding will not erase existing results, but will start workers to perform
    the additional simulations determined by the added draws/seeds.
    RESULTS_ROOT is expected to be an output directory from a previous
    ``psimulate run`` invocation.

    """
    utilities.configure_master_process_logging_to_terminal(options['verbose'])
    main = utilities.handle_exceptions(runner.main, options['with_debugger'])

    main(None, None, results_root,
         options['project'], options['peak_memory'],
         redis_processes=options['redis'],
         expand={'num_draws': options['add_draws'],
                 'num_seeds': options['add_seeds']})
