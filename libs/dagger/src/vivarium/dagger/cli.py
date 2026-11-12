"""
==========
dagger CLI
==========

Command line interface for ``dagger``.

``-c/--config`` names either a workflow YAML file (``.yaml`` or ``.yml``) or a Python
pipeline definition (``path/to/file.py`` or ``package.module``, either optionally
followed by ``:function``), and the command uses the matching frontend. A YAML workflow
runs through Jobmon as :doc:`/dagger` describes. A pipeline definition is run, previewed,
or forced as :doc:`/python_pipelines` describes; ``run`` and ``force`` run its stale steps
through Jobmon, or in this process with ``--local``.

.. click:: vivarium.dagger.cli:dagger
   :prog: dagger
   :show-nested:

"""

from collections.abc import Callable
from pathlib import Path

import click
from click.core import ParameterSource
from loguru import logger
from vivarium.cluster_tools.core import cli_tools, logs
from vivarium.cluster_tools.core.cli_tools import handle_exceptions

from vivarium.dagger.frontend import cli as definitions

# The YAML commands import vivarium.dagger.runner and vivarium.dagger.config inside their
# bodies, because both import Jobmon, which only the ``cluster`` extra installs. A
# pipeline definition imports Jobmon only when the bridge runs its workflow, so a preview
# and a run with --local need no Jobmon.

_YAML_SUFFIXES = (".yaml", ".yml")
"""Suffixes of a ``-c`` value that names a workflow YAML file, not a pipeline definition."""

_JOBMON_OPTIONS = ("project", "queue", "max_attempts")
"""Options that apply to a workflow YAML file and to a pipeline definition run through
Jobmon, but not to a pipeline definition run with ``--local``."""


def _is_yaml(config: str | Path) -> bool:
    """Return whether ``config`` names a workflow YAML file."""
    return Path(config).suffix.lower() in _YAML_SUFFIXES


def _resolve_config(ctx: click.Context, param: click.Parameter, value: str) -> Path | str:
    """Return a workflow YAML file as its full path, checked to exist; leave a definition as given."""
    if _is_yaml(value):
        path_type = click.Path(exists=True, dir_okay=False, path_type=Path)
        converted = path_type.convert(value, param, ctx)
        # path_type=Path makes convert return a Path; older click stubs type it more widely.
        assert isinstance(converted, Path)
        return converted.resolve()
    return value


def _with_config(func: cli_tools.CLIFunction) -> cli_tools.CLIFunction:
    """Add the ``--config/-c`` option for a workflow YAML file or a pipeline definition."""
    return click.option(
        "--config",
        "-c",
        "config",
        required=True,
        callback=_resolve_config,
        is_eager=True,
        help=(
            "A workflow YAML file (.yaml or .yml), or a Python pipeline definition: "
            "path/to/file.py or package.module, optionally followed by :function."
        ),
    )(func)


def _with_until(func: cli_tools.CLIFunction) -> cli_tools.CLIFunction:
    """Add the ``--until`` option, which names steps of a pipeline definition."""
    return click.option(
        "--until",
        multiple=True,
        metavar="STEP",
        help=(
            "Pipeline definitions only. Use only STEP and the steps it depends on. "
            "Repeat to name more steps."
        ),
    )(func)


def _with_local(func: cli_tools.CLIFunction) -> cli_tools.CLIFunction:
    """Add the ``--local`` flag, which runs a pipeline definition in this process."""
    return click.option(
        "--local",
        is_flag=True,
        help=(
            "Pipeline definitions only. Run the steps in this process. Without it, they "
            "run through Jobmon."
        ),
    )(func)


def _reject_given(ctx: click.Context, names: tuple[str, ...], accepts: str) -> None:
    """Raise a usage error if any option in ``names`` was given; only ``accepts`` takes it."""
    for param in ctx.command.params:
        if (
            param.name in names
            and ctx.get_parameter_source(param.name) is ParameterSource.COMMANDLINE
        ):
            raise click.UsageError(f"{param.opts[0]} applies only to {accepts}.", ctx)


def _with_jobmon_options(
    yaml: bool,
) -> Callable[[cli_tools.CLIFunction], cli_tools.CLIFunction]:
    """Return a decorator that adds -P, -q and -m; ``yaml`` adds their YAML help."""

    def help_text(yaml_text: str, definition_text: str) -> str:
        if yaml:
            return (
                f"For a workflow YAML file, {yaml_text} For a pipeline definition run "
                f"through Jobmon, {definition_text}"
            )
        return f"For a run through Jobmon, {definition_text}"

    def decorate(func: cli_tools.CLIFunction) -> cli_tools.CLIFunction:
        for option in (
            click.option(
                "--max-attempts",
                "-m",
                type=click.IntRange(min=1),
                default=None,
                help=help_text(
                    "override the maximum Jobmon task attempts from the config file.",
                    "how many times Jobmon tries each step.",
                ),
            ),
            click.option(
                "--queue",
                "-q",
                default=None,
                help=help_text(
                    "override the queue from the config file.",
                    "the queue of every step that does not set its own.",
                ),
            ),
            click.option(
                "--project",
                "-P",
                default=None,
                help=help_text(
                    "override the project from the config file.",
                    "the project of every step that does not set its own.",
                ),
            ),
        ):
            func = option(func)
        return func

    return decorate


def _require_definition(ctx: click.Context, config: Path | str) -> str:
    """Return ``config`` if it names a pipeline definition; otherwise raise a usage error."""
    if _is_yaml(config):
        raise click.UsageError(
            f"'dagger {ctx.command.name}' takes a Python pipeline definition, not a "
            "workflow YAML file.",
            ctx,
        )
    return str(config)


@click.group()
def dagger() -> None:
    """A command line utility for running multi-step pipelines.

    -c/--config names a workflow YAML file or a Python pipeline definition. A YAML
    file lists each step's command, compute resources, and environment (a conda env
    or a venv, given by name or prefix path), and runs as one Jobmon workflow; use
    ``run`` to launch it and ``restart`` to resume it from its output directory. A
    pipeline definition is a Python file or module with a function (by default
    ``pipeline``) that returns a Pipeline; ``run`` runs its stale steps, ``preview``
    shows which would run and why, and ``force`` runs named steps even if current.

    A pipeline definition's ``run`` and ``force`` go through Jobmon unless given
    ``--local``. -P/--project and -q/--queue then set the project and queue of every
    step that does not set its own, and -m/--max-attempts sets how many times Jobmon
    tries each step.

    For a pipeline definition, exit status is 0 on success; 1 if a step failed or
    exited, another run holds the pipeline's run lock, or the build stopped for another
    reason, possibly before any step ran; 2 if the definition or the command is wrong,
    or Jobmon cannot run here, and nothing ran; and 130 if the command was interrupted.
    """
    pass


_YAML_RUN_OPTIONS = (
    "name",
    "output_directory",
    "default_environment",
    "slack_channel",
    "slack_tag",
    "mute_slack",
    "verbose",
    "with_debugger",
)
"""``dagger run`` options that apply only to a workflow YAML file."""


@dagger.command()
@_with_config
@_with_until
@_with_local
@click.option(
    "--name",
    "-n",
    default=None,
    help="Override workflow name from config file.",
)
@_with_jobmon_options(yaml=True)
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
@cli_tools.with_slack_channel
@cli_tools.with_slack_tag
@cli_tools.with_slack_mute
@cli_tools.with_verbose_and_pdb
def run(
    config: Path | str,
    until: tuple[str, ...],
    local: bool,
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
    """Run a workflow YAML file through Jobmon, or the stale steps of a pipeline definition.

    For a workflow YAML file, every top-level configuration value other than
    ``steps`` can be overridden from the command line via the corresponding flag.
    For a pipeline definition, the stale steps run through Jobmon, or in this process
    with --local, and --until runs only the named steps and the steps they depend on.
    -P/--project, -q/--queue and -m/--max-attempts apply to a definition only when it
    runs through Jobmon. A step's own resources override -P and -q.
    """
    ctx = click.get_current_context()
    if not _is_yaml(config):
        _reject_given(ctx, _YAML_RUN_OPTIONS, "a workflow YAML file")
        if local:
            _reject_given(ctx, _JOBMON_OPTIONS, "a run through Jobmon, not with --local")
        definitions.run_definition(
            str(config),
            until,
            local=local,
            project=project,
            queue=queue,
            max_attempts=max_attempts,
        )
        return
    _reject_given(ctx, ("until", "local"), "a Python pipeline definition")

    from vivarium.dagger import runner
    from vivarium.dagger.config.parsing import load_workflow_config
    from vivarium.dagger.config.utilities import WORKFLOW_ARGS_FILENAME

    logs.configure_main_process_logging_to_terminal(verbose)
    cli_tools.validate_slack_options(slack_channel, slack_tag, mute_slack)

    workflow_config = load_workflow_config(
        Path(config),
        name=name,
        project=project,
        queue=queue,
        output_directory=output_directory,
        default_environment=default_environment,
        max_attempts=max_attempts,
    )

    if (workflow_config.output_directory / WORKFLOW_ARGS_FILENAME).exists():
        click.confirm(
            f"{workflow_config.output_directory} already contains a previous "
            "dagger run. Continuing will start a new run in this directory and "
            "may overwrite existing files there, including prior results and the "
            "files needed to restart the previous run. Continue?",
            abort=True,
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
    from vivarium.dagger import runner

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


@dagger.command()
@_with_config
@_with_until
def preview(config: Path | str, until: tuple[str, ...]) -> None:
    """Show which steps of a pipeline definition would run, and why.

    Nothing runs and no product is written. --until plans only the named steps and
    the steps they depend on.
    """
    definition = _require_definition(click.get_current_context(), config)
    definitions.preview_definition(definition, until)


@dagger.command()
@_with_config
@click.argument("steps", nargs=-1, required=True, metavar="STEP...")
@_with_local
@_with_jobmon_options(yaml=False)
def force(
    config: Path | str,
    steps: tuple[str, ...],
    local: bool,
    project: str | None,
    queue: str | None,
    max_attempts: int | None,
) -> None:
    """Run each STEP of a pipeline definition even if it is current.

    Every other stale step of the definition runs too. The steps run through Jobmon, or
    in this process with --local. -P/--project, -q/--queue and -m/--max-attempts are as
    for ``run`` and apply only to a run through Jobmon.
    """
    ctx = click.get_current_context()
    definition = _require_definition(ctx, config)
    if local:
        _reject_given(ctx, _JOBMON_OPTIONS, "a run through Jobmon, not with --local")
    definitions.force_definition(
        definition,
        steps,
        local=local,
        project=project,
        queue=queue,
        max_attempts=max_attempts,
    )
