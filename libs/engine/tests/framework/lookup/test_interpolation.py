import itertools
from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd
import pytest

from vivarium.engine.framework.lookup.interpolation import (
    ContinuousParameter,
    Interpolation,
    check_data_complete,
    validate_parameters,
)


def _continuous_param(name: str) -> ContinuousParameter:
    """Build a naming-only parameter record for direct validator calls."""
    return ContinuousParameter(name, f"{name}_start", f"{name}_end", np.array([]), None)


def make_bin_edges(data: pd.DataFrame, col: str) -> pd.DataFrame:
    """Given a dataframe and a column containing midpoints, construct
    equally sized bins around midpoints.
    """
    mid_pts = data[[col]].drop_duplicates().sort_values(by=col).reset_index(drop=True)
    mid_pts["shift"] = mid_pts[col].shift()

    mid_pts["start"] = mid_pts.apply(
        lambda row: (row[col] if pd.isna(row["shift"]) else 0.5 * (row[col] + row["shift"])),
        axis=1,
    )

    mid_pts["end"] = mid_pts["start"].shift(-1)
    mid_pts["end"] = mid_pts.end.fillna(
        mid_pts.end.max() + mid_pts.start.tolist()[-1] - mid_pts.start.tolist()[-2]
    )

    data = data.copy()
    idx = data.index

    data = data.set_index(col, drop=False)
    mid_pts = mid_pts.set_index(col, drop=False)

    data[[f"{col}_start", f"{col}_end"]] = mid_pts[["start", "end"]]

    return data.set_index(idx).drop(columns=[col])


def _order0_interpolation(
    data: pd.DataFrame,
    value_columns: Sequence[str] = ("value",),
    *,
    extrapolate: bool = True,
    validate: bool = True,
) -> Interpolation:
    """Build an order-0 Interpolation over ``data``."""
    return Interpolation(
        data,
        value_columns=pd.Index(list(value_columns)),
        order=0,
        extrapolate=extrapolate,
        validate=validate,
    )


@pytest.mark.skip(reason="only order 0 interpolation currently supported")
def test_1d_interpolation() -> None:
    df = pd.DataFrame({"a": np.arange(100), "b": np.arange(100), "c": np.arange(100, 0, -1)})
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["c"]),
        order=1,
        extrapolate=True,
        validate=True,
    )

    query = pd.DataFrame({"a": np.arange(0, 100, step=0.01)})

    assert np.allclose(query.a, i(query).b)
    assert np.allclose(100 - query.a, i(query).c)


@pytest.mark.skip(reason="only order 0 interpolation currently supported")
def test_age_year_interpolation() -> None:
    years = list(range(1990, 2010))
    ages = list(range(0, 90))
    pops = np.array(ages) * 11.1
    data = []
    for age, pop in zip(ages, pops):
        for year in years:
            for sex in ["Male", "Female"]:
                data.append({"age": age, "sex": sex, "year": year, "pop": pop})
    df = pd.DataFrame(data)

    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["pop"]),
        order=1,
        extrapolate=True,
        validate=True,
    )
    query = pd.DataFrame({"year": [1990, 1990], "age": [35, 35], "sex": ["Male", "Female"]})
    assert np.allclose(i(query), 388.5)


@pytest.mark.parametrize(
    "query",
    [
        pd.DataFrame({"year": [1990, 1990], "age": [35, 35]}),
        pd.DataFrame({"year": [1990, 1990], "sex": ["Male", "Female"]}),
    ],
)
@pytest.mark.skip(reason="only order 0 interpolation currently supported")
def test_interpolation_called_missing_param_col(query: pd.DataFrame) -> None:
    a = [range(1990, 1995), range(25, 30), ["Male", "Female"]]
    df = pd.DataFrame(list(itertools.product(*a)), columns=["year", "age", "sex"])
    df["pop"] = df.age * 11.1
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input
    i = Interpolation(
        df,
        value_columns=pd.Index(["pop"]),
        order=1,
        extrapolate=True,
        validate=True,
    )
    with pytest.raises(ValueError):
        i(query)


@pytest.mark.skip(reason="only order 0 interpolation currently supported")
def test_2d_interpolation() -> None:
    a = np.mgrid[0:5, 0:5][0].reshape(25)
    b = np.mgrid[0:5, 0:5][1].reshape(25)
    df = pd.DataFrame({"a": a, "b": b, "c": b, "d": a})
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["c", "d"]),
        order=1,
        extrapolate=True,
        validate=True,
    )

    query = pd.DataFrame({"a": np.arange(0, 4, step=0.01), "b": np.arange(0, 4, step=0.01)})

    assert np.allclose(query.b, i(query).c)
    assert np.allclose(query.a, i(query).d)


@pytest.mark.skip(reason="only order 0 interpolation currently supported")
def test_interpolation_with_categorical_parameters() -> None:
    a = ["one"] * 100 + ["two"] * 100
    b = np.append(np.arange(100), np.arange(100))
    c = np.append(np.arange(100), np.arange(100, 0, -1))
    df = pd.DataFrame({"a": a, "b": b, "c": c})
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["c"]),
        order=1,
        extrapolate=True,
        validate=True,
    )

    query_one = pd.DataFrame({"a": "one", "b": np.arange(0, 100, step=0.01)})
    query_two = pd.DataFrame({"a": "two", "b": np.arange(0, 100, step=0.01)})

    assert np.allclose(np.arange(0, 100, step=0.01), i(query_one).c)

    assert np.allclose(np.arange(0, 100, step=-0.01), i(query_two).c)


def test_order_zero_2d() -> None:
    a = np.mgrid[0:5, 0:5][0].reshape(25)
    b = np.mgrid[0:5, 0:5][1].reshape(25)
    df = pd.DataFrame({"a": a + 0.5, "b": b + 0.5, "c": b * 3, "garbage": ["test"] * len(a)})
    df = make_bin_edges(df, "a")
    df = make_bin_edges(df, "b")
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["c"]),
        order=0,
        extrapolate=True,
        validate=True,
    )

    column = np.arange(0.5, 4, step=0.011)
    query = pd.DataFrame({"a": column, "b": column, "garbage": ["test"] * (len(column))})

    assert np.allclose(query.b.astype(int) * 3, i(query).c)


def test_order_zero_2d_fails_on_extrapolation() -> None:
    a = np.mgrid[0:5, 0:5][0].reshape(25)
    b = np.mgrid[0:5, 0:5][1].reshape(25)
    df = pd.DataFrame({"a": a + 0.5, "b": b + 0.5, "c": b * 3, "garbage": ["test"] * len(a)})
    df = make_bin_edges(df, "a")
    df = make_bin_edges(df, "b")
    df = df.sample(frac=1)  # Shuffle table to assure interpolation works given unsorted input

    i = Interpolation(
        df,
        value_columns=pd.Index(["c"]),
        order=0,
        extrapolate=False,
        validate=True,
    )

    column = np.arange(0.0, 4.0, step=0.011)
    query = pd.DataFrame({"a": column, "b": column, "garbage": ["test"] * (len(column))})

    with pytest.raises(ValueError) as error:
        i(query)

    message = error.value.args[0]

    assert "Extrapolation" in message and "a" in message


def test_order_zero_1d_no_extrapolation() -> None:
    s = pd.Series({0: 0, 1: 1}, name="val").reset_index()
    s = make_bin_edges(s, "index")
    f = Interpolation(
        s,
        value_columns=pd.Index(["val"]),
        order=0,
        extrapolate=False,
        validate=True,
    )

    assert f(pd.DataFrame({"index": [0]}))["val"][0] == 0, "should be precise at index values"
    assert f(pd.DataFrame({"index": [0.999]}))["val"][0] == 1

    with pytest.raises(ValueError) as error:
        f(pd.DataFrame({"index": [1]}))

    message = error.value.args[0]
    assert "Extrapolation" in message and "index" in message


def test_order_zero_1d_constant_extrapolation() -> None:
    s = pd.Series({0: 0, 1: 1}, name="val").reset_index()
    s = make_bin_edges(s, "index")
    f = Interpolation(
        s,
        value_columns=pd.Index(["val"]),
        order=0,
        extrapolate=True,
        validate=True,
    )

    assert f(pd.DataFrame({"index": [1]}))["val"][0] == 1
    assert (
        f(pd.DataFrame({"index": [2]}))["val"][0] == 1
    ), "should be constant extrapolation outside of input range"
    assert f(pd.DataFrame({"index": [-1]}))["val"][0] == 0


def test_validate_parameters__empty_data() -> None:
    with pytest.raises(ValueError, match="must supply non-empty data"):
        validate_parameters(
            pd.DataFrame(
                columns=["age_start", "age_end", "sex", "year_start", "year_end", "value"]
            ),
            ["sex"],
            [_continuous_param("age"), _continuous_param("year")],
            ["value"],
        )


def test_validate_parameters__extra_columns() -> None:
    """Columns in ``data`` that are not declared as categorical, continuous
    bin-edge, or value columns trigger a structured error so that mis-wired
    callers fail loudly instead of silently ignoring data."""
    data = pd.DataFrame(
        {
            "sex": ["Male", "Female"],
            "age_start": [0, 50],
            "age_end": [50, 125],
            "value": [1.0, 2.0],
            "stowaway": [9, 9],
        }
    )
    with pytest.raises(ValueError, match="extra columns"):
        validate_parameters(
            data,
            ["sex"],
            [_continuous_param("age")],
            ["value"],
        )


def test_check_data_complete_gaps() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990, 1995, 1995],
            "year_end": [1995, 1995, 2000, 2000],
            "age_start": [16, 10, 10, 16],
            "age_end": [20, 15, 15, 20],
        }
    )

    with pytest.raises(NotImplementedError) as error:
        check_data_complete(data, [_continuous_param("year"), _continuous_param("age")])

    message = error.value.args[0]

    assert "age_start" in message and "age_end" in message


def test_check_data_complete_overlap() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1995, 1995, 2000, 2005, 2010],
            "year_end": [2000, 2000, 2005, 2010, 2015],
        }
    )

    with pytest.raises(ValueError) as error:
        check_data_complete(data, [_continuous_param("year")])

    message = error.value.args[0]

    assert "year_start" in message and "year_end" in message


def test_check_data_missing_combos() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990, 1995],
            "year_end": [1995, 1995, 2000],
            "age_start": [10, 15, 10],
            "age_end": [15, 20, 15],
        }
    )

    with pytest.raises(ValueError) as error:
        check_data_complete(data, [_continuous_param("year"), _continuous_param("age")])

    message = error.value.args[0]

    assert "combination" in message


def test_order_zero_3d_no_key_column() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990, 1990, 1990, 1995, 1995, 1995, 1995],
            "year_end": [1995, 1995, 1995, 1995, 2000, 2000, 2000, 2000],
            "age_start": [15, 10, 10, 15, 10, 10, 15, 15],
            "age_end": [20, 15, 15, 20, 15, 15, 20, 20],
            "height_start": [140, 160, 140, 160, 140, 160, 140, 160],
            "height_end": [160, 180, 160, 180, 160, 180, 160, 180],
            "value": [5, 3, 1, 7, 8, 6, 4, 2],
        }
    )

    interp = _order0_interpolation(data)

    interpolants = pd.DataFrame(
        {
            "age": [12, 17, 8, 24, 12],
            "year": [1992, 1998, 1985, 1992, 1992],
            "height": [160, 145, 140, 179, 160],
        }
    )

    result = interp(interpolants)
    assert result.equals(pd.DataFrame({"value": [3, 4, 1, 7, 3]}))


def test_order_zero_1d_with_key_column() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990, 1995, 1995],
            "year_end": [1995, 1995, 2000, 2000],
            "sex": ["Male", "Female", "Male", "Female"],
            "value_1": [10, 7, 2, 12],
            "value_2": [1200, 1350, 1476, 1046],
        }
    )

    i = Interpolation(
        data,
        value_columns=pd.Index(["value_1", "value_2"]),
        order=0,
        extrapolate=True,
        validate=True,
    )

    query = pd.DataFrame(
        {
            "year": [
                1992,
                1993,
            ],
            "sex": ["Male", "Female"],
        }
    )

    expected_result = pd.DataFrame({"value_1": [10, 7], "value_2": [1200, 1350]})

    assert i(query).equals(expected_result)


def test_order_zero_non_numeric_values() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990],
            "year_end": [1995, 1995],
            "age_start": [
                15,
                24,
            ],
            "age_end": [24, 30],
            "value_1": ["blue", "red"],
        }
    )

    i = Interpolation(
        data,
        value_columns=pd.Index(["value_1"]),
        order=0,
        extrapolate=True,
        validate=True,
    )

    query = pd.DataFrame(
        {
            "year": [1990, 1990],
            "age": [
                15,
                24,
            ],
        },
        index=[1, 0],
    )

    expected_result = pd.DataFrame({"value_1": ["blue", "red"]}, index=[1, 0])

    assert i(query).equals(expected_result)


def test_order_zero_3d_with_key_col() -> None:
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990, 1990, 1990, 1995, 1995, 1995, 1995] * 2,
            "year_end": [1995, 1995, 1995, 1995, 2000, 2000, 2000, 2000] * 2,
            "age_start": [15, 10, 10, 15, 10, 10, 15, 15] * 2,
            "age_end": [20, 15, 15, 20, 15, 15, 20, 20] * 2,
            "height_start": [140, 160, 140, 160, 140, 160, 140, 160] * 2,
            "height_end": [160, 180, 160, 180, 160, 180, 160, 180] * 2,
            "sex": ["Male"] * 8 + ["Female"] * 8,
            "value": [5, 3, 1, 7, 8, 6, 4, 2, 6, 4, 2, 8, 9, 7, 5, 3],
        }
    )

    interp = Interpolation(
        data,
        value_columns=pd.Index(["value"]),
        order=0,
        extrapolate=True,
        validate=True,
    )

    interpolants = pd.DataFrame(
        {
            "age": [12, 17, 8, 24, 12],
            "year": [1992, 1998, 1985, 1992, 1992],
            "height": [160, 145, 140, 185, 160],
            "sex": ["Male", "Female", "Female", "Male", "Male"],
        },
        index=[10, 4, 7, 0, 9],
    )

    result = interp(interpolants)
    assert result.equals(pd.DataFrame({"value": [3, 5, 2, 7, 3]}, index=[10, 4, 7, 0, 9]))


def test_order_zero_diff_bin_sizes() -> None:
    data = pd.DataFrame(
        {
            "year_start": [
                1990,
                1995,
                1996,
                2005,
                2005.5,
            ],
            "year_end": [1995, 1996, 2005, 2005.5, 2010],
            "value": [1, 5, 2.3, 6, 100],
        }
    )

    i = Interpolation(
        data,
        value_columns=pd.Index(["value"]),
        order=0,
        extrapolate=False,
        validate=True,
    )

    query = pd.DataFrame({"year": [2007, 1990, 2005.4, 1994, 2004, 1995, 2002, 1995.5, 1996]})

    expected_result = pd.DataFrame({"value": [100, 1, 6, 1, 2.3, 5, 2.3, 5, 2.3]})

    assert i(query).equals(expected_result)


@pytest.mark.parametrize("validate", [True, False])
def test_interpolation_init_validate_option_invalid_data(validate: bool) -> None:
    if validate:
        with pytest.raises(
            ValueError, match="You must supply non-empty data to create the interpolation."
        ):
            Interpolation(
                pd.DataFrame(),
                value_columns=pd.Index([]),
                order=0,
                extrapolate=True,
                validate=validate,
            )
    else:
        Interpolation(
            pd.DataFrame(),
            value_columns=pd.Index([]),
            order=0,
            extrapolate=True,
            validate=validate,
        )


@pytest.mark.parametrize("validate", [True, False])
def test_interpolation_init_validate_option_valid_data(validate: bool) -> None:
    s = pd.Series({0: 0, 1: 1}, name="val").reset_index()
    s = make_bin_edges(s, "index")
    Interpolation(
        s,
        value_columns=pd.Index(["val"]),
        order=0,
        extrapolate=True,
        validate=validate,
    )


@pytest.mark.parametrize("validate", [True, False])
def test_interpolation_call_validate_option_invalid_data(validate: bool) -> None:
    s = pd.Series({0: 0, 1: 1}, name="val").reset_index()
    s = make_bin_edges(s, "index")
    i = Interpolation(
        s,
        value_columns=pd.Index(["val"]),
        order=0,
        extrapolate=True,
        validate=validate,
    )
    if validate:
        with pytest.raises(
            TypeError, match=r"Interpolations can only be called on pandas.DataFrames.*"
        ):
            result = i(cast(pd.DataFrame, 1))
    else:
        with pytest.raises(AttributeError):
            result = i(cast(pd.DataFrame, 1))


@pytest.mark.parametrize("validate", [True, False])
def test_interpolation_call_validate_option_valid_data(validate: bool) -> None:
    data = pd.DataFrame(
        {
            "year_start": [
                1990,
                1995,
                1996,
                2005,
                2005.5,
            ],
            "year_end": [1995, 1996, 2005, 2005.5, 2010],
            "value": [1, 5, 2.3, 6, 100],
        }
    )

    i = Interpolation(
        data,
        value_columns=pd.Index(["value"]),
        order=0,
        extrapolate=False,
        validate=validate,
    )
    query = pd.DataFrame({"year": [2007, 1990, 2005.4, 1994, 2004, 1995, 2002, 1995.5, 1996]})

    result = i(query)


def test_multiple_categorical_columns() -> None:
    """Two categorical key columns (e.g. sex and location) select the correct value for each (sex, location) group."""
    data = pd.DataFrame(
        {
            "year_start": [1990, 1995, 1990, 1995, 1990, 1995, 1990, 1995],
            "year_end": [1995, 2000, 1995, 2000, 1995, 2000, 1995, 2000],
            "sex": [
                "Male",
                "Male",
                "Male",
                "Male",
                "Female",
                "Female",
                "Female",
                "Female",
            ],
            "location": ["A", "A", "B", "B", "A", "A", "B", "B"],
            "value": [1, 2, 3, 4, 5, 6, 7, 8],
        }
    )

    interp = _order0_interpolation(data)

    query = pd.DataFrame(
        {
            "year": [1992, 1998, 1992, 1998, 1992],
            "sex": ["Male", "Male", "Female", "Female", "Male"],
            "location": ["A", "B", "A", "B", "B"],
        }
    )

    # (Male, A, 1992)->1; (Male, B, 1998)->4; (Female, A, 1992)->5;
    # (Female, B, 1998)->8; (Male, B, 1992)->3.
    expected = pd.DataFrame({"value": [1, 4, 5, 8, 3]})
    assert interp(query).equals(expected)


def test_no_merge_fanout_on_shared_bin_edges() -> None:
    """A bin start shared across many categorical groups must not fan out: output length equals input length with the correct per-group value for every simulant."""
    data = pd.DataFrame(
        {
            "location": ["A", "B", "C", "D", "E"],
            "year_start": [1990, 1990, 1990, 1990, 1990],
            "year_end": [2000, 2000, 2000, 2000, 2000],
            "value": [10, 20, 30, 40, 50],
        }
    )

    interp = _order0_interpolation(data)

    query = pd.DataFrame(
        {
            "location": ["A", "B", "C", "D", "E"],
            "year": [1995, 1991, 1999, 1993, 1997],
        }
    )

    result = interp(query)
    assert len(result) == len(query)
    assert result.equals(pd.DataFrame({"value": [10, 20, 30, 40, 50]}))


class TestNonUniformBinsAcrossGroups:
    """Bin edges that differ across categorical groups — the data shape uniform-bins validation rejects."""

    @pytest.fixture
    def data(self) -> pd.DataFrame:
        # Male has bins [1990, 1995), [1995, 2000); Female has a single [1990, 2000).
        return pd.DataFrame(
            {
                "sex": ["Male", "Male", "Female"],
                "year_start": [1990, 1995, 1990],
                "year_end": [1995, 2000, 2000],
                "value": [1, 2, 3],
            }
        )

    def test_validate_true_raises_at_construction(self, data: pd.DataFrame) -> None:
        """Construction raises, naming the offending parameter."""
        with pytest.raises(ValueError, match="different bin edges") as error:
            _order0_interpolation(data)
        assert "year" in str(error.value)

    def test_validate_false_constructs_and_resolves(self, data: pd.DataFrame) -> None:
        """With validate=False the non-uniform data is accepted at construction."""
        interp = _order0_interpolation(data, validate=False)
        result = interp(pd.DataFrame({"sex": ["Male", "Male"], "year": [1992, 1997]}))
        assert result["value"].tolist() == [1, 2]


def test_unknown_category_in_query_raises() -> None:
    """Calling with a categorical value that is absent from the source data raises KeyError."""
    data = pd.DataFrame(
        {
            "year_start": [1990, 1990],
            "year_end": [2000, 2000],
            "sex": ["Male", "Female"],
            "value": [1, 2],
        }
    )

    interp = _order0_interpolation(data)

    query = pd.DataFrame({"year": [1995], "sex": ["Other"]})
    with pytest.raises(KeyError, match="absent from the interpolation"):
        interp(query)


def test_purely_categorical_table() -> None:
    """A table with only categorical parameters broadcasts each group's value."""
    data = pd.DataFrame({"sex": ["Male", "Female"], "value": [1, 2]})
    interp = _order0_interpolation(data)

    query = pd.DataFrame({"sex": ["Male", "Male", "Female"]})
    result = interp(query)
    assert result.equals(pd.DataFrame({"value": [1, 1, 2]}))


class TestDuplicateKeyRows:
    """Rows that share one lookup key — duplicates must raise, never silently resolve."""

    @pytest.fixture
    def data(self) -> pd.DataFrame:
        # "Male" appears twice with conflicting values.
        return pd.DataFrame(
            {
                "sex": ["Male", "Male", "Female"],
                "value": [1, 99, 2],
            }
        )

    def test_validate_true_raises_at_construction(self, data: pd.DataFrame) -> None:
        """Construction raises, naming the duplicated keys."""
        with pytest.raises(ValueError, match="uniquely identified") as error:
            _order0_interpolation(data)
        assert "Male" in str(error.value)

    def test_validate_false_raises_on_call(self, data: pd.DataFrame) -> None:
        """With validate=False the duplicate surfaces as a MergeError at call time."""
        interp = _order0_interpolation(data, validate=False)
        with pytest.raises(pd.errors.MergeError):
            interp(pd.DataFrame({"sex": ["Male"]}))


def test_no_parameters_broadcasts_value() -> None:
    """A single-row table with neither categorical nor continuous parameters broadcasts its value to every interpolant."""
    data = pd.DataFrame({"value": [42]})
    interp = _order0_interpolation(data)

    # No parameter columns, so the query columns are irrelevant; every row gets
    # the data row's value.
    query = pd.DataFrame({"ignored": [1, 2, 3]}, index=[10, 11, 12])
    result = interp(query)
    assert result["value"].tolist() == [42, 42, 42]
    assert result.index.equals(query.index)


def test_no_parameters_multi_row_raises() -> None:
    """A multi-row table with no parameters is ambiguous and raises at construction."""
    data = pd.DataFrame({"value": [42, 99]})
    with pytest.raises(ValueError, match="single row"):
        _order0_interpolation(data)


def test_integer_value_dtype_preserved() -> None:
    """Interpolating an integer value column over fully in-range interpolants returns an integer dtype column (no NaN-driven upcast to float)."""
    data = pd.DataFrame(
        {
            "year_start": [1990, 1995],
            "year_end": [1995, 2000],
            "value": [10, 20],
        }
    )
    assert pd.api.types.is_integer_dtype(data["value"])

    interp = _order0_interpolation(data)

    query = pd.DataFrame({"year": [1992, 1998]})
    result = interp(query)
    assert pd.api.types.is_integer_dtype(result["value"])
    assert result["value"].tolist() == [10, 20]


def test_empty_interpolants_returns_float64_frame() -> None:
    """Calling with an empty interpolant frame returns an empty float64 DataFrame carrying the value columns and the interpolant index."""
    data = pd.DataFrame(
        {
            "year_start": [1990, 1995],
            "year_end": [1995, 2000],
            "value": [1, 2],
        }
    )

    interp = _order0_interpolation(data)

    query = pd.DataFrame({"year": pd.Series([], dtype="float64")})
    result = interp(query)
    assert list(result.columns) == ["value"]
    assert len(result) == 0
    assert result["value"].dtype == np.dtype("float64")
    assert result.index.equals(query.index)


def test_order_zero_multi_group() -> None:
    """A many-group table (multiple categorical groups sharing bin edges) returns the exact expected per-simulant bin values — a regression for the categorical + binned lookup."""
    locations = ["USA", "Canada", "Mexico"]
    sexes = ["Female", "Male"]
    age_bins = [(0, 5), (5, 10)]
    year_bins = [(1990, 2000), (2000, 2010)]

    rows = []
    for loc_idx, location in enumerate(locations):
        for sex_idx, sex in enumerate(sexes):
            for age_idx, (age_start, age_end) in enumerate(age_bins):
                for year_idx, (year_start, year_end) in enumerate(year_bins):
                    value = 1000 * loc_idx + 100 * sex_idx + 10 * age_idx + year_idx
                    rows.append(
                        {
                            "location": location,
                            "sex": sex,
                            "age_start": age_start,
                            "age_end": age_end,
                            "year_start": year_start,
                            "year_end": year_end,
                            "value": value,
                            # A second (float) value column guards against column
                            # misalignment through the lookup.
                            "value2": value + 0.5,
                        }
                    )
    data = pd.DataFrame(rows)

    interp = _order0_interpolation(data, ("value", "value2"))

    query = pd.DataFrame(
        {
            "location": ["USA", "Mexico", "Canada", "USA", "Mexico", "Canada"],
            "sex": ["Female", "Male", "Female", "Male", "Female", "Male"],
            "age": [2, 7, 4, 9, 0, 5],
            "year": [1995, 2005, 2001, 1990, 1999, 2009],
        },
        index=[5, 2, 8, 0, 7, 3],
    )

    # Computed by hand from value = 1000*loc + 100*sex + 10*age_bin + year_bin,
    # with loc {USA:0, Canada:1, Mexico:2}, sex {Female:0, Male:1},
    # age_bin {[0,5):0, [5,10):1}, year_bin {[1990,2000):0, [2000,2010):1}.
    values = [0, 2111, 1001, 110, 2000, 1111]
    expected = pd.DataFrame(
        {"value": values, "value2": [v + 0.5 for v in values]},
        index=[5, 2, 8, 0, 7, 3],
    )
    assert interp(query).equals(expected)
