"""Main application functions for building artifacts.

.. admonition::

   Logging in this module should typically be done at the ``info`` level.
   Use your best judgement.

"""
import shutil
import sys
import time
import click

from pathlib import Path
from typing import Tuple, Union
from loguru import logger

import vivarium_cluster_tools as vct

from {{cookiecutter.package_name}}.constants import data_keys, metadata
from {{cookiecutter.package_name}}.utilities import sanitize_location, delete_if_exists, len_longest_location
from {{cookiecutter.package_name}}.tools.app_logging import add_logging_sink, decode_status


def running_from_cluster() -> bool:

    import vivarium_cluster_tools as vct
    on_cluster = True

    try:
        vct.get_cluster_name()
    except:
        on_cluster = False
    return on_cluster


def check_for_existing(output_dir: Path, location: str, append: bool, replace_keys: Tuple) -> None:
    existing_artifacts = set([item.stem for item in output_dir.iterdir()
                              if item.is_file() and item.suffix == '.hdf'])
    locations = set([sanitize_location(loc) for loc in metadata.LOCATIONS])
    existing = locations.intersection(existing_artifacts)

    if existing:
        if location != 'all':
            existing = [sanitize_location(location)]
        if not append:
            click.confirm(
                f'Existing artifacts found for {existing}. Do you want to delete and rebuild?',
                abort=True
            )
            for loc in existing:
                path = output_dir / f'{loc}.hdf'
                logger.info(f'Deleting artifact at {str(path)}.')
                path.unlink(missing_ok=True)
        elif replace_keys:
            click.confirm(
                f'Existing artifacts found for {existing}. If the listed keys {replace_keys} '
                'exist, they will be deleted and regenerated. Do you want to delete and regenerate '
                'them?',
                abort=True
            )


def build_single(location: str, output_dir: str, replace_keys: Tuple) -> None:
    path = Path(output_dir) / f'{sanitize_location(location)}.hdf'
    build_single_location_artifact(path, location, replace_keys)


def build_artifacts(
        location: str, output_dir: str, append: bool, replace_keys: Tuple, verbose: int
) -> None:
    """Main application function for building artifacts.
    Parameters
    ----------
    location
        The location to build the artifact for.  Must be one of the
        locations specified in the project globals or the string 'all'.
        If the latter, this application will build all artifacts in
        parallel.
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
    vct.mkdir(output_dir, parents=True, exists_ok=True)

    check_for_existing(Path(output_dir), location, append, replace_keys)

    if location in metadata.LOCATIONS:
        build_single(location, output_dir, replace_keys)
    elif location == 'all':
        if running_from_cluster():
            # parallel build when on cluster
            build_all_artifacts(output_dir, verbose)
        else:
            # serial build when not on cluster
            for loc in metadata.LOCATIONS:
                build_single(loc, output_dir, replace_keys)
    else:
        raise ValueError(f'Location must be one of {metadata.LOCATIONS} or the string "all". '
                         f'You specified {location}.')


def build_all_artifacts(output_dir: Path, verbose: int) -> None:
    """Builds artifacts for all locations in parallel.
    Parameters
    ----------
    output_dir
        The directory where the artifacts will be built.
    verbose
        How noisy the logger should be.
    Note
    ----
        This function should not be called directly.  It is intended to be
        called by the :func:`build_artifacts` function located in the same
        module.
    """
    from vivarium_cluster_tools.psimulate.utilities import get_drmaa
    drmaa = get_drmaa()

    jobs = {}
    with drmaa.Session() as session:
        for location in metadata.LOCATIONS:
            path = output_dir / f'{sanitize_location(location)}.hdf'

            job_template = session.createJobTemplate()
            job_template.remoteCommand = shutil.which("python")
            job_template.args = [__file__, str(path), f'"{location}"']
            job_template.nativeSpecification = (f'-V '  # Export all environment variables
                                                f'-b y '  # Command is a binary (python)
                                                f'-P {metadata.CLUSTER_PROJECT} '  
                                                f'-q {metadata.CLUSTER_QUEUE} '  
                                                f'-l fmem={metadata.MAKE_ARTIFACT_MEM} '
                                                f'-l fthread={metadata.MAKE_ARTIFACT_CPU} '
                                                f'-l h_rt={metadata.MAKE_ARTIFACT_RUNTIME} '
                                                f'-l archive=TRUE '  # Need J-drive access for data
                                                f'-N {sanitize_location(location)}_artifact')  # Name of the job
            jobs[location] = (session.runJob(job_template), drmaa.JobState.UNDETERMINED)
            logger.info(f'Submitted job {jobs[location][0]} to build artifact for {location}.')
            session.deleteJobTemplate(job_template)

        if verbose:
            logger.info('Entering monitoring loop.')
            logger.info('-------------------------')
            logger.info('')

            while any([job[1] not in [drmaa.JobState.DONE, drmaa.JobState.FAILED] for job in jobs.values()]):
                for location, (job_id, status) in jobs.items():
                    jobs[location] = (job_id, session.jobStatus(job_id))
                    logger.info(f'{location:<35}: {decode_status(drmaa, jobs[location][1]):>15}')
                logger.info('')
                time.sleep(metadata.MAKE_ARTIFACT_SLEEP)
                logger.info('Checking status again')
                logger.info('---------------------')
                logger.info('')

    logger.info('**Done**')


def build_single_location_artifact(
        path: Union[str, Path], location: str, replace_keys: Tuple = (), log_to_file: bool = False
) -> None:
    """Builds an artifact for a single location.
    Parameters
    ----------
    path
        The full path to the artifact to build.
    location
        The location to build the artifact for.  Must be one of the locations
        specified in the project globals.
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
        log_file = path.parent / 'logs' / f'{sanitize_location(location)}.log'
        if log_file.exists():
            log_file.unlink()
        add_logging_sink(log_file, verbose=2)

    # Local import to avoid data dependencies
    from {{cookiecutter.package_name}}.data import builder

    logger.info(f'Building artifact for {location} at {str(path)}.')
    artifact = builder.open_artifact(path, location)

    for key_group in data_keys.MAKE_ARTIFACT_KEY_GROUPS:
        logger.info(f'Loading and writing {key_group.log_name} data')
        for key in key_group:
            logger.info(f'   - Loading and writing {key} data')
            builder.load_and_write_data(artifact, key, location, key in replace_keys)

    logger.info(f'**Done building -- {location}**')


if __name__ == "__main__":
    artifact_path = sys.argv[1]
    artifact_location = sys.argv[2]
    build_single_location_artifact(artifact_path, artifact_location, log_to_file=True)
