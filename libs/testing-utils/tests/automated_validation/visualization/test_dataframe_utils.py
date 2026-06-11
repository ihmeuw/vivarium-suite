from typing import Any

import pytest
from pytest_check import check

from vivarium_testing_utils.automated_validation.constants import DataSource
from vivarium_testing_utils.automated_validation.visualization.dataframe_utils import (
    format_draws_sample,
    format_metadata,
)

MEASURE_KEY = "test_measure"


@pytest.fixture
def test_info() -> dict[str, Any]:
    """Info dictionary with draws."""
    return {
        "source": "sim",
        "index_columns": ["year", "sex", "age", "input_draw"],
        "size": "100 rows × 5 columns",
        "num_draws": "10",
        "input_draws": "[0, 1, 2, 3]",
    }


@pytest.fixture
def reference_info() -> dict[str, Any]:
    """Info dictionary without draws."""
    return {
        "source": "artifact",
        "index_columns": ["year", "sex", "age"],
        "size": "50 rows × 3 columns",
    }


def test_format_metadata_basic(
    test_info: dict[str, Any], reference_info: dict[str, Any]
) -> None:
    """Test we can format metadata into a pandas DataFrame."""
    df = format_metadata(MEASURE_KEY, test_info, reference_info)

    expected_metadata = [
        ("Measure Key", "test_measure", "test_measure"),
        ("Source", "sim", "artifact"),
        ("Shared Indices", "age, sex, year", "age, sex, year"),
        ("Source Specific Indices", "input_draw", ""),
        ("Size", "100 rows × 5 columns", "50 rows × 3 columns"),
        ("Num Draws", "10", ""),
        ("Input Draws", "[0, 1, 2, 3]", ""),
    ]

    assert df.index.name == "Property"
    assert df.shape == (7, 2)
    assert df.columns.tolist() == ["Test Data", "Reference Data"]

    with check:
        for property_name, test_value, reference_value in expected_metadata:
            assert df.loc[property_name]["Test Data"] == test_value
            assert df.loc[property_name]["Reference Data"] == reference_value


def test_format_metadata_missing_fields() -> None:
    """Test we can format metadata into a pandas DataFrame wtih missing fields."""
    test_info: dict[str, Any] = {"source": "sim"}
    reference_info: dict[str, Any] = {"source": "artifact"}
    test_info["index_columns"] = []
    reference_info["index_columns"] = []

    df = format_metadata(MEASURE_KEY, test_info, reference_info)
    for i in range(3, 6):
        assert df["Test Data"].iloc[i] == ""
        assert df["Reference Data"].iloc[i] == ""


@pytest.mark.parametrize(
    "draws",
    [
        [0, 1, 2, 3],
        [0, 1, 2],
        [0],
    ],
)
@pytest.mark.parametrize(
    "data_source",
    [
        DataSource.SIM,
        DataSource.ARTIFACT,
        DataSource.GBD,
    ],
)
def test_format_draws(draws: list[int], data_source: DataSource) -> None:
    """Test formatting a small number of draws."""
    # Test with a small list of draws (less than 2 * max_display)
    if data_source == DataSource.SIM:
        assert format_draws_sample(draws, data_source) == ", ".join(
            str(draw) for draw in draws
        )
    else:
        expected = f"range({draws[0]}-{draws[-1]})"
        assert format_draws_sample(draws, data_source) == expected


@pytest.mark.parametrize(
    "data_source",
    [
        DataSource.SIM,
        DataSource.ARTIFACT,
        DataSource.GBD,
    ],
)
def test_format_draws_sample_large(data_source: DataSource) -> None:
    """Test formatting a large number of draws."""
    # Test with a large list of draws (more than 2 * max_display)
    draws = list(range(18))
    if data_source == DataSource.SIM:
        assert format_draws_sample(draws, data_source) == ", ".join(
            str(draw) for draw in draws
        )
    else:
        assert format_draws_sample(draws, data_source) == "range(0-17)"
