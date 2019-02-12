from itertools import product

import numpy as np
import pandas as pd
import pytest

from risk_distributions.formatting import cast_to_series


valid_inputs = (np.array([1]), pd.Series([1]), [1], (1,), 1)


@pytest.mark.parametrize('mean, sd', product(valid_inputs, valid_inputs))
def test_cast_to_series_single_ints(mean, sd):
    expected_mean, expected_sd = pd.Series([1]), pd.Series([1])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


valid_inputs = (np.array([1.]), pd.Series([1.]), [1.], (1.,), 1.)


@pytest.mark.parametrize('mean, sd', product(valid_inputs, valid_inputs))
def test_cast_to_series_single_floats(mean, sd):
    expected_mean, expected_sd = pd.Series([1.]), pd.Series([1.])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


valid_inputs = (np.array([1, 2, 3]), pd.Series([1, 2, 3]), [1, 2, 3], (1, 2, 3))


@pytest.mark.parametrize('mean, sd', product(valid_inputs, valid_inputs))
def test_cast_to_series_array_like(mean, sd):
    expected_mean, expected_sd = pd.Series([1, 2, 3]), pd.Series([1, 2, 3])
    out_mean, out_sd = cast_to_series(mean, sd)
    assert expected_mean.equals(out_mean)
    assert expected_sd.equals(out_sd)


reference = pd.Series([1, 2, 3], index=['a', 'b', 'c'])
valid_inputs = (np.array([1, 2, 3]), reference, [1, 2, 3], (1, 2, 3))


@pytest.mark.parametrize('reference, other', product([reference], valid_inputs))
def test_cast_to_series_indexed(reference, other):
    out_mean, out_sd = cast_to_series(reference, other)
    assert reference.equals(out_mean)
    assert reference.equals(out_sd)

    out_mean, out_sd = cast_to_series(other, reference)
    assert reference.equals(out_mean)
    assert reference.equals(out_sd)


null_inputs = (np.array([]), pd.Series([]), [], ())


@pytest.mark.parametrize('val, null', product([1], null_inputs))
def test_cast_to_series_nulls(val, null):
    with pytest.raises(ValueError, match='Empty data structure'):
        cast_to_series(val, null)

    with pytest.raises(ValueError, match='Empty data structure'):
        cast_to_series(null, val)
