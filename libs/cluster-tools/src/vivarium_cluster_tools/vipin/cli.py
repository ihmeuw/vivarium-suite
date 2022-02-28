"""
=========
vipin CLI
=========

Command line interface for `vipin`.

.. click:: vivarium_cluster_tools.vipin.cli:vipin
   :prog: vipin
   :show-nested:

"""
import click

from vivarium_cluster_tools import logs
from vivarium_cluster_tools.vipin import perf_report


@click.command()
@click.argument("logs-directory", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--result-directory",
    "-o",
    type=click.Path(exists=True, file_okay=False),
    help="The directory into which to write the summary of the parsed logs. "
    "Defaults to given logs directory if not given.",
)
@click.option(
    "--hdf/--csv", default=False, help="Choose hdf or csv for output data. Defaults to csv."
)
@click.option("-v", "verbose", count=True, help="Configure logging verbosity.")
def vipin(logs_directory, result_directory, hdf, verbose):
    """Get performance information from worker_logs from a ``psimulate`` command.

    Given a worker logs directory from a previous run, a summary csv will be
    created in the ``result_directory`` (which defaults to the given logs
    directory unless otherwise specified).
    """
    logs.configure_main_process_logging_to_terminal(verbose, process_name="vipin")
    if not result_directory:
        result_directory = logs_directory

    perf_report.report_performance(logs_directory, result_directory, hdf, verbose)
