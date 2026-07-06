"""Tests for causal factor data-source normalization utilities.

These exercise ``load_tmred`` and ``load_categories``, which normalize the
``get_data`` result (a dict from the artifact or a DataFrame from a config
data source) into the dict shape their consumers expect.
"""

import pandas as pd
import pytest
from vivarium.config_tree import ConfigurationError

from vivarium.public_health.causal_factor.utilities import load_categories, load_tmred


def test_load_tmred_from_dataframe() -> None:
    """A single-row TMRED DataFrame is converted to a dict of scalar fields."""
    data = pd.DataFrame(
        {
            "distribution": ["uniform"],
            "min": [50.0],
            "max": [80.0],
            "inverted": [False],
        }
    )

    result = load_tmred(data)

    assert isinstance(result, dict)
    assert result["distribution"] == "uniform"
    assert result["min"] == 50.0
    assert result["max"] == 80.0
    assert not result["inverted"]


def test_load_tmred_from_dataframe_preserves_inverted() -> None:
    """The ``inverted`` flag round-trips from the TMRED DataFrame into the dict."""
    data = pd.DataFrame(
        {
            "distribution": ["uniform"],
            "min": [50.0],
            "max": [80.0],
            "inverted": [True],
        }
    )

    result = load_tmred(data)

    assert result["inverted"]


def test_load_tmred_passes_dict_through() -> None:
    """A TMRED dict (as loaded from the artifact) is returned unchanged."""
    data = {"distribution": "uniform", "min": 1.0, "max": 2.0, "inverted": False}

    assert load_tmred(data) == data


def test_load_tmred_rejects_multirow_dataframe() -> None:
    """A TMRED DataFrame with more than one row raises ValueError."""
    data = pd.DataFrame(
        {
            "distribution": ["uniform", "uniform"],
            "min": [1.0, 2.0],
            "max": [2.0, 3.0],
        }
    )

    with pytest.raises(ValueError):
        load_tmred(data)


def test_load_tmred_rejects_empty_dataframe() -> None:
    """A TMRED DataFrame with no rows raises ValueError."""
    data = pd.DataFrame({"distribution": [], "min": [], "max": []})

    with pytest.raises(ValueError):
        load_tmred(data)


def test_load_tmred_rejects_missing_column() -> None:
    """A TMRED DataFrame missing a required column raises ValueError."""
    data = pd.DataFrame({"distribution": ["uniform"], "min": [1.0]})

    with pytest.raises(ValueError):
        load_tmred(data)


def test_load_categories_from_dataframe() -> None:
    """A two-column categories DataFrame is converted to a {category: description} dict."""
    data = pd.DataFrame(
        {
            "category": ["cat1", "cat2", "cat3"],
            "description": ["severe", "moderate", "mild"],
        }
    )

    result = load_categories(data)

    assert result == {"cat1": "severe", "cat2": "moderate", "cat3": "mild"}


def test_load_categories_passes_dict_through() -> None:
    """A categories dict (as loaded from the artifact) is returned unchanged."""
    data = {"cat1": "severe", "cat2": "moderate", "cat3": "mild"}

    assert load_categories(data) == data


def test_load_categories_rejects_missing_column() -> None:
    """A categories DataFrame missing a required column raises ValueError."""
    data = pd.DataFrame({"category": ["cat1", "cat2"]})

    with pytest.raises(ValueError):
        load_categories(data)


def test_load_categories_rejects_empty_dataframe() -> None:
    """A categories DataFrame with no rows raises ValueError."""
    data = pd.DataFrame({"category": [], "description": []})

    with pytest.raises(ValueError):
        load_categories(data)


def test_load_categories_rejects_duplicate_category() -> None:
    """A categories DataFrame with duplicate category values raises ValueError."""
    data = pd.DataFrame(
        {
            "category": ["cat1", "cat1"],
            "description": ["severe", "moderate"],
        }
    )

    with pytest.raises(ValueError):
        load_categories(data)


def test_load_tmred_rejects_unsupported_type() -> None:
    """A TMRED value that is neither a dict nor a DataFrame raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_tmred([1, 2, 3])


def test_load_categories_rejects_unsupported_type() -> None:
    """A categories value that is neither a dict nor a DataFrame raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_categories([1, 2, 3])
