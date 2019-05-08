import pytest
import pandas as pd

from vivarium_cluster_tools.runner import np_concat_preserve_types, concat_results


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

    for c in df:
        assert (result[c] == df[c].append(df2[c]).reset_index(drop=True)).all()

    assert result.shape == expected_shape
    assert (result.dtypes == expected_dtypes).all()


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
def test_concat_results(data_types):
    old = pd.DataFrame([data_types])
    new = pd.DataFrame([[d*2 for d in data_types]])

    old['input_draw'] = old['random_seed'] = 0.0
    new['input_draw'] = new['random_seed'] = 1.0

    old = old.set_index(['input_draw', 'random_seed'], drop=False)
    new = new.set_index(['input_draw', 'random_seed'], drop=False)

    combined = concat_results(old, [new])

    expected_dtypes = old.dtypes
    if (expected_dtypes == 'float64').any():
        expected_dtypes.loc[expected_dtypes == 'int64'] = 'float64'  # np converts int64 to floats if any floats

    expected_shape = (old.shape[0] + new.shape[0], old.shape[1])

    for c in old:
        assert (combined[c] == new[c].append(old[c])).all()

    assert combined.shape == expected_shape
    assert (combined.dtypes == expected_dtypes).all()
