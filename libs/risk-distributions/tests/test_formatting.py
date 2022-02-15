from itertools import product

import numpy as np
import pandas as pd
import pytest

from risk_distributions.formatting import cast_to_series, format_data_frame

valid_inputs = (np.array([1]), pd.Series([1]), [1], (1,), 1)


@pytest.mark.parametrize("mean, sd", product(valid_inputs, valid_inputs))
def test_cast_to_series_single_ints(mean, sd):
    expected_mean, expected_sd = pd.Series([1]), pd.Series([1])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


valid_inputs = (np.array([1.0]), pd.Series([1.0]), [1.0], (1.0,), 1.0)


@pytest.mark.parametrize("mean, sd", product(valid_inputs, valid_inputs))
def test_cast_to_series_single_floats(mean, sd):
    expected_mean, expected_sd = pd.Series([1.0]), pd.Series([1.0])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


valid_inputs = (np.array([1, 2, 3]), pd.Series([1, 2, 3]), [1, 2, 3], (1, 2, 3))


@pytest.mark.parametrize("mean, sd", product(valid_inputs, valid_inputs))
def test_cast_to_series_array_like(mean, sd):
    expected_mean, expected_sd = pd.Series([1, 2, 3]), pd.Series([1, 2, 3])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


reference = pd.Series([1, 2, 3], index=["a", "b", "c"])
valid_inputs = (np.array([1, 2, 3]), reference, [1, 2, 3], (1, 2, 3))


@pytest.mark.parametrize("reference, other", product([reference], valid_inputs))
def test_cast_to_series_indexed(reference, other):
    out_mean, out_sd = cast_to_series(reference, other)
    assert reference.equals(out_mean)
    assert reference.equals(out_sd)

    out_mean, out_sd = cast_to_series(other, reference)
    assert reference.equals(out_mean)
    assert reference.equals(out_sd)


null_inputs = (np.array([]), pd.Series([]), [], ())


@pytest.mark.parametrize("val, null", product([1], null_inputs))
def test_cast_to_series_nulls(val, null):
    with pytest.raises(ValueError, match="Empty data structure"):
        cast_to_series(val, null)

    with pytest.raises(ValueError, match="Empty data structure"):
        cast_to_series(null, val)


def test_cast_to_series_mismatched_index():
    reference = pd.Series([1, 2, 3], index=["a", "b", "c"])
    other = pd.Series([1, 2, 3])

    with pytest.raises(ValueError, match="identically indexed"):
        cast_to_series(reference, other)

    with pytest.raises(ValueError, match="identically indexed"):
        cast_to_series(other, reference)


reference = (np.array([1, 2, 3]), pd.Series([1, 2, 3]), [1, 2, 3], (1, 2, 3))
invalid = (
    np.array([1]),
    pd.Series([1]),
    [1],
    (1,),
    1,
    1.0,
    np.arange(5),
    pd.Series(np.arange(5)),
    list(range(5)),
    tuple(range(5)),
)


@pytest.mark.parametrize("reference, other", product(reference, invalid))
def test_cast_to_series_mismatched_length(reference, other):
    with pytest.raises(ValueError, match="same number of values"):
        cast_to_series(reference, other)

    with pytest.raises(ValueError, match="same number of values"):
        cast_to_series(other, reference)


@pytest.mark.parametrize(
    "data_columns, required_columns, match",
    [
        (["a", "b", "c"], ["b", "c"], "extra columns"),
        (["a", "b"], ["a", "b", "c"], "missing columns"),
        ([], ["a"], "No data"),
    ],
)
def test_format_data_frame(data_columns, required_columns, match):
    data = pd.DataFrame(data={c: [1] for c in data_columns}, index=[0])

    with pytest.raises(ValueError, match=match):
        format_data_frame(data, required_columns, measure="test")
