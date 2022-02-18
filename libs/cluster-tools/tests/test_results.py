import pandas as pd
import pytest

from vivarium_cluster_tools.psimulate.results import (
    concat_preserve_types,
    concat_results,
)


@pytest.mark.parametrize(
    "data_types",
    [
        [0, 1, 2],
        [0, 0.0, 1, 2, 3],
        [0.0, 1.0, 1.987987],
        [0, "a", 1, "b", 1.2],
        ["a", "b", "c", "d"],
    ],
    ids=[
        "just ints",
        "ints and floats",
        "just floats",
        "ints, floats, and strings",
        "just strings",
    ],
)
def test_concat_preserve_types(data_types):
    df = pd.DataFrame([data_types])
    df2 = pd.DataFrame([[d * 2 for d in data_types]])

    result = pd.DataFrame(concat_preserve_types([df, df2]))

    expected_dtypes = df.dtypes

    expected_shape = (df.shape[0] + df2.shape[0], df.shape[1])

    for c in df:
        assert (result[c] == pd.concat([df[c], df2[c]]).reset_index(drop=True)).all()

    assert result.shape == expected_shape
    assert result.dtypes.sort_index().equals(expected_dtypes)


@pytest.mark.parametrize(
    "data_types",
    [
        [0, 1, 2],
        [0, 0.0, 1, 2, 3],
        [0.0, 1.0, 1.987987],
        [0, "a", 1, "b", 1.2],
        ["a", "b", "c", "d"],
    ],
    ids=[
        "just ints",
        "ints and floats",
        "just floats",
        "ints, floats, and strings",
        "just strings",
    ],
)
def test_concat_results(data_types):
    columns = [chr(i) for i in range(ord("a"), ord("a") + len(data_types))]
    old = pd.DataFrame([data_types], columns=columns)
    new = pd.DataFrame([[d * 2 for d in data_types]], columns=columns)

    old["input_draw"] = old["random_seed"] = 0.0
    new["input_draw"] = new["random_seed"] = 1.0

    combined = concat_results(old, [new])

    expected_dtypes = old.dtypes

    expected_shape = (old.shape[0] + new.shape[0], old.shape[1])

    for c in old:
        assert (combined[c] == (new[c].append(old[c])).reset_index(drop=True)).all()

    assert combined.shape == expected_shape
    assert combined.dtypes.sort_index().equals(expected_dtypes.sort_index())

    # now no existing results
    no_old_combined = concat_results(pd.DataFrame(), [new, old])

    assert no_old_combined.equals(combined)
