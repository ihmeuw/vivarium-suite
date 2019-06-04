import click

from vivarium_cluster_tools.vadmin import utilities, log_parser, repositories


@click.group()
def vadmin():
    """Utility for performing ``vivarium`` administrative tasks."""
    pass


@vadmin.command()
@click.argument('logs-directory', type=click.Path(exists=True, file_okay=False))
@click.option('--result-directory', '-o', type=click.Path(exists=True, file_okay=False),
              help='The directory into which to write the summary of the parsed logs. '
                   'Defaults to given logs directory if not given.')
@click.option('-v', 'verbose', count=True, help='Configure logging verbosity.')
def parse(logs_directory, result_directory, verbose):
    """Parse and summarize the worker_logs from a ``psimulate`` command.

    Given a worker logs directory from a previous run, a summary hdf will be
    created in the ``result_directory`` (which defaults to the given logs
    directory unless otherwise specified) with two keys: 'worker_data', which
    includes a summary line for each worker log in the directory and 'sim_data',
    which includes a summary line for each simulation job run by a worker.
    """
    utilities.configure_master_process_logging_to_terminal(verbose)
    if not result_directory:
        result_directory = logs_directory
    log_parser.parse_log_directory(logs_directory, result_directory)


@vadmin.group()
def oauth():
    """Add or delete OAuth tokens from github and stash.

    This command manages authentication for github and stash by communicating
    with the webservices over http through their respective REST APIs. It
    allows a user to create, store, or delete OAuth tokens which will be
    used in place of a user name and password when managing repositories.

    """
    pass


@oauth.command()
@click.argument('service', type=click.Choice(['stash', 'github']))
@click.option('-v', 'verbose', count=True, help='Configure logging verbosity.')
def create(service, verbose):
    """Create a new OAuth token for a SERVICE."""
    utilities.configure_master_process_logging_to_terminal(verbose)
    repositories.oauth_create(service)


@oauth.command()
@click.argument('service', type=click.Choice(['stash', 'github']))
def remove(service):
    print(f'creating oauth token for {service}')


@oauth.command()
def display():
    print(f'Displaying OAuth tokens')
