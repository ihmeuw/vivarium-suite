"""
=============
psimulate CLI
=============

Command line interface for `psimulate`.

.. click:: vivarium_cluster_tools.psimulate.cli:psimulate
   :prog: psimulate
   :show-nested:

"""
from pathlib import Path
from typing import Optional

import click
from loguru import logger
from vivarium.framework.utilities import handle_exceptions

from vivarium_cluster_tools import cli_tools, logs
from vivarium_cluster_tools.psimulate import (
    COMMANDS,
    cluster,
    paths,
    redis_dbs,
    results,
    runner,
)
from vivarium_cluster_tools.psimulate.worker.load_test_work_horse import (
    get_psimulate_test_dict,
)


@click.group()
def psimulate():
    """A command line utility for running many simulations in parallel.

    You may initiate a new run with the ``run`` sub-command or restart a run
    from where it was stopped by using the ``restart`` sub-command.

    """
    pass


shared_options = [
    cluster.with_project,
    cluster.with_queue_and_max_runtime,
    cluster.with_peak_memory,
    redis_dbs.with_redis,
    results.with_no_batch,
    cli_tools.with_verbose_and_pdb,
]


@psimulate.command()
@click.argument(
    "model_specification",
    type=click.Path(exists=True, dir_okay=False),
    callback=cli_tools.coerce_to_full_path,
)
@click.argument(
    "branch_configuration",
    type=click.Path(exists=True, dir_okay=False),
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--artifact_path",
    "-i",
    type=click.Path(exists=True, dir_okay=False),
    help="The path to the artifact data file.",
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--result-directory",
    "-o",
    type=click.Path(file_okay=False),
    default=paths.DEFAULT_OUTPUT_DIRECTORY,
    show_default=True,
    help="The directory to write results to. A folder will be "
    "created in this directory with the same name as the "
    "configuration file.",
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--max-workers",
    type=click.IntRange(min=1),
    help="The maximum number of workers (and therefore jobs) to run concurrently."
    "If unset, submit all jobs at once and let the cluster schedule them.",
)
@cli_tools.pass_shared_options(shared_options)
def run(
    model_specification: Path,
    branch_configuration: Path,
    artifact_path: Optional[Path],
    result_directory: Optional[Path],
    **options,
) -> None:
    """Run a parallel simulation.

    The simulation itself is defined by a MODEL_SPECIFICATION yaml file
    and the parameter changes across runs are defined by a BRANCH_CONFIGURATION
    yaml file.

    The path to the data artifact can be provided as an argument here, in the
    branch configuration, or in the model specification file. Values provided as
    a command line argument or in the branch specification file will override a
    value specified in the model specifications file. If an artifact path is
    provided both as a command line argument and to the branch configuration file
    a ConfigurationError will be thrown.

    Within the provided or default results directory, a subdirectory will be
    created with the same name as the MODEL_SPECIFICATION if one does not exist.
    Results will be written to a further subdirectory named after the start time
    of the simulation run.

    """
    logs.configure_main_process_logging_to_terminal(options["verbose"])
    main = handle_exceptions(runner.main, logger, options["with_debugger"])

    main(
        command=COMMANDS.run,
        input_paths=paths.InputPaths.from_entry_point_args(
            input_model_specification_path=model_specification,
            input_branch_configuration_path=branch_configuration,
            input_artifact_path=artifact_path,
            result_directory=result_directory,
        ),
        native_specification=cluster.NativeSpecification(
            job_name=model_specification.stem,
            project=options["project"],
            queue=options["queue"],
            peak_memory=options["peak_memory"],
            max_runtime=options["max_runtime"],
        ),
        redis_processes=options["redis"],
        no_batch=options["no_batch"],
        extra_args={"max_workers": options["max_workers"]},
    )


@psimulate.command()
@click.argument(
    "results-root",
    type=click.Path(exists=True, file_okay=False, writable=True),
    callback=cli_tools.coerce_to_full_path,
)
@cli_tools.pass_shared_options(shared_options)
def restart(results_root, **options):
    """Restart a parallel simulation from a previous run at RESULTS_ROOT.

    Restarting will not erase existing results, but will start workers to
    perform the remaining simulations.  RESULTS_ROOT is expected to be an
    output directory from a previous ``psimulate run`` invocation.

    """
    logs.configure_main_process_logging_to_terminal(options["verbose"])
    main = handle_exceptions(runner.main, logger, options["with_debugger"])

    main(
        command=COMMANDS.restart,
        input_paths=paths.InputPaths.from_entry_point_args(
            result_directory=results_root,
        ),
        native_specification=cluster.NativeSpecification(
            job_name=results_root.parent.name,
            project=options["project"],
            queue=options["queue"],
            peak_memory=options["peak_memory"],
            max_runtime=options["max_runtime"],
        ),
        redis_processes=options["redis"],
        no_batch=options["no_batch"],
        extra_args={},
    )


@psimulate.command()
@click.argument(
    "results-root",
    type=click.Path(exists=True, file_okay=False, writable=True),
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--add-draws",
    type=int,
    default=0,
    help="The number of input draws to add to a previous run.",
)
@click.option(
    "--add-seeds",
    type=int,
    default=0,
    help="The number of random seeds to add to a previous run.",
)
@cli_tools.pass_shared_options(shared_options)
def expand(results_root, **options):
    """Expand a previous run at RESULTS_ROOT by adding input draws and/or
    random seeds.

    Expanding will not erase existing results, but will start workers to perform
    the additional simulations determined by the added draws/seeds.
    RESULTS_ROOT is expected to be an output directory from a previous
    ``psimulate run`` invocation.

    """
    logs.configure_main_process_logging_to_terminal(options["verbose"])
    main = handle_exceptions(runner.main, logger, options["with_debugger"])

    main(
        command=COMMANDS.expand,
        input_paths=paths.InputPaths.from_entry_point_args(
            result_directory=results_root,
        ),
        native_specification=cluster.NativeSpecification(
            job_name=results_root.parent.name,
            project=options["project"],
            queue=options["queue"],
            peak_memory=options["peak_memory"],
            max_runtime=options["max_runtime"],
        ),
        redis_processes=options["redis"],
        no_batch=options["no_batch"],
        extra_args={
            "num_draws": options["add_draws"],
            "num_seeds": options["add_seeds"],
        },
    )


@psimulate.command()
@click.argument(
    "test-type",
    type=click.Choice(list(get_psimulate_test_dict())),
)
@click.option(
    "--num-workers",
    "-n",
    type=click.INT,
    default=1000,
)
@click.option(
    "--result-directory",
    "-o",
    type=click.Path(file_okay=False),
    default=f"{paths.DEFAULT_OUTPUT_DIRECTORY}/load_tests",
    callback=cli_tools.coerce_to_full_path,
)
@cli_tools.pass_shared_options(shared_options)
def test(test_type, num_workers, result_directory, **options):
    logs.configure_main_process_logging_to_terminal(options["verbose"])
    main = handle_exceptions(runner.main, logger, options["with_debugger"])
    main(
        command=COMMANDS.load_test,
        input_paths=paths.InputPaths.from_entry_point_args(
            result_directory=result_directory,
        ),
        native_specification=cluster.NativeSpecification(
            job_name=f"load_test_{test_type}",
            project=options["project"],
            queue=options["queue"],
            peak_memory=options["peak_memory"],
            max_runtime=options["max_runtime"],
        ),
        redis_processes=options["redis"],
        no_batch=options["no_batch"],
        extra_args={
            "test_type": test_type,
            "num_workers": num_workers,
        },
    )
