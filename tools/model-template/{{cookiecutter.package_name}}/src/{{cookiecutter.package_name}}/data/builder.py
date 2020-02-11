"""Modularized functions for building project data artifacts.

This module is an abstraction around the load portion of our artifact building ETL pipeline.
The intent is to be declarative so it's easy to see what is put into the artifact and how.
Some degree of verbosity/boilerplate is fine in the interest of transparancy.

.. admonition::

   Logging in this module should be done at the ``debug`` level.

"""
from pathlib import Path

from loguru import logger
import pandas as pd
from vivarium.framework.artifact import Artifact, get_location_term, EntityKey
from vivarium_public_health.risks.data_transformations import pivot_categorical

from {{cookiecutter.package_name}} import globals as project_globals
from {{cookiecutter.package_name}}.data import loader


def open_artifact(output_path: Path, location: str) -> Artifact:
    """Creates or opens an artifact at the output path.

    Parameters
    ----------
    output_path
        Fully resolved path to the artifact file.
    location
        Proper GBD location name represented by the artifact.

    Returns
    -------
        A new artifact.

    """
    if not output_path.exists():
        logger.debug(f"Creating artifact at {str(output_path)}.")
    else:
        logger.debug(f"Opening artifact at {str(output_path)} for appending.")

    artifact = Artifact(output_path, filter_terms=[get_location_term(location)])

    key = project_globals.METADATA_LOCATIONS
    if key not in artifact:
        artifact.write(key, [location])

    return artifact


def load_and_write_data(artifact: Artifact, key: str, location: str):
    """Loads data and writes it to the artifact if not already present.

    Parameters
    ----------
    artifact
        The artifact to write to.
    key
        The entity key associated with the data to write.
    location
        The location associated with the data to load and the artifact to
        write to.

    """
    if key in artifact:
        logger.debug(f'Data for {key} already in artifact.  Skipping...')
    else:
        logger.debug(f'Loading data for {key} for location {location}.')
        data = loader.get_data(key, location)
        logger.debug(f'Writing data for {key} to artifact.')
        artifact.write(key, data)
    return artifact.load(key)


def write_data(artifact: Artifact, key: str, data: pd.DataFrame):
    """Writes data to the artifact if not already present.

    Parameters
    ----------
    artifact
        The artifact to write to.
    key
        The entity key associated with the data to write.
    data
        The data to write.

    """
    if key in artifact:
        logger.debug(f'Data for {key} already in artifact.  Skipping...')
    else:
        logger.debug(f'Writing data for {key} to artifact.')
        artifact.write(key, data)
    return artifact.load(key)

# TODO - writing and reading by draw is necessary if you are using
#        LBWSG data. If not, these functions can be removed.
def write_data_by_draw(artifact: Artifact, key: str, data: pd.DataFrame):
    """Writes data to the artifact on a per-draw basis. This is useful
    for large datasets like Low Birthweight Short Gestation (LBWSG).

    Parameters
    ----------
    artifact
        The artifact to write to.
    key
        The entity key associated with the data to write.
    data
        The data to write.

    """
    with pd.HDFStore(artifact.path, complevel=9, mode='a') as store:
        key = EntityKey(key)
        artifact._keys.append(key)
        store.put(f'{key.path}/index', data.index.to_frame(index=False))
        data = data.reset_index(drop=True)
        for c in data.columns:
            store.put(f'{key.path}/{c}', data[c])


def read_data_by_draw(artifact_path, key, draw):
    """Reads data from the artifact on a per-draw basis. This
    is necessary for Low Birthweight Short Gestation (LBWSG) data.

    Parameters
    ----------
    artifact
        The artifact to read from.
    key
        The entity key associated with the data to read.
    draw
        The data to retrieve.

    """
    key = key.replace(".", "/")
    with pd.HDFStore(artifact_path, mode='r') as store:
        index = store.get(f'{key}/index')
        draw = store.get(f'{key}/draw_{draw}')
    draw = draw.rename("value")
    data = pd.concat([index, draw], axis=1)
    data = data.drop(columns='location')
    data = pivot_categorical(data)
    data[project_globals.LBWSG_MISSING_CATEGORY.CAT] = project_globals.LBWSG_MISSING_CATEGORY.EXPOSURE
    return data


def load_and_write_demographic_data(artifact: Artifact, location: str):
    keys = [
        project_globals.POPULATION_STRUCTURE,
        project_globals.POPULATION_AGE_BINS,
        project_globals.POPULATION_DEMOGRAPHY,
        project_globals.ALL_CAUSE_CSMR,
    ]

    for key in keys:
        load_and_write_data(artifact, key, location)

# TODO - create appropriate functions to write data
'''
def load_and_write_diarrhea_data(artifact: Artifact, location: str):
    keys = [
        project_globals.DIARRHEA_PREVALENCE,
        project_globals.DIARRHEA_INCIDENCE_RATE,
        project_globals.DIARRHEA_REMISSION_RATE,
        project_globals.DIARRHEA_CAUSE_SPECIFIC_MORTALITY_RATE,
        project_globals.DIARRHEA_EXCESS_MORTALITY_RATE,
        project_globals.DIARRHEA_DISABILITY_WEIGHT,
        project_globals.DIARRHEA_RESTRICTIONS
    ]

    for key in keys:
        load_and_write_data(artifact, key, location)
'''

