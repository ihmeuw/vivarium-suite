from __future__ import annotations

from typing import Any, Callable, TypeVar

import pandas as pd
import pandera as pa
from vivarium_inputs.globals import DEMOGRAPHIC_COLUMNS, VIVARIUM_COLUMNS

from vivarium_testing_utils.automated_validation.constants import INPUT_DATA_INDEX_NAMES

F = TypeVar("F", bound=Callable[..., Any])


def check_io(**model_dict: type) -> Callable[[F], F]:
    """A wrapper for pa.check_io that automatically converts SchemaModels to schemas.

    Parameters
    ----------
    **model_dict
        Keyword arguments where keys are parameter names and values
        are SchemaModel classes or schema objects.

    Returns
    -------
        A decorator function that wraps the target function with pa.check_io.
    """
    # Convert any SchemaModel classes to schemas
    schema_dict = {}
    for key, value in model_dict.items():
        # Check if it's a SchemaModel class (not instance) and has to_schema method
        if hasattr(value, "to_schema") and callable(value.to_schema):
            schema_dict[key] = value.to_schema()
        else:
            # If it's already a schema or something else, use it as is
            schema_dict[key] = value

    # Return the decorator using pa.check_io with the converted schemas
    return pa.check_io(**schema_dict)


# TODO: Remove this function and references when we can support Series schemas
# more easily
def series_to_dataframe(series: pd.Series[float]) -> pd.DataFrame:
    """Convert a Series to a DataFrame with the Series values as a single column."""
    return series.to_frame(name="value")


def drop_extra_columns(raw_gbd: pd.DataFrame, data_key: str) -> pd.DataFrame:
    """Format the output of a get_draws call to have expect index and value columns."""

    value_cols = [col for col in raw_gbd.columns if "draw" in col]
    # Population structure only has "population"
    # Data should only have a "value" column when cached. Draws get converted when cached
    if data_key == "population.structure":
        # Population structure has only "population" column. Rename it
        raw_gbd = raw_gbd.rename(columns={"population": "value"})
        if "value" in raw_gbd.columns:
            value_cols = ["value"]
    if not value_cols:
        raise ValueError(
            f"No value columns found in the data. Columns found: {raw_gbd.columns.tolist()}"
        )

    gbd_cols = get_measure_index_names(data_key)
    columns_to_keep = [col for col in raw_gbd.columns if col in gbd_cols + value_cols]
    return raw_gbd[columns_to_keep]


def set_gbd_index(data: pd.DataFrame, data_key: str) -> pd.DataFrame:
    """Set the index of a GBD DataFrame based on the data key."""
    gbd_cols = get_measure_index_names(data_key)

    # CAUSE_ID is expected to be a column when Vivarium Inputs maps all of the IDs to values.
    index_cols = [
        col
        for col in gbd_cols
        if col in data.columns and col != INPUT_DATA_INDEX_NAMES.CAUSE_ID
    ]

    formatted = data.set_index(index_cols)
    return formatted


def set_validation_index(data: pd.DataFrame) -> pd.DataFrame:
    """Set the index of cached validation data to expected columns."""
    # Data should only have a "value" column when cached. Draws get converted when cached
    extra_columns = [col for col in data.columns if "draw" not in col and "value" not in col]
    # Preserve existing index order and add extra columns
    sorted_data_index = [n for n in data.index.names]
    sorted_data_index.extend([col for col in extra_columns if col not in sorted_data_index])
    # Reset index to convert existing index to columns, then set new index
    data = data.reset_index()
    data = data.set_index(sorted_data_index)

    return data


def get_measure_index_names(data_key: str, data_schema: str = "gbd") -> list[str]:
    """Get the expected index names for a given data key.

    Parameters
    ----------
    data_key
        The data key to get the index names for.
    data_schema
        The data schema type. Either "gbd" or "vivarium". Defaults to "gbd".

    Returns
    -------
        The list of expected index names for the given data key and data schema pair.
    """

    measure = data_key.split(".")[-1]
    if data_schema == "gbd":
        measure_cols = list(DEMOGRAPHIC_COLUMNS)
    else:
        measure_cols = list(VIVARIUM_COLUMNS)
    if measure in ["exposure", "relative_risk"]:
        measure_cols.append(INPUT_DATA_INDEX_NAMES.PARAMETER)
    if measure == "relative_risk":
        if data_schema == "gbd":
            measure_cols.append(INPUT_DATA_INDEX_NAMES.CAUSE_ID)
        else:
            measure_cols.append(INPUT_DATA_INDEX_NAMES.AFFECTED_ENTITY)
        measure_cols.append(INPUT_DATA_INDEX_NAMES.AFFECTED_MEASURE)

    return measure_cols


def add_comparison_metadata_levels(data: pd.DataFrame, comparison_key: str) -> pd.DataFrame:
    """Add entity and measure levels to a DataFrame index for comparison display.

    Parameters
    ----------
    data
        The DataFrame to add the levels to.
    comparison_key
        The comparison key in the format "entity.measure".

    Returns
    -------
        The DataFrame with the added index levels.
    """
    entity, measure = comparison_key.split(".")[-2:]
    idx_order = list(data.index.names)
    # Add entity and measure to index
    return (
        data.reset_index()
        .assign(
            **{INPUT_DATA_INDEX_NAMES.ENTITY: entity, INPUT_DATA_INDEX_NAMES.MEASURE: measure}
        )
        .set_index(
            [INPUT_DATA_INDEX_NAMES.ENTITY, INPUT_DATA_INDEX_NAMES.MEASURE] + idx_order
        )
    )
