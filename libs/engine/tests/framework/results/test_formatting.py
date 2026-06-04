"""Acceptance criteria for ``cast_non_value_columns_to_categorical`` (MIC-6499).

Each test stub names one behavior the implementation must satisfy. The bodies
are filled in by the test author against the source stub's signature and
docstring; the implementer treats these names and docstrings as the read-only
contract and never sees the assertions.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from vivarium.engine.framework.results.formatting import cast_non_value_columns_to_categorical


def test_non_value_columns_become_categorical() -> None:
    """Every column except ``value`` is cast to a pandas Categorical dtype."""
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "ravenclaw"],
            "familiar": ["cat", "owl", "gecko"],
            "value": [1.0, 2.0, 3.0],
        }
    )
    result = cast_non_value_columns_to_categorical(df)
    assert isinstance(result["house"].dtype, pd.CategoricalDtype)
    assert isinstance(result["familiar"].dtype, pd.CategoricalDtype)


def test_value_column_is_left_unchanged() -> None:
    """The ``value`` column keeps its original (float) dtype and is never categorical."""
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin"],
            "value": [1.5, 2.5],
        }
    )
    result = cast_non_value_columns_to_categorical(df)
    assert not isinstance(result["value"].dtype, pd.CategoricalDtype)
    assert result["value"].dtype == float
    pd.testing.assert_series_equal(result["value"], df["value"])


def test_column_in_orderings_is_ordered_with_that_category_order() -> None:
    """A non-value column listed in ``orderings`` becomes an *ordered* Categorical whose categories are exactly the supplied order."""
    houses = ["hufflepuff", "ravenclaw", "slytherin", "gryffindor"]
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "hufflepuff"],
            "value": [1.0, 2.0, 3.0],
        }
    )
    result = cast_non_value_columns_to_categorical(df, orderings={"house": houses})
    assert isinstance(result["house"].dtype, pd.CategoricalDtype)
    assert result["house"].cat.ordered
    assert list(result["house"].cat.categories) == houses


def test_column_absent_from_orderings_is_unordered_categorical() -> None:
    """A non-value column not present in ``orderings`` becomes an unordered Categorical."""
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin"],
            "familiar": ["cat", "owl"],
            "value": [1.0, 2.0],
        }
    )
    # "familiar" is absent from orderings; "house" is present
    result = cast_non_value_columns_to_categorical(
        df, orderings={"house": ["hufflepuff", "ravenclaw", "slytherin", "gryffindor"]}
    )
    assert isinstance(result["familiar"].dtype, pd.CategoricalDtype)
    assert not result["familiar"].cat.ordered


def test_numeric_and_datetime_columns_are_left_unchanged() -> None:
    """A non-value column that is numeric, bool, or datetime/timedelta (and absent from ``orderings``) is left unchanged, not cast to Categorical."""
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "ravenclaw"],
            "power_level": [20, 40, 60],
            "is_alive": [True, False, True],
            "event_time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "elapsed": pd.to_timedelta([1, 2, 3], unit="D"),
            "value": [1.0, 2.0, 3.0],
        }
    )
    original_power_level_dtype = df["power_level"].dtype
    original_is_alive_dtype = df["is_alive"].dtype
    original_event_time_dtype = df["event_time"].dtype
    original_elapsed_dtype = df["elapsed"].dtype

    # No orderings: numeric/bool/datetime/timedelta columns must be left alone.
    result = cast_non_value_columns_to_categorical(df)

    # Numeric, bool, datetime, and timedelta non-value columns are unchanged.
    assert not isinstance(result["power_level"].dtype, pd.CategoricalDtype)
    assert result["power_level"].dtype == original_power_level_dtype
    assert not isinstance(result["is_alive"].dtype, pd.CategoricalDtype)
    assert result["is_alive"].dtype == original_is_alive_dtype
    assert not isinstance(result["event_time"].dtype, pd.CategoricalDtype)
    assert result["event_time"].dtype == original_event_time_dtype
    assert not isinstance(result["elapsed"].dtype, pd.CategoricalDtype)
    assert result["elapsed"].dtype == original_elapsed_dtype

    # An object column in the same frame still becomes (unordered) Categorical.
    assert isinstance(result["house"].dtype, pd.CategoricalDtype)
    assert not result["house"].cat.ordered

    # value column is untouched.
    assert not isinstance(result["value"].dtype, pd.CategoricalDtype)


def test_value_outside_ordering_becomes_nan() -> None:
    """A value present in the data but absent from the supplied ``orderings`` list becomes NaN after casting; the column is the ordered Categorical with exactly the supplied categories."""
    houses = ["hufflepuff", "ravenclaw", "slytherin"]  # note: "gryffindor" excluded
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "ravenclaw"],
            "value": [1.0, 2.0, 3.0],
        }
    )
    result = cast_non_value_columns_to_categorical(df, orderings={"house": houses})

    # The column is the ordered Categorical with exactly the supplied categories.
    assert isinstance(result["house"].dtype, pd.CategoricalDtype)
    assert result["house"].cat.ordered
    assert list(result["house"].cat.categories) == houses

    # The "gryffindor" value, absent from the ordering, becomes NaN.
    assert pd.isna(result["house"].iloc[0])
    # The in-ordering values are preserved.
    assert result["house"].iloc[1] == "slytherin"
    assert result["house"].iloc[2] == "ravenclaw"


def test_already_categorical_column_absent_from_orderings_stays_unordered_categorical() -> None:
    """A non-value column that is *already* Categorical but absent from ``orderings`` comes out as an unordered Categorical."""
    pre_cast = pd.Categorical(
        ["gryffindor", "slytherin", "ravenclaw"],
        categories=["gryffindor", "ravenclaw", "slytherin"],
    )
    df = pd.DataFrame(
        {
            "house": pre_cast,
            "value": [1.0, 2.0, 3.0],
        }
    )
    # "house" is absent from orderings entirely.
    result = cast_non_value_columns_to_categorical(df, orderings={"familiar": ["cat", "owl"]})

    assert isinstance(result["house"].dtype, pd.CategoricalDtype)
    assert not result["house"].cat.ordered


def test_already_categorical_column_is_recast_to_the_ordering() -> None:
    """A non-value column that is *already* Categorical (e.g. pre-cast by the formatter) is still re-cast to the ordered categories from ``orderings``."""
    houses_ordered = ["hufflepuff", "ravenclaw", "slytherin", "gryffindor"]
    # Pre-cast with a different (unordered) category set
    pre_cast = pd.Categorical(
        ["gryffindor", "slytherin"], categories=["gryffindor", "slytherin"]
    )
    df = pd.DataFrame(
        {
            "house": pre_cast,
            "value": [1.0, 2.0],
        }
    )
    assert not df["house"].cat.ordered  # sanity check: starts unordered

    result = cast_non_value_columns_to_categorical(df, orderings={"house": houses_ordered})
    assert result["house"].cat.ordered
    assert list(result["house"].cat.categories) == houses_ordered


def test_categorical_dtype_and_order_survive_parquet_roundtrip(tmp_path: Path) -> None:
    """After ``to_parquet`` then ``read_parquet``, non-value columns load back as Categoricals and ordered ones retain their order (the MIC-6499 'does this matter in parquet' check)."""
    houses = ["hufflepuff", "ravenclaw", "slytherin", "gryffindor"]
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "hufflepuff", "ravenclaw"],
            "familiar": ["cat", "owl", "gecko", "banana_slug"],
            "value": [1.0, 2.0, 3.0, 4.0],
        }
    )
    result = cast_non_value_columns_to_categorical(df, orderings={"house": houses})

    parquet_path = tmp_path / "results.parquet"
    result.to_parquet(parquet_path)
    reloaded = pd.read_parquet(parquet_path)

    # Ordered stratification column must survive
    assert isinstance(reloaded["house"].dtype, pd.CategoricalDtype)
    assert reloaded["house"].cat.ordered
    assert list(reloaded["house"].cat.categories) == houses

    # Unordered stratification column must survive as categorical
    assert isinstance(reloaded["familiar"].dtype, pd.CategoricalDtype)

    # value column is untouched
    assert not isinstance(reloaded["value"].dtype, pd.CategoricalDtype)


def test_value_only_frame_is_returned_unchanged() -> None:
    """A frame containing only the ``value`` column is returned with no categorical casting."""
    df = pd.DataFrame({"value": [1.0, 2.0, 3.0]})
    result = cast_non_value_columns_to_categorical(df)
    assert list(result.columns) == ["value"]
    assert not isinstance(result["value"].dtype, pd.CategoricalDtype)
    pd.testing.assert_series_equal(result["value"], df["value"])


def test_empty_frame_is_handled() -> None:
    """A zero-column frame and a zero-row frame with a non-value column are both handled without error; present non-value columns are still cast."""
    # Zero-row frame with a non-value column: column should still become Categorical
    df_zero_rows = pd.DataFrame(
        {"house": pd.Series([], dtype=str), "value": pd.Series([], dtype=float)}
    )
    result_zero_rows = cast_non_value_columns_to_categorical(df_zero_rows)
    assert isinstance(result_zero_rows["house"].dtype, pd.CategoricalDtype)
    assert not isinstance(result_zero_rows["value"].dtype, pd.CategoricalDtype)

    # Zero-column frame: should be returned without error
    df_zero_cols = pd.DataFrame()
    result_zero_cols = cast_non_value_columns_to_categorical(df_zero_cols)
    assert list(result_zero_cols.columns) == []


def test_input_frame_is_not_mutated() -> None:
    """The input frame is not modified in place; a new copy is returned."""
    df = pd.DataFrame(
        {
            "house": ["gryffindor", "slytherin", "ravenclaw"],
            "value": [1.0, 2.0, 3.0],
        }
    )
    original_house_dtype = df["house"].dtype
    original_value_dtype = df["value"].dtype
    original_id = id(df)

    result = cast_non_value_columns_to_categorical(df)

    # Input frame's dtypes are unchanged
    assert df["house"].dtype == original_house_dtype
    assert df["value"].dtype == original_value_dtype
    # The returned object is a different frame
    assert id(result) != original_id
