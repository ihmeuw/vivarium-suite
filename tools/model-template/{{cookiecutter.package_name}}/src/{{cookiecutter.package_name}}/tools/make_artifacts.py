"""Main application functions for building artifacts.

.. admonition::

   Logging in this module should typically be done at the ``info`` level.
   Use your best judgement.

"""
import shutil
import sys
import time
from pathlib import Path

import click
from loguru import logger

from {{cookiecutter.package_name}}.constants import data_keys, metadata
from {{cookiecutter.package_name}}.tools.app_logging import (
    add_logging_sink,
    decode_status
)
from {{cookiecutter.package_name}}.utilities import sanitize_location


def running_from_cluster() -> bool:
    import vivarium_cluster_tools as vct

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
    import vivarium_cluster_tools as vct

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
    """Builds artifacts for all locations in parallel.
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
    from vivarium_cluster_tools.utilities import get_drmaa

    drmaa = get_drmaa()

    jobs = {}
    with drmaa.Session() as session:
        for location in metadata.LOCATIONS:
            location_cleaned = sanitize_location(location)
            path = output_dir / f"{location_cleaned}.hdf"

            job_template = session.createJobTemplate()
            job_template.remoteCommand = shutil.which("python")
            job_template.args = [__file__, str(path), f'"{location}"', str(years)]
            job_template.jobEnvironment = {
                "LC_ALL": "en_US.UTF-8",
                "LANG": "en_US.UTF-8",
            }
            job_template.nativeSpecification = (
                f"-A {metadata.CLUSTER_PROJECT} "
                f"-p {metadata.CLUSTER_QUEUE} "
                f"--mem={metadata.MAKE_ARTIFACT_MEM*1024} "
                f"-c {metadata.MAKE_ARTIFACT_CPU} "
                f"-t {metadata.MAKE_ARTIFACT_RUNTIME} "
                f"-C archive "  # Need J-drive access for data
                f"-J {location_cleaned}_artifact"  # Name of the job
            )
            jobs[location] = (session.runJob(job_template), drmaa.JobState.UNDETERMINED)
            logger.info(
                f"Submitted job {jobs[location][0]} to build artifact for {location}."
            )
            session.deleteJobTemplate(job_template)

        if verbose:
            logger.info("Entering monitoring loop.")
            logger.info("-------------------------")
            logger.info("")

            while any(
                [
                    job[1] not in [drmaa.JobState.DONE, drmaa.JobState.FAILED]
                    for job in jobs.values()
                ]
            ):
                for location, (job_id, status) in jobs.items():
                    jobs[location] = (job_id, session.jobStatus(job_id))
                    logger.info(
                        f"{location:<35}: {decode_status(drmaa, jobs[location][1]):>15}"
                    )
                logger.info("")
                time.sleep(metadata.MAKE_ARTIFACT_SLEEP)
                logger.info("Checking status again")
                logger.info("---------------------")
                logger.info("")

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
    replace_keys
        A list of keys to replace in the artifact. Is ignored if append is
        False or if there is no existing artifact at the output location
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
    from {{cookiecutter.package_name}}.data import builder

    logger.info(f"Building artifact for {location} at {str(path)}.")
    artifact = builder.open_artifact(path, location)

    for key_group in data_keys.MAKE_ARTIFACT_KEY_GROUPS:
        logger.info(f"Loading and writing {key_group.log_name} data")
        for key in key_group:
            logger.info(f"   - Loading and writing {key} data")
            builder.load_and_write_data(artifact, key, location, years, key in replace_keys)

    logger.info(f"**Done building -- {location}**")


if __name__ == "__main__":
    artifact_path = sys.argv[1]
    artifact_location = sys.argv[2]
    artifact_years = None if sys.argv[3] == "None" else sys.argv[3]

    build_single_location_artifact(
        path=artifact_path, location=artifact_location, years=artifact_years, log_to_file=True
    )
