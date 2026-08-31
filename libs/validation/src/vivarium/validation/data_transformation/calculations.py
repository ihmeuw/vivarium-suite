from __future__ import annotations

from typing import Any, Collection, Literal, Mapping

import numpy as np
import pandas as pd
from loguru import logger
from vivarium.engine.framework.utilities import rate_to_probability

from vivarium.validation.constants import DRAW_INDEX, DRAW_PREFIX, SEED_INDEX
from vivarium.validation.data_transformation import utils
from vivarium.validation.data_transformation.data_schema import DrawData, SingleNumericColumn
from vivarium.validation.data_transformation.types import RateConversionType


def filter_data(
    data: pd.DataFrame, filter_cols: Mapping[str, str | list[str]], drop_singles: bool = True
) -> pd.DataFrame:
    """Filter a DataFrame by the given index columns and values.

    The filter_cols argument
    should be a dictionary where the keys are column names and the values are lists of
    values to keep. If we filter to a single value, drop the column. If the dataframe is empty
    after filtering, raise an error."""
    for col, values in filter_cols.items():
        if isinstance(values, str):
            values = [values]
        if len(values) == 1:
            data = data[data.index.get_level_values(col) == values[0]]
            if drop_singles:
                data = data.droplevel([col])
        else:
            data = data[data.index.get_level_values(col).isin(values)]
    if data.empty:
        # TODO: Make sure we handle this case appropriately when we
        # want to automatically add many comparisons
        raise ValueError(
            f"DataFrame is empty after filtering by {filter_cols}. "
            f"Check that the filter values are valid."
        )

    return data


@utils.check_io(
    numerator_data=SingleNumericColumn,
    denominator_data=SingleNumericColumn,
    out=SingleNumericColumn,
)
def ratio(numerator_data: pd.DataFrame, denominator_data: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame with the ratio of two SingleNumericColumn DataFrames.

    Their indexes do not need to match, but must be interoperable.

    Parameters
    ----------
    numerator_data
        SingleNumericColumn DataFrame to use as the numerator
    denominator_data
        SingleNumericColumn DataFrame  to use as the denominator

    Returns
    -------
        SingleNumericColumn DataFrame containing the ratio values
    """
    zero_denominator = denominator_data["value"] == 0
    if zero_denominator.any():
        logger.warning(
            "Denominator has zero values. "
            "These will be put into the ratio dataframe as NaN."
        )
    denominator_data[zero_denominator] = np.nan
    return numerator_data / denominator_data


def aggregate_sum(
    data: pd.DataFrame, groupby_cols: Collection[str] | Literal["all"]
) -> pd.DataFrame:
    """Aggregate the dataframe over the specified index columns by summing."""
    if not groupby_cols:
        data = pd.DataFrame(
            {"value": [data["value"].sum()]}, index=pd.Index([0], name="index")
        )
        return data
    if groupby_cols == "all":
        groupby_cols = data.index.names
    ordered_cols = [col for col in data.index.names if col in groupby_cols]
    # Use observed=True to avoid sorting categorical levels
    # This is a hack, because we're not technically using pd.Categorical here.
    # TODO: MIC-6090  Use the right abstractions for categorical index columns.
    # You might need to keep this observed=True even after doing that.
    result = data.groupby(list(groupby_cols), sort=False, observed=True).sum()

    # Only reorder levels if the result has a MultiIndex (hierarchical index)
    return (
        result.reorder_levels(ordered_cols)
        if isinstance(result.index, pd.MultiIndex) and len(ordered_cols) > 1
        else result
    )


def stratify(
    data: pd.DataFrame, stratification_cols: Collection[str] | Literal["all"]
) -> pd.DataFrame:
    """Stratify the data by the index columns, summing over everything else. Syntactic sugar for aggregate."""
    return aggregate_sum(data, stratification_cols)


def marginalize(
    data: pd.DataFrame, marginalize_cols: Collection[str] | Literal["all"]
) -> pd.DataFrame:
    """Sum over marginalize columns, keeping the rest. Syntactic sugar for aggregate."""
    if marginalize_cols == "all":
        marginalize_cols = []
    return aggregate_sum(data, [x for x in data.index.names if x not in marginalize_cols])


def linear_combination(
    data: pd.DataFrame, coeff_a: float, col_a: str, coeff_b: float, col_b: str
) -> pd.DataFrame:
    """Return a series that is the linear combination of two columns in a DataFrame."""
    return utils.series_to_dataframe((data[col_a] * coeff_a) + (data[col_b] * coeff_b))


@utils.check_io(data=DrawData, out=SingleNumericColumn)
def clean_draw_columns(
    data: pd.DataFrame,
) -> pd.DataFrame:
    """Clean the artifact data by dropping unnecessary columns and renaming the value column."""
    # Drop unnecessary columns
    # if data has value columns of format draw_1, draw_2, etc., drop the draw_ prefix
    # and melt the data into long format
    data = data.melt(
        var_name=DRAW_INDEX,
        value_name="value",
        ignore_index=False,
    )
    data[DRAW_INDEX] = data[DRAW_INDEX].str.replace(DRAW_PREFIX, "", regex=False)
    data[DRAW_INDEX] = data[DRAW_INDEX].astype(int)
    data = data.set_index(DRAW_INDEX, append=True).sort_index()
    return data


def get_singular_indices(data: pd.DataFrame) -> dict[str, Any]:
    """Get index levels and their values that are singular (i.e. have only one unique value)."""
    singular_metadata: dict[str, Any] = {}
    for index_level in data.index.names:
        if data.index.get_level_values(index_level).nunique() == 1:
            singular_metadata[index_level] = data.index.get_level_values(index_level)[0]
    return singular_metadata


@utils.check_io(
    data=SingleNumericColumn,
    weights=SingleNumericColumn,
)
def weighted_average(
    data: pd.DataFrame,
    weights: pd.DataFrame,
    stratifications: list[str] | Literal["all"] = "all",
    scenario_columns: list[str] = [],
) -> pd.DataFrame | float:
    """Calculate a weighted average of the data using the provided weights.

    Parameters
    ----------
    data
        DataFrame with the values to average. Must have a 'value' column.
    weights
        DataFrame with the weights to apply to the values in data. Must have a 'value' column.
    stratifications
        List of index level names to use for stratification/grouping.
    scenario_columns
        List of columns to retain. If scenario columns are present in data but not weights, these
        columns will be cast across weights and added as index levels.

    Raises
    ------
    ValueError
        If data index levels contain columns not present in weights or scenario columns.

    Returns
    -------
        Pandas DataFrame with the weighted average values for each stratification group.

    Examples
    --------

    >>> fish_data = pd.DataFrame(
    ...     {
    ...         "weights": [20, 100, 2, 50],
    ...         "value": [2, 3, 5, 7],
    ...     },
    ...     index=pd.MultiIndex.from_tuples([
    ...         ("Male", "Red"),
    ...         ("Male", "Blue"),
    ...         ("Female", "Red"),
    ...         ("Female", "Blue"),
    ...     ], names=["sex", "color"])
    ... )
    >>> data = pd.DataFrame({"value": fish_data["value"]}, index=fish_data.index)
    >>> weights = pd.DataFrame({"value": fish_data["weights"]}, index=fish_data.index)

    # Weighted average by sex:
    >>> weighted_average(data, weights, ["sex"])
    # Returns:
    #         value
    # sex
    # Male     2.83  # (20*2 + 100*3)/(20+100) = 340/120 ≈ 2.83
    # Female   6.92  # (2*5 + 50*7)/(2+50) = 360/52 ≈ 6.92

    # Weighted average by color:
    >>> weighted_average(data, weights, ["color"])
    # Returns:
    #        value
    # color
    # Red     2.27  # (20*2 + 2*5)/(20+2) = 50/22 ≈ 2.27
    # Blue    4.33  # (100*3 + 50*7)/(100+50) = 650/150 ≈ 4.33

    # Overall weighted average (no stratification):
    >>> weighted_average(data, weights, [])
    # Returns: 3.55  # (20*2 + 100*3 + 2*5 + 50*7)/(20+100+2+50) = 700/172 ≈ 4.07

    """

    # Check if weights has extra index levels compared to data
    data_index_names = set(data.index.names)
    weights_index_names = set(weights.index.names)
    scenario_cols = set(scenario_columns + [DRAW_INDEX, SEED_INDEX])

    # If weights has extra index levels, aggregate by summing
    extra_weight_levels = weights_index_names - data_index_names - scenario_cols
    if extra_weight_levels:
        # Group by the levels that match data's index and sum over the extra levels
        weights = aggregate_sum(
            weights, [col for col in weights.index.names if col not in extra_weight_levels]
        )

    # Check if data has extra columns outside of scenario columns
    if data_index_names - weights_index_names - scenario_cols:
        raise ValueError(
            f"Data index levels {data_index_names - weights_index_names - scenario_cols} "
            f"are not present in weights index levels {weights_index_names} or scenario columns {scenario_cols}"
        )
    # Cast scenario columns across weights if they do not exist in weights
    cols_to_cast = [
        col
        for col in scenario_cols
        if col in data_index_names and col not in weights_index_names
    ]
    # Cast cols_to_cast on weights
    if cols_to_cast:
        # Get unique values for each column to cast from data
        cast_values = {col: data.index.get_level_values(col).unique() for col in cols_to_cast}
        # Cross join weights with the new index levels
        weights = weights.reindex(
            pd.MultiIndex.from_product(
                [
                    weights.index.get_level_values(level).unique()
                    for level in weights.index.names
                ]
                + list(cast_values.values()),
                names=list(weights.index.names) + list(cast_values.keys()),
            )
        )

    # Sort both dataframes by their index to ensure they're in the same order
    if len(weights.index.names) > 1:
        weights = weights.reorder_levels(list(data.index.names))
    data = data.sort_index()
    weights = weights.sort_index()

    # Indexes should be equal at this point
    if not data.index.equals(weights.index):
        raise ValueError(
            "Data and weights must have the same index levels. "
            f"Data index: {data.index.names}, Weights index: {weights.index.names}"
        )

    if not stratifications:
        # Return a single float value instead of a one row pandas series
        return float(((data.mul(weights).sum()) / weights.sum()).item())

    numerator = aggregate_sum(data.mul(weights), stratifications)
    denominator = aggregate_sum(weights, stratifications)
    return ratio(numerator, denominator)


_PERSON_STEP_ROUNDING_TOLERANCE = 1e-3
"""Person-time accrues as ``people * step_size`` each step, so dividing it back out is
exact up to accumulated floating point error -- orders of magnitude below the ~0.5 drift
a step size that does not match the simulation's would produce."""


@utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
def person_time_to_person_steps(data: pd.DataFrame, step_size: float | None) -> pd.DataFrame:
    """Convert person-time in years to a whole number of person-steps.

    Parameters
    ----------
    data
        Person-time in years.
    step_size
        The simulation's time step, as a fraction of a year.

    Returns
    -------
        The equivalent count of person-steps.

    Raises
    ------
    ValueError
        If step_size is None, since person-time has no meaning as a count of
        opportunities until it is divided by one.
    """
    if step_size is None:
        raise ValueError(
            "Cannot convert person-time to a count of opportunities without a step "
            "size. Set 'time.step_size' in the model specification."
        )

    person_steps = data / step_size
    rounded = person_steps.round()

    drift = float((person_steps - rounded).abs().max().max())
    if drift > _PERSON_STEP_ROUNDING_TOLERANCE:
        logger.warning(
            f"Person-time is not a whole number of person-steps at a step size of "
            f"{step_size} years; the largest value is off by {drift:g} steps. Either "
            "the step size does not match the one the simulation ran with, which would "
            "bias every target this comparison tests, or the simulation used a variable "
            "clock, in which case person-steps are not a meaningful count."
        )

    return rounded


@utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
def rate_to_step_probability(
    data: pd.DataFrame,
    step_size: float | None,
    rate_conversion_type: RateConversionType = "linear",
) -> pd.DataFrame:
    """Convert an annual rate to the probability of an event in one time step.

    Defers to the engine so that validation converts rates exactly as the simulation
    did, including the caller's choice of conversion. The engine helper returns a bare
    array and its exponential branch caps its input in place, so it is handed a copy
    and its result is put back on the original index.

    A rate too high to express as a per-step probability is refused rather than
    tested. The engine clamps such a rate to 1, which is worse than useless here: it
    leaves the no-bug distribution with mass only at ``n``, so the Bayes factor is
    infinite and the group reports a decisive failure rather than the error it
    deserves.

    Parameters
    ----------
    data
        An annual rate.
    step_size
        The simulation's time step, as a fraction of a year.
    rate_conversion_type
        The conversion the simulation was configured to use.

    Returns
    -------
        The per-time-step probability.

    Raises
    ------
    ValueError
        If step_size is None, since a rate has no per-step probability without one, or
        if a rate is too high to express as a per-step probability.
    """
    if step_size is None:
        raise ValueError(
            "Cannot convert an annual rate to a per-step probability without a step "
            "size. Set 'time.step_size' in the model specification."
        )

    column = data.columns[0]
    probability = rate_to_probability(
        data[column].copy(),
        time_scaling_factor=step_size,
        rate_conversion_type=rate_conversion_type,
    )
    converted = pd.DataFrame({column: np.asarray(probability)}, index=data.index)

    # A probability of exactly 1 means the engine clamped a rate it could not express
    # at this step size; no conversion reaches 1 otherwise.
    impossible = converted[column] >= 1.0
    if impossible.any():
        raise ValueError(
            f"{int(impossible.sum())} of {len(converted)} reference rates are too high "
            f"to express as a probability over a step of {step_size} years, the largest "
            f"being {data[column].max():g} per year. Those groups cannot be tested as a "
            "proportion of per-step opportunities."
        )

    return converted
