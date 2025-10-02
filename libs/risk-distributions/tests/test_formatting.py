from itertools import product

import numpy as np
import pandas as pd
import pytest

from risk_distributions.formatting import (
    cast_to_series,
    format_call_data,
    format_data,
    format_data_frame,
)

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


@pytest.mark.parametrize("data", ["string", {1, 2, 3}, None])
def test_format_data_unsupported_types(data):
    """Test format_data with unsupported data types."""
    with pytest.raises(TypeError, match="Unsupported data type"):
        format_data(data, ["param1"], "test")


@pytest.mark.parametrize(
    "data, required_columns, expected",
    [
        (np.array([1, 2, 3]), ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        (np.array([[1, 2, 3]]), ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        (
            np.array([1, 2, 3]),
            ["a", "b", "c"],
            pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"]),
        ),
        (
            np.array([[1, 2, 3]]),
            ["a", "b", "c"],
            pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"]),
        ),
        (
            np.array([[1, 4], [2, 5], [3, 6]]),
            ["a", "b"],
            pd.DataFrame([[1, 4], [2, 5], [3, 6]], columns=["a", "b"]),
        ),
        (
            np.array([[1, 2, 3], [4, 5, 6]]),
            ["a", "b"],
            pd.DataFrame([[1, 4], [2, 5], [3, 6]], columns=["a", "b"]),
        ),
    ],
)
def test_format_array_success(data, required_columns, expected):
    """Test successful format_array operations."""
    result = format_data(data, required_columns, "test")
    pd.testing.assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "data, required_columns, expected_error",
    [
        (np.array([]), ["param1"], "No data provided for test"),
        (
            np.array([1, 2]),
            ["a", "b", "c"],
            "2 values provided for test when 3 were expected",
        ),
        (
            np.array([[1, 2]]),
            ["a", "b", "c"],
            "2 values provided for test when 3 were expected",
        ),
        (
            np.array([[1, 2], [3, 4]]),
            ["param1"],
            "2D array provided for test where values for a single parameter were expected",
        ),
        (
            np.array([[1, 2, 3], [4, 5, 6]]),
            ["a", "b", "c", "d"],
            "Expected one axis in test data to have length 4",
        ),
        (np.array([[[1, 2], [3, 4]]]), ["param1"], "Invalid data shape"),
    ],
)
def test_format_array_errors(data, required_columns, expected_error):
    """Test format_array error cases."""
    with pytest.raises(ValueError, match=expected_error):
        format_data(data, required_columns, "test")


@pytest.mark.parametrize(
    "data, required_columns, expected",
    [
        (pd.Series([1, 2, 3]), ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        (
            pd.Series([1, 2, 3], index=["x", "y", "z"]),
            ["param1"],
            pd.DataFrame([1, 2, 3], columns=["param1"], index=["x", "y", "z"]),
        ),
        (
            pd.Series([1, 2, 3]),
            ["a", "b", "c"],
            pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"]),
        ),
        (
            pd.Series([10, 20, 30], index=["a", "b", "c"]),
            ["a", "b", "c"],
            pd.DataFrame([[10, 20, 30]], columns=["a", "b", "c"]),
        ),
        (
            pd.Series([30, 10, 20], index=["c", "a", "b"]),
            ["a", "b", "c"],
            pd.DataFrame([[30, 10, 20]], columns=["c", "a", "b"]),
        ),
    ],
)
def test_format_series_success(data, required_columns, expected):
    """Test successful format_series operations."""
    result = format_data(data, required_columns, "test")
    pd.testing.assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "data, required_columns, expected_error",
    [
        (pd.Series([]), ["param1"], "No data provided for test"),
        (
            pd.Series([1, 2]),
            ["a", "b", "c"],
            "2 values provided for test when 3 were expected",
        ),
    ],
)
def test_format_series_errors(data, required_columns, expected_error):
    """Test format_series error cases."""
    with pytest.raises(ValueError, match=expected_error):
        format_data(data, required_columns, "test")


@pytest.mark.parametrize(
    "data, required_columns, expected",
    [
        (
            pd.DataFrame({"a": [1, 2], "b": [3, 4]}),
            ["a", "b"],
            pd.DataFrame({"a": [1, 2], "b": [3, 4]}),
        ),
        (
            pd.DataFrame({"b": [3, 4], "a": [1, 2]}),
            ["a", "b"],
            pd.DataFrame({"b": [3, 4], "a": [1, 2]}),
        ),
        (pd.DataFrame({"param1": [42]}), ["param1"], pd.DataFrame({"param1": [42]})),
        (
            pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]}),
            ["a", "b", "c"],
            pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]}),
        ),
    ],
)
def test_format_data_frame_success(data, required_columns, expected):
    """Test successful format_data_frame operations."""
    result = format_data_frame(data, required_columns, "test")
    pd.testing.assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "data, required_columns, expected",
    [
        ([1, 2, 3], ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        ((1, 2, 3), ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        ([[1, 2, 3]], ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        (((1, 2, 3),), ["param1"], pd.DataFrame([1, 2, 3], columns=["param1"])),
        ([1, 2, 3], ["a", "b", "c"], pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"])),
        ((1, 2, 3), ["a", "b", "c"], pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"])),
        (
            [[1, 4], [2, 5], [3, 6]],
            ["a", "b"],
            pd.DataFrame([[1, 4], [2, 5], [3, 6]], columns=["a", "b"]),
        ),
        (
            ((1, 4), (2, 5), (3, 6)),
            ["a", "b"],
            pd.DataFrame([[1, 4], [2, 5], [3, 6]], columns=["a", "b"]),
        ),
    ],
)
def test_format_list_like_success(data, required_columns, expected):
    """Test successful format_list_like operations."""
    result = format_data(data, required_columns, "test")
    pd.testing.assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "data, required_columns, expected_error",
    [
        ([], ["param1"], "No data provided for test"),
        ((), ["param1"], "No data provided for test"),
        ([[]], ["param1"], "No data provided for test"),
        ([1, 2], ["a", "b", "c"], "2 values provided for test when 3 were expected"),
    ],
)
def test_format_list_like_errors(data, required_columns, expected_error):
    """Test format_list_like error cases."""
    with pytest.raises(ValueError, match=expected_error):
        format_data(data, required_columns, "test")


@pytest.mark.parametrize(
    "data, required_columns, expected",
    [
        (
            {"a": 1, "b": 2, "c": 3},
            ["a", "b", "c"],
            pd.DataFrame({"a": [1], "b": [2], "c": [3]}),
        ),
        (
            {"a": [1, 4], "b": [2, 5], "c": [3, 6]},
            ["a", "b", "c"],
            pd.DataFrame({"a": [1, 4], "b": [2, 5], "c": [3, 6]}),
        ),
        ({"a": (1, 4), "b": (2, 5)}, ["a", "b"], pd.DataFrame({"a": [1, 4], "b": [2, 5]})),
        (
            {"a": np.array([1, 4]), "b": np.array([2, 5])},
            ["a", "b"],
            pd.DataFrame({"a": [1, 4], "b": [2, 5]}),
        ),
        (
            {"c": 3, "a": 1, "b": 2},
            ["a", "b", "c"],
            pd.DataFrame({"c": [3], "a": [1], "b": [2]}),
        ),
    ],
)
def test_format_dict_success(data, required_columns, expected):
    """Test successful format_dict operations."""
    result = format_data(data, required_columns, "test")
    pd.testing.assert_frame_equal(result, expected)


@pytest.mark.parametrize(
    "data, required_columns, expected_error",
    [
        ({"x": 1, "y": 2}, ["a", "b"], "must supply only keys"),
        ({"a": 1}, ["a", "b"], "must supply only keys"),
        ({"a": 1, "b": 2, "c": 3}, ["a", "b"], "must supply only keys"),
        (
            {"a": [1, 2, 3], "b": [4, 5]},
            ["a", "b"],
            "same number of values for each parameter",
        ),
    ],
)
def test_format_dict_errors(data, required_columns, expected_error):
    """Test format_dict error cases."""
    with pytest.raises(ValueError, match=expected_error):
        format_data(data, required_columns, "test")


@pytest.mark.parametrize(
    "call_data, parameters, expected_call_index",
    [
        # Single parameter cases
        ([10, 20, 30], pd.DataFrame({"param1": [1, 2, 3]}), None),
        (42, pd.DataFrame({"param1": [1, 2, 3]}), None),
        (np.array([10, 20, 30]), pd.DataFrame({"param1": [1, 2, 3]}), None),
        # Multiple parameter cases - matching index
        (
            pd.Series([10, 20, 30], index=["a", "b", "c"]),
            pd.DataFrame({"p1": [1, 2, 3], "p2": [4, 5, 6]}, index=["a", "b", "c"]),
            ["a", "b", "c"],
        ),
        # Multiple parameter cases - list input
        (
            [10, 20, 30],
            pd.DataFrame({"p1": [1, 2, 3], "p2": [4, 5, 6]}, index=["a", "b", "c"]),
            ["a", "b", "c"],
        ),
        # Multiple parameter cases - scalar input
        (
            42,
            pd.DataFrame({"p1": [1, 2, 3], "p2": [4, 5, 6]}, index=["a", "b", "c"]),
            ["a", "b", "c"],
        ),
        # Multiple parameter cases - numpy array input
        (
            np.array([10, 20, 30]),
            pd.DataFrame({"p1": [1, 2, 3], "p2": [4, 5, 6]}, index=["a", "b", "c"]),
            ["a", "b", "c"],
        ),
    ],
)
def test_format_call_data_success(call_data, parameters, expected_call_index):
    """Test successful format_call_data operations."""
    result_call, result_params = format_call_data(call_data, parameters)
    assert isinstance(result_call, pd.Series)
    assert isinstance(result_params, pd.DataFrame)
    if expected_call_index is not None:
        assert list(result_call.index) == expected_call_index
        assert list(result_params.index) == expected_call_index


@pytest.mark.parametrize(
    "call_data, parameters, expected_error",
    [
        # Error case - mismatched index
        (
            pd.Series([10, 20, 30], index=["x", "y", "z"]),
            pd.DataFrame({"p1": [1, 2, 3], "p2": [4, 5, 6]}, index=["a", "b", "c"]),
            "indexed consistently",
        ),
    ],
)
def test_format_call_data_errors(call_data, parameters, expected_error):
    """Test format_call_data error cases."""
    with pytest.raises(ValueError, match=expected_error):
        format_call_data(call_data, parameters)
