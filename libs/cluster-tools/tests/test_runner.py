import pytest
import pandas as pd

from vivarium_cluster_tools.runner import np_concat_preserve_types


@pytest.mark.parametrize('data_types', [[0, 1, 2],
                                        [0, 0.0, 1, 2, 3],
                                        [0.0, 1.0, 1.987987],
                                        [0, 'a', 1, 'b', 1.2],
                                        ['a', 'b', 'c', 'd']],
                                        ids=['just ints',
                                             'ints and floats',
                                             'just floats',
                                             'ints, floats, and strings',
                                             'just strings'])
def test_np_concat_preserve_types(data_types):
    df = pd.DataFrame([data_types])
    df2 = pd.DataFrame([[d*2 for d in data_types]])

    result = pd.DataFrame(np_concat_preserve_types([df, df2]))

    expected_dtypes = df.dtypes
    if (expected_dtypes == 'float64').any():
        expected_dtypes.loc[expected_dtypes == 'int64'] = 'float64'  # np converts int64 to floats if any floats

    expected_shape = (df.shape[0] + df2.shape[0], df.shape[1])

    assert result.shape == expected_shape
    assert (result.dtypes == expected_dtypes).all()


