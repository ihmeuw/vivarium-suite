import click
from loguru import logger
from vivarium.framework.utilities import handle_exceptions

from {{cookiecutter.package_name}}.constants import metadata, paths
from {{cookiecutter.package_name}}.tools import (
    build_artifacts,
    configure_logging_to_terminal
)


@click.command()
@click.option(
    "-l",
    "--location",
    default="all",
    show_default=True,
    type=click.Choice(metadata.LOCATIONS + ["all"]),
    help=(
        "Location for which to make an artifact. Note: prefer building archives on the cluster.\n"
        'If you specify location "all" you must be on a cluster node.'
    ),
)
@click.option(
    "--years",
    default=None,
    help=(
        "Years for which to make an artifact. Can be a single year or 'all'. \n"
        "If not specified, make for most recent year."
    ),
)
@click.option(
    "-o",
    "--output-dir",
    default=str(paths.ARTIFACT_ROOT),
    show_default=True,
    type=click.Path(),
    help="Specify an output directory. Directory must exist.",
)
@click.option(
    "-a", "--append", is_flag=True, help="Append to the artifact instead of overwriting."
)
@click.option("-r", "--replace-keys", multiple=True, help="Specify keys to overwrite")
@click.option("-v", "verbose", count=True, help="Configure logging verbosity.")
@click.option(
    "--pdb",
    "with_debugger",
    is_flag=True,
    help="Drop into python debugger if an error occurs.",
)
def make_artifacts(
    location: str,
    years: str | None,
    output_dir: str,
    append: bool,
    replace_keys: tuple[str, ...],
    verbose: int,
    with_debugger: bool,
) -> None:
    configure_logging_to_terminal(verbose)
    main = handle_exceptions(build_artifacts, logger, with_debugger=with_debugger)
    main(location, years, output_dir, append or replace_keys, replace_keys, verbose)
