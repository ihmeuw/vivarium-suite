import pandas as pd

from vivarium_public_health.risks.data_transformations import pivot_categorical

from {{cookiecutter.package_name}} import globals as project_globals


def sanitize_location(location: str):
    """Cleans up location formatting for writing and reading from file names.

    Parameters
    ----------
    location
        The unsanitized location name.

    Returns
    -------
        The sanitized location name (lower-case with white-space and
        special characters removed.

    """
    # FIXME: Should make this a reversible transformation.
    return location.replace(" ", "_").replace("'", "_").lower()


def read_data_by_draw(artifact_path: str, key : str, draw: int) -> pd.DataFrame:
    """Reads data from the artifact on a per-draw basis. This
    is necessary for Low Birthweight Short Gestation (LBWSG) data.

    Parameters
    ----------
    artifact_path
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
