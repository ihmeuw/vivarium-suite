import click

from vivarium_cluster_tools import shared
from vivarium_cluster_tools.vadmin import utilities, log_parser, repositories, oauth_utilities


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
    main = shared.handle_exceptions(oauth_utilities.oauth_create, with_debugger=True)
    main(service)


@oauth.command()
@click.argument('service', type=click.Choice(['stash', 'github']))
@click.option('-v', 'verbose', count=True, help='Configure logging verbosity.')
def remove(service, verbose):
    """Remove an existing OAuth token for SERVICE."""
    utilities.configure_master_process_logging_to_terminal(verbose)
    main = shared.handle_exceptions(oauth_utilities.oauth_remove, with_debugger=True)
    main(service)


@oauth.command()
@click.option('-v', 'verbose', count=True, help='Configure logging verbosity.')
def display(verbose):
    """Display local OAuth information."""
    utilities.configure_master_process_logging_to_terminal(verbose)
    main = shared.handle_exceptions(oauth_utilities.oauth_display, with_debugger=True)
    main()


@vadmin.command()
@click.argument('service', type=click.Choice(['stash', 'github']))
@click.argument('repo_name', type=click.STRING)
@click.option('-o', '--output_root', type=click.Path(exists=True, file_okay=False, resolve_path=True),
              help='Directory to create the new repository in.')
@click.option('-v', 'verbose', count=True, help='Configure logging verbosity.')
def init(service, repo_name, output_root, verbose):
    """Creates a new research repository REPO_NAME and hosts in on SERVICE.

    In order to use this command, you must have an OAuth token for the
    requested SERVICE. You can generate an OAuth token by running::

    vadmin oauth create SERVICE

    You must also have ssh keys set up on the requested SERVICE so that you
    can ``clone`` and ``push`` to the new repository.  You must handle the
    ssh configuration manually.

    The research repository will be generated from a template at
    `https://github.com/ihmeuw/vivarium_research_template`_.

    """
    utilities.configure_master_process_logging_to_terminal(verbose)
    main = shared.handle_exceptions(repositories.init, with_debugger=True)
    main(service, repo_name, output_root)
