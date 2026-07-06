"""
=========
Utilities
=========

This module contains utility functions for the placeholder components.

"""

from typing import Any

import numpy as np
import pandas as pd
from vivarium.config_tree import ConfigurationError
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.lookup import DEFAULT_VALUE_COLUMN

from vivarium.public_health.utilities import EntityString

#############
# Utilities #
#############


def pivot_categorical(
    data: pd.DataFrame, pivot_column: str = "parameter", reset_index: bool = True
) -> pd.DataFrame:
    """Pivots data that is long on categories to be wide."""
    index_cols = [
        column
        for column in data.columns
        if column not in [DEFAULT_VALUE_COLUMN, pivot_column]
    ]
    data = data.pivot_table(
        index=index_cols, columns=pivot_column, values=DEFAULT_VALUE_COLUMN
    )
    if reset_index:
        data = data.reset_index()
    data.columns.name = None

    return data


##########################
# Exposure data handlers #
##########################


def get_exposure_post_processor(builder, risk: str):
    """Build a post-processor that bins continuous exposure into categories.

    If category thresholds are configured, return a callable that bins
    exposure values using ``pd.cut``. Otherwise, return an empty list
    (no post-processing).

    Parameters
    ----------
    builder
        Access point for utilizing framework interfaces during setup.
    risk
        The name of the risk in the configuration.

    Returns
    -------
        A callable post-processor or an empty list.
    """
    thresholds = builder.configuration[risk]["category_thresholds"]

    if thresholds:
        thresholds = [-np.inf] + thresholds + [np.inf]
        categories = [f"cat{i}" for i in range(1, len(thresholds))]

        def post_processor(exposure, _):
            return pd.Series(
                pd.cut(exposure, thresholds, labels=categories), index=exposure.index
            ).astype(str)

    else:
        post_processor = []

    return post_processor


def load_tmred(data: dict[str, Any] | pd.DataFrame) -> dict[str, Any]:
    """Normalize theoretical-minimum-risk exposure (TMRED) data to a dict.

    Accept the TMRED data as resolved by :meth:`~vivarium.engine.component.Component.get_data`
    from either the artifact or a configuration data source, and return it
    as a plain dict of scalar fields so downstream callers can read
    ``result["distribution"]``, ``result["min"]``, and ``result["max"]``
    regardless of how the data was supplied.

    Two input shapes are supported:

    - A ``dict`` (as loaded from the artifact), which is returned as-is.
    - A single-row :class:`pandas.DataFrame` with columns ``"distribution"``
      (str), ``"min"`` (float), and ``"max"`` (float), and optionally
      ``"inverted"`` (bool) — the config data-source form. Its one row is
      converted to a dict of scalars.

    Parameters
    ----------
    data
        The TMRED data as a dict or a single-row DataFrame.

    Returns
    -------
        The TMRED data as a dict of scalar fields.

    Raises
    ------
    ValueError
        If a DataFrame is provided that does not contain exactly one row or
        is missing any of the ``distribution``, ``min``, or ``max`` columns.
    ConfigurationError
        If ``data`` is neither a dict nor a DataFrame.
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, pd.DataFrame):
        if len(data) != 1:
            raise ValueError(
                f"TMRED data must contain exactly one row, but found {len(data)} rows."
            )
        required_columns = {"distribution", "min", "max"}
        missing_columns = required_columns - set(data.columns)
        if missing_columns:
            raise ValueError(
                f"TMRED data is missing required columns: {sorted(missing_columns)}."
            )
        return data.iloc[0].to_dict()
    raise ConfigurationError(
        f"TMRED data must be a dict or a DataFrame, but got {type(data)}."
    )


def load_categories(data: dict[str, str] | pd.DataFrame) -> dict[str, str]:
    """Normalize risk-category data to a ``{category: description}`` dict.

    Accept the categories data as resolved by
    :meth:`~vivarium.engine.component.Component.get_data` from either the
    artifact or a configuration data source, and return it as a plain dict
    mapping each category name to its description string, so downstream
    callers can iterate ``result.items()`` regardless of how the data was
    supplied.

    Two input shapes are supported:

    - A ``dict`` mapping category name to description (as loaded from the
      artifact), which is returned as-is.
    - A :class:`pandas.DataFrame` with a ``"category"`` column and a
      ``"description"`` column — the config data-source form. Its rows are
      converted to a ``{category: description}`` dict.

    Parameters
    ----------
    data
        The categories data as a dict or a two-column DataFrame.

    Returns
    -------
        A mapping of category name to description string.

    Raises
    ------
    ValueError
        If a DataFrame is provided that is missing the ``category`` or
        ``description`` column, is empty, or contains duplicate ``category``
        values.
    ConfigurationError
        If ``data`` is neither a dict nor a DataFrame.
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, pd.DataFrame):
        required_columns = {"category", "description"}
        missing_columns = required_columns - set(data.columns)
        if missing_columns:
            raise ValueError(
                f"Categories data is missing required columns: {sorted(missing_columns)}."
            )
        if data.empty:
            raise ValueError("Categories data must not be empty.")
        if data["category"].duplicated().any():
            raise ValueError("Categories data must not contain duplicate categories.")
        return dict(zip(data["category"], data["description"]))
    raise ConfigurationError(
        f"Categories data must be a dict or a DataFrame, but got {type(data)}."
    )


def load_exposure_data(builder: Builder, risk: EntityString) -> pd.DataFrame:
    """Load exposure data for a risk from its configured data source.

    Parameters
    ----------
    builder
        Access point for utilizing framework interfaces during setup.
    risk
        The entity string identifying the risk.

    Returns
    -------
        The exposure data as a DataFrame.
    """
    risk_component = builder.components.get_component(risk)
    return risk_component.get_data(
        builder, builder.configuration[risk_component.name]["data_sources"]["exposure"]
    )
