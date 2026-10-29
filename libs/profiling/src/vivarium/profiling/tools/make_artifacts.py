"""Main application functions for building artifacts.

.. admonition::

   Logging in this module should typically be done at the ``info`` level.
   Use your best judgement.

"""
import sys
import time
from pathlib import Path

import click
from loguru import logger

from vivarium.profiling.constants import data_keys, metadata
from vivarium.profiling.tools.app_logging import add_logging_sink
from vivarium.profiling.utilities import sanitize_location


def running_from_cluster() -> bool:
    import vivarium.cluster_tools as vct

    return "slurm" in vct.get_cluster_name()


def check_for_existing(
    output_dir: Path, location: str, append: bool, replace_keys: tuple
) -> None:
    existing_artifacts = set(
        [
            item.stem
            for item in output_dir.iterdir()
            if item.is_file() and item.suffix == ".hdf"
        ]
    )
    location = sanitize_location(location)
    if location == "all":
        locations = set([sanitize_location(loc) for loc in metadata.LOCATIONS])
        existing = locations.intersection(existing_artifacts)
    else:
        existing = [location] if location in existing_artifacts else None

    if existing:
        if not append:
            click.confirm(
                f"Existing artifacts found for {existing}. Do you want to delete and rebuild?",
                abort=True,
            )
            for loc in existing:
                path = output_dir / f"{loc}.hdf"
                logger.info(f"Deleting artifact at {str(path)}.")
                path.unlink(missing_ok=True)
        elif replace_keys:
            click.confirm(
                f"Existing artifacts found for {existing}. If the listed keys {replace_keys} "
                "exist, they will be deleted and regenerated. Do you want to delete and regenerate "
                "them?",
                abort=True,
            )


def build_single(
    location: str, years: str | None, output_dir: str, replace_keys: tuple
) -> None:
    path = Path(output_dir) / f"{sanitize_location(location)}.hdf"
    build_single_location_artifact(path, location, years, replace_keys)


def build_artifacts(
    location: str,
    years: str | None,
    output_dir: str,
    append: bool,
    replace_keys: tuple,
    verbose: int,
) -> None:
    """Main application function for building artifacts.
    Parameters
    ----------
    location
        The location to build the artifact for.  Must be one of the
        locations specified in the project globals or the string 'all'.
        If the latter, this application will build all artifacts in
        parallel.
    years
        Years for which to make an artifact. Can be a single year or 'all'.
        If not specified, make for most recent year.
    output_dir
        The path where the artifact files will be built. The directory
        will be created if it doesn't exist
    append
        Whether we should append to existing artifacts at the given output
        directory.  Has no effect if artifacts are not found.
    replace_keys
        A list of keys to replace in the artifact. Is ignored if append is
        False or if there is no existing artifact at the output location
    verbose
        How noisy the logger should be.
    """
    import vivarium.cluster_tools as vct

    output_dir = Path(output_dir)
    vct.mkdir(output_dir, parents=True, exists_ok=True)

    check_for_existing(output_dir, location, append, replace_keys)

    if location in metadata.LOCATIONS:
        build_single(location, years, output_dir, replace_keys)
    elif location == "all":
        if running_from_cluster():
            # parallel build when on cluster
            build_all_artifacts(output_dir, years, verbose)
        else:
            # serial build when not on cluster
            for loc in metadata.LOCATIONS:
                build_single(loc, years, output_dir, replace_keys)
    else:
        raise ValueError(
            f'Location must be one of {metadata.LOCATIONS} or the string "all". '
            f"You specified {location}."
        )


def build_all_artifacts(output_dir: Path, years: str | None, verbose: int) -> None:
    """Build artifacts for all locations in parallel via a Jobmon workflow.

    Fan one independent task out per location into a single Jobmon workflow so
    the locations build concurrently on SLURM. Each task re-invokes this
    module's ``__main__`` entry point to build one location's artifact.

    Parameters
    ----------
    output_dir
        The directory where the artifacts will be built.
    years
        Years for which to make an artifact. Can be a single year or 'all'.
        If not specified, make for most recent year.
    verbose
        How noisy the logger should be.

    Note
    ----
        This function should not be called directly.  It is intended to be
        called by the :func:`build_artifacts` function located in the same
        module.
    """
    from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
    from vivarium.cluster_tools.core.jobmon.artifact import build_artifacts_in_parallel

    worker_logging_root = output_dir / "logs"
    worker_logging_root.mkdir(parents=True, exist_ok=True)

    workflow_name = f"make_artifacts_{int(time.time())}"

    python = sys.executable
    this_file = Path(__file__).resolve()
    build_commands: dict[str, str] = {}
    for location in metadata.LOCATIONS:
        location_cleaned = sanitize_location(location)
        artifact_path = output_dir / f"{location_cleaned}.hdf"
        build_commands[
            f"{location_cleaned}_artifact"
        ] = f'{python} {this_file} "{artifact_path}" "{location}" {years}'

    native_specification = NativeSpecification(
        job_name="make_artifacts",
        project=metadata.CLUSTER_PROJECT,
        queue=metadata.CLUSTER_QUEUE,
        peak_memory=metadata.MAKE_ARTIFACT_MEM,
        max_runtime=metadata.MAKE_ARTIFACT_RUNTIME,
        hardware=[],
        cores=metadata.MAKE_ARTIFACT_CPU,
        requires_archive_node=True,  # need archive-node (J-drive) access for input data
    )

    _, monitoring_url = build_artifacts_in_parallel(
        workflow_name=workflow_name,
        build_commands=build_commands,
        native_specification=native_specification,
        worker_logging_root=worker_logging_root,
        env_prefix=sys.prefix,
        resume=False,
        max_concurrently_running=len(build_commands),
    )

    logger.info(f"Built artifacts for {len(build_commands)} locations.")
    if monitoring_url:
        logger.info(f"Monitor progress in the Jobmon GUI at: {monitoring_url}")
    logger.info("**Done**")


def build_single_location_artifact(
    path: str | Path,
    location: str,
    years: str | None,
    replace_keys: tuple = (),
    log_to_file: bool = False,
) -> None:
    """Builds an artifact for a single location.
    Parameters
    ----------
    path
        The full path to the artifact to build.
    location
        The location to build the artifact for.  Must be one of the locations
        specified in the project globals.
    years
        Years for which to make an artifact. Can be a single year or 'all'.
        If not specified, make for most recent year.
    log_to_file
        Whether we should write the application logs to a file.
    Note
    ----
        This function should not be called directly.  It is intended to be
        called by the :func:`build_artifacts` function located in the same
        module.
    """
    location = location.strip('"')
    path = Path(path)
    if log_to_file:
        log_file = path.parent / "logs" / f"{sanitize_location(location)}.log"
        if log_file.exists():
            log_file.unlink()
        add_logging_sink(log_file, verbose=2)

    # Local import to avoid data dependencies
    from vivarium.profiling.data import builder

    logger.info(f"Building artifact for {location} at {str(path)}.")
    artifact = builder.open_artifact(path, location)

    for key_group in data_keys.MAKE_ARTIFACT_KEY_GROUPS:
        logger.info(f"Loading and writing {key_group.log_name} data")
        for key in key_group:
            logger.info(f"   - Loading and writing {key} data")
            builder.load_and_write_data(artifact, key, location, years, key in replace_keys)

    logger.info(f"**Done building -- {location}**")


def _main(argv: list[str]) -> None:
    """Build one location's artifact from a parallel build task's command-line arguments."""
    artifact_path = argv[1]
    artifact_location = argv[2]
    artifact_years = None if argv[3] == "None" else argv[3]

    build_single_location_artifact(
        path=artifact_path, location=artifact_location, years=artifact_years, log_to_file=True
    )


if __name__ == "__main__":
    _main(sys.argv)
