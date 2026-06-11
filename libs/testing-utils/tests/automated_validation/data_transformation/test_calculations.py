from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vivarium_testing_utils.automated_validation.constants import DRAW_INDEX, SEED_INDEX
from vivarium_testing_utils.automated_validation.data_transformation.calculations import (
    aggregate_sum,
    filter_data,
    linear_combination,
    ratio,
    weighted_average,
)


@pytest.fixture
def intermediate_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "a": [1, 2, 3, 4],
            "b": [4, 5, 6, 7],
            "c": [1, 1, 0, 1],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("x", 0),
                ("x", 1),
                ("y", 0),
                ("y", 1),
            ],
            names=["group", "time"],
        ),
    )


@pytest.fixture
def filter_test_data() -> pd.DataFrame:
    """Create a DataFrame with multiple index levels for testing filter_data."""
    return pd.DataFrame(
        {
            "value": [10, 20, 30, 40, 50, 60, 70, 80],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("location_1", "sex_1", "age_1"),
                ("location_1", "sex_1", "age_2"),
                ("location_1", "sex_2", "age_1"),
                ("location_1", "sex_2", "age_2"),
                ("location_2", "sex_1", "age_1"),
                ("location_2", "sex_1", "age_2"),
                ("location_2", "sex_2", "age_1"),
                ("location_2", "sex_2", "age_2"),
            ],
            names=["location", "sex", "age"],
        ),
    )


@pytest.fixture
def weights() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "value": [1, 2, 3, 4],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("location_1", "sex_1", "age_1"),
                ("location_1", "sex_1", "age_2"),
                ("location_2", "sex_1", "age_1"),
                ("location_2", "sex_1", "age_2"),
            ],
            names=["location", "sex", "age"],
        ),
    )


@pytest.fixture
def fish_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "weights": [20, 100, 2, 50],
            "value": [2, 3, 5, 7],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("Male", "Red"),
                ("Male", "Blue"),
                ("Female", "Red"),
                ("Female", "Blue"),
            ],
            names=["sex", "color"],
        ),
    )


@pytest.mark.parametrize(
    "filter_cols,drop_singles,expected_index_names,expected_values",
    [
        # Test filtering to single value with drop_singles=True (default)
        (
            {"location": "location_1"},
            True,
            ["sex", "age"],
            [10, 20, 30, 40],
        ),
        # Test filtering to single value with drop_singles=False
        (
            {"location": "location_1"},
            False,
            ["location", "sex", "age"],
            [10, 20, 30, 40],
        ),
        # Test filtering to multiple values (drop_singles should not affect this)
        (
            {"sex": ["sex_1", "sex_2"], "age": "age_1"},
            True,
            ["location", "sex"],
            [10, 30, 50, 70],
        ),
        (
            {"sex": ["sex_1", "sex_2"], "age": "age_1"},
            False,
            ["location", "sex", "age"],
            [10, 30, 50, 70],
        ),
        # Test filtering with multiple single values
        (
            {"location": "location_1", "sex": "sex_1"},
            True,
            ["age"],
            [10, 20],
        ),
        (
            {"location": "location_1", "sex": "sex_1"},
            False,
            ["location", "sex", "age"],
            [10, 20],
        ),
    ],
)
def test_filter_data(
    filter_test_data: pd.DataFrame,
    filter_cols: dict[str, str],
    drop_singles: bool,
    expected_index_names: list[str],
    expected_values: list[int | float],
) -> None:
    """Test filtering DataFrame with different drop_singles settings."""
    result = filter_data(filter_test_data, filter_cols, drop_singles=drop_singles)
    assert list(result.index.names) == expected_index_names
    assert list(result["value"]) == expected_values


def test_filter_data_empty_result(filter_test_data: pd.DataFrame) -> None:
    """Test that filter_data raises ValueError when result is empty."""
    with pytest.raises(ValueError, match="DataFrame is empty after filtering"):
        filter_data(filter_test_data, {"location": "nonexistent_location"})


def test_ratio(intermediate_data: pd.DataFrame) -> None:
    """Test taking ratio of two DataFrames with 'value' columns"""
    # Create separate numerator and denominator DataFrames
    numerator_a = pd.DataFrame(
        {"value": intermediate_data["a"]}, index=intermediate_data.index
    )
    denominator_b = pd.DataFrame(
        {"value": intermediate_data["b"]}, index=intermediate_data.index
    )
    denominator_c = pd.DataFrame(
        {"value": intermediate_data["c"]}, index=intermediate_data.index
    )

    # Test normal ratio calculation
    assert ratio(numerator_a, denominator_b).equals(
        pd.DataFrame({"value": [1 / 4, 2 / 5, 3 / 6, 4 / 7]}, index=intermediate_data.index)
    )

    # Test ratio with zero denominator
    pd.testing.assert_frame_equal(
        ratio(numerator_a, denominator_c),
        pd.DataFrame({"value": [1.0, 2.0, np.nan, 4.0]}, index=intermediate_data.index),
    )


def test_aggregate_sum(intermediate_data: pd.DataFrame) -> None:
    """Test aggregating over different combinations of value columns."""
    assert aggregate_sum(intermediate_data, ["group"]).equals(
        pd.DataFrame(
            {
                "a": [3, 7],
                "b": [9, 13],
                "c": [2, 1],
            },
            index=pd.Index(["x", "y"], name="group"),
        )
    )
    assert aggregate_sum(intermediate_data, ["time"]).equals(
        pd.DataFrame(
            {
                "a": [4, 6],
                "b": [10, 12],
                "c": [1, 2],
            },
            index=pd.Index([0, 1], name="time"),
        )
    )
    assert aggregate_sum(intermediate_data, ["group", "time"]).equals(intermediate_data)
    # test non-existent index column
    with pytest.raises(KeyError):
        aggregate_sum(intermediate_data, ["foo"])


def test_linear_combination(intermediate_data: pd.DataFrame) -> None:
    """Test linear combination of two columns in a multi-indexed DataFrame"""
    assert linear_combination(intermediate_data, 1, "a", 1, "b").equals(
        pd.DataFrame({"value": [5, 7, 9, 11]}, index=intermediate_data.index)
    )
    assert linear_combination(intermediate_data, 2, "a", -1, "b").equals(
        pd.DataFrame({"value": [-2, -1, 0, 1]}, index=intermediate_data.index)
    )
    # test non-existent column
    with pytest.raises(KeyError):
        linear_combination(intermediate_data, 1, "a", 1, "foo")


def test_aggregate_sum_preserves_string_order() -> None:
    """Test that aggregate_sum preserves the order of string index levels."""
    # Create a dataframe with string index that has a non-alphabetical order
    df = pd.DataFrame(
        {"value": [1, 2, 3, 4]},
        index=pd.Index(["c", "a", "d", "b"], name="category"),
    )

    # The result should maintain the original order
    result = aggregate_sum(df, ["category"])
    expected_order = pd.Index(["c", "a", "d", "b"], name="category")
    assert list(result.index) == list(expected_order)


@pytest.mark.parametrize(
    "stratifications,expected_values,expected_index",
    [
        # Test aggregating by sex
        (
            ["sex"],
            [
                6.92,
                2.83,
            ],  # Male: (20*2 + 100*3)/(20+100) ≈ 2.83, Female: (2*5 + 50*7)/(2+50) ≈ 6.92
            pd.Index(["Female", "Male"], name="sex"),
        ),
        # Test aggregating by color
        (
            ["color"],
            [
                4.33,
                2.27,
            ],  # Red: (20*2 + 2*5)/(20+2) ≈ 2.27, Blue: (100*3 + 50*7)/(100+50) ≈ 4.33
            pd.Index(["Blue", "Red"], name="color"),
        ),
        # Test no aggregation - keeping all index levels
        (
            ["sex", "color"],
            [7.0, 5.0, 3.0, 2.0],  # Original values
            pd.MultiIndex.from_tuples(
                [("Female", "Blue"), ("Female", "Red"), ("Male", "Blue"), ("Male", "Red")],
                names=["sex", "color"],
            ),
        ),
        # Test empty stratification list - should aggregate over all levels
        (
            [],
            4.07,  # Overall weighted average: (20*2 + 100*3 + 2*5 + 50*7)/(20+100+2+50) = (40+300+10+350)/172 ≈ 4.07
            pd.Index([0]),  # Default integer index when all levels are aggregated
        ),
    ],
)
def test_weighted_average(
    fish_data: pd.DataFrame,
    stratifications: list[str],
    expected_values: list[float] | float,
    expected_index: pd.Index[str] | pd.MultiIndex | pd.Index[int],
) -> None:
    """Test weighted average with different stratification scenarios."""
    # Split fish_data into separate data and weights dataframes
    data = pd.DataFrame({"value": fish_data["value"]}, index=fish_data.index)
    weights = pd.DataFrame({"value": fish_data["weights"]}, index=fish_data.index)

    # Test weighted average calculation
    result = weighted_average(data, weights, stratifications)
    if isinstance(result, float):
        assert np.isclose(result, expected_values, rtol=1e-2)
    else:
        expected = pd.DataFrame(
            {"value": expected_values},
            index=expected_index,
        )
        pd.testing.assert_frame_equal(result, expected, rtol=1e-2)


def test_weighted_average_extra_data_index_fails(fish_data: pd.DataFrame) -> None:
    """Test weighted average when weights DataFrame has fewer index levels than data DataFrame."""
    # Split fish_data into separate data and weights dataframes
    data = pd.DataFrame({"value": fish_data["value"]}, index=fish_data.index)
    weights = pd.DataFrame({"value": fish_data["weights"]}, index=fish_data.index)

    # Remove the color index level from weights - aggregate weights by sex
    weights.index = weights.index.droplevel("color")
    # This should give us:
    # sex
    # Male      120  (20 + 100)
    # Female     52  (2 + 50)

    # Test that weighted_average works with subset index
    # When grouping by sex, it should broadcast the weights appropriately
    with pytest.raises(ValueError, match="are not present in weights index levels"):
        weighted_average(data, weights, ["sex"])


def test_weighted_average_extra_weights_index(fish_data: pd.DataFrame) -> None:
    """Test weighted average when weights DataFrame has extra index levels compared to data DataFrame."""
    # Split fish_data into separate data and weights dataframes
    data = pd.DataFrame({"value": fish_data["value"]}, index=fish_data.index)
    weights = pd.DataFrame({"value": fish_data["weights"]}, index=fish_data.index)

    # Remove index layer from data so weights has an extra layer
    data = data.groupby("sex", sort=False, observed=True).sum()
    weighted_avg = weighted_average(data=data, weights=weights, stratifications=[])
    assert weighted_avg == ((5 * (20 + 100)) + (12 * (2 + 50))) / (20 + 100 + 2 + 50)


def test_aggregate_sum_all(fish_data: pd.DataFrame) -> None:
    """Test aggregate_sum when aggregating over all index levels."""
    data = pd.DataFrame({"value": fish_data["value"]}, index=fish_data.index)
    aggregate = aggregate_sum(data, "all")
    assert aggregate.equals(data)


def test_weighted_average_cast_index() -> None:
    data = pd.DataFrame(
        {
            "location": ["Shadow"] * 8,
            "sex": ["Male", "Male", "Male", "Male", "Female", "Female", "Female", "Female"],
            "age": ["0-4", "0-4", "5-9", "5-9", "0-4", "0-4", "5-9", "5-9"],
            DRAW_INDEX: [0, 1, 0, 1, 0, 1, 0, 1],
            SEED_INDEX: [42] * 8,
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        }
    )
    data = data.set_index(["location", "sex", "age", DRAW_INDEX, SEED_INDEX])
    weights = pd.DataFrame(
        {
            "location": ["Shadow"] * 4,
            "sex": ["Male", "Male", "Female", "Female"],
            "age": ["0-4", "5-9", "0-4", "5-9"],
            "value": [10, 30, 50, 70],
        }
    )
    weights = weights.set_index(["location", "sex", "age"])
    weighted_avg = weighted_average(
        data, weights, "all", scenario_columns=[DRAW_INDEX, SEED_INDEX]
    )
    assert isinstance(weighted_avg, pd.DataFrame)
    assert set(weighted_avg.index.names) == set(data.index.names)


def test_weighted_average_extra_weights_index_and_cast() -> None:
    data = pd.DataFrame(
        {
            "location": ["Hera"] * 8,
            "sex": ["Male", "Male", "Male", "Male", "Female", "Female", "Female", "Female"],
            "age": ["0-4", "0-4", "5-9", "5-9", "0-4", "0-4", "5-9", "5-9"],
            DRAW_INDEX: [0, 1, 0, 1, 0, 1, 0, 1],
            SEED_INDEX: [42] * 8,
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        }
    )
    data = data.set_index(["location", "sex", "age", DRAW_INDEX, SEED_INDEX])
    weights = pd.DataFrame(
        {
            "location": ["Hera"] * 4,
            "sex": ["Male", "Male", "Female", "Female"],
            "age": ["0-4", "5-9", "0-4", "5-9"],
            "color": ["Red", "Blue", "Red", "Blue"],
            "value": [10, 30, 50, 70],
        }
    )
    weights = weights.set_index(["location", "sex", "age", "color"])
    weighted_avg = weighted_average(
        data, weights, "all", scenario_columns=[DRAW_INDEX, SEED_INDEX]
    )
    assert isinstance(weighted_avg, pd.DataFrame)
    assert set(weighted_avg.index.names) == set(data.index.names)


def test_weighted_average_error_on_extra_data_indices() -> None:
    data = pd.DataFrame(
        {
            "location": ["Regina"] * 4,
            "sex": ["Male", "Male", "Female", "Female"],
            "age": ["0-4", "5-9", "0-4", "5-9"],
            "gravity": ["Low", "High", "Low", "High"],
            "value": [1.0, 2.0, 3.0, 4.0],
        }
    )
    data = data.set_index(["location", "sex", "age", "gravity"])
    weights = pd.DataFrame(
        {
            "location": ["Regina"] * 4,
            "sex": ["Male", "Male", "Female", "Female"],
            "age": ["0-4", "5-9", "0-4", "5-9"],
            "color": ["Red", "Blue", "Red", "Blue"],
            "value": [10, 30, 50, 70],
        }
    )
    weights = weights.set_index(["location", "sex", "age", "color"])
    with pytest.raises(ValueError, match="are not present in weights index levels"):
        weighted_average(data, weights)
