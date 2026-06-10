"""
==========
dagger CLI
==========

Command line interface for ``dagger``.

.. click:: vivarium_cluster_tools.dagger.cli:dagger
   :prog: dagger
   :show-nested:

"""

from pathlib import Path

import click
from loguru import logger
from vivarium.engine.framework.utilities import handle_exceptions

from vivarium_cluster_tools.core import cli_tools, logs
from vivarium_cluster_tools.dagger import runner
from vivarium_cluster_tools.dagger.config.parsing import load_workflow_config


@click.group()
def dagger() -> None:
    """A command line utility for running multi-step Jobmon workflows.

    Workflows are defined by a YAML configuration file that lists each
    step's command, compute resources, and conda environment. Use the
    ``run`` sub-command to launch a fresh workflow, or ``restart`` to resume
    a previously started workflow from its output directory.
    """
    pass


@dagger.command()
@cli_tools.with_workflow_config
@click.option(
    "--name",
    "-n",
    default=None,
    help="Override workflow name from config file.",
)
@click.option(
    "--project",
    "-P",
    default=None,
    help="Override project from config file.",
)
@click.option(
    "--queue",
    "-q",
    default=None,
    help="Override queue from config file.",
)
@click.option(
    "--output-directory",
    "-o",
    type=click.Path(file_okay=False),
    default=None,
    help="Override output directory from config file.",
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--default-environment",
    "-e",
    default=None,
    help="Override default_environment from config file.",
)
@click.option(
    "--max-attempts",
    "-m",
    type=click.IntRange(min=1),
    default=None,
    help="Override maximum Jobmon task attempts from config file.",
)
@cli_tools.with_slack_channel
@cli_tools.with_slack_tag
@cli_tools.with_slack_mute
@cli_tools.with_verbose_and_pdb
def run(
    config_path: Path,
    name: str | None,
    project: str | None,
    queue: str | None,
    output_directory: Path | None,
    default_environment: str | None,
    max_attempts: int | None,
    slack_channel: str | None,
    slack_tag: str | None,
    mute_slack: bool,
    verbose: int,
    with_debugger: bool,
) -> None:
    """Run a multi-step Jobmon workflow.

    The workflow is defined in a workflow configuration YAML file
    specified via the -c/--config option. The config file specifies
    all workflow steps, compute resources, and execution order.

    Every top-level configuration value other than ``steps`` can be
    overridden from the command line via the corresponding flag.
    """
    logs.configure_main_process_logging_to_terminal(verbose)
    cli_tools.validate_slack_options(slack_channel, slack_tag, mute_slack)

    workflow_config = load_workflow_config(
        config_path,
        name=name,
        project=project,
        queue=queue,
        output_directory=output_directory,
        default_environment=default_environment,
        max_attempts=max_attempts,
    )

    main = handle_exceptions(runner.run_workflow, logger, with_debugger)

    main(
        workflow_config=workflow_config,
        verbose=verbose,
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
    )


@dagger.command()
@click.argument(
    "results_directory",
    type=click.Path(exists=True, file_okay=False, writable=True),
    callback=cli_tools.coerce_to_full_path,
)
@click.option(
    "--project",
    "-P",
    default=None,
    help="Override project from the saved configuration.",
)
@click.option(
    "--queue",
    "-q",
    default=None,
    help="Override queue from the saved configuration.",
)
@click.option(
    "--max-attempts",
    "-m",
    type=click.IntRange(min=1),
    default=None,
    help="Override maximum Jobmon task attempts from the saved configuration.",
)
@cli_tools.with_slack_channel
@cli_tools.with_slack_tag
@cli_tools.with_slack_mute
@cli_tools.with_verbose_and_pdb
def restart(
    results_directory: Path,
    project: str | None,
    queue: str | None,
    max_attempts: int | None,
    slack_channel: str | None,
    slack_tag: str | None,
    mute_slack: bool,
    verbose: int,
    with_debugger: bool,
) -> None:
    """Restart a previously started workflow.

    RESULTS_DIRECTORY is the output directory of a previous ``dagger run``.
    Reloads the saved configuration and persisted Jobmon workflow and resumes
    it, skipping tasks that already completed. ``--project``, ``--queue``, and
    ``--max-attempts`` override the saved configuration.
    """
    logs.configure_main_process_logging_to_terminal(verbose)
    cli_tools.validate_slack_options(slack_channel, slack_tag, mute_slack)

    main = handle_exceptions(runner.restart_workflow, logger, with_debugger)

    main(
        results_directory=results_directory,
        project=project,
        queue=queue,
        max_attempts=max_attempts,
        verbose=verbose,
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
    )
