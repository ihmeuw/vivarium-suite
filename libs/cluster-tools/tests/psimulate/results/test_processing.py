from pathlib import Path

import pandas as pd
import pytest

from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.results.processing import (
    _concat_metadata,
    _concat_preserve_types,
    write_results_batch,
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

    result = pd.DataFrame(_concat_preserve_types([df, df2]))

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

    combined = _concat_metadata(old, [new])

    expected_dtypes = old.dtypes

    expected_shape = (old.shape[0] + new.shape[0], old.shape[1])

    for c in old:
        assert (combined[c] == pd.concat([old[c], new[c]]).reset_index(drop=True)).all()

    assert combined.shape == expected_shape
    assert combined.dtypes.sort_index().equals(expected_dtypes.sort_index())

    # now no existing results
    no_old_combined = _concat_metadata(pd.DataFrame(), [old, new])

    assert no_old_combined.equals(combined)


def test_write_results_batch(tmp_path):
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    # create and save out already-existing metadata file and results
    existing_metadata_orig = pd.DataFrame({"rows": [1, 2], "batch": [1, 1]})
    existing_results_orig = {
        "results": pd.concat(
            [existing_metadata_orig, pd.DataFrame({"value": [10, 20]})], axis=1
        )
    }
    existing_metadata_orig.to_csv(output_paths.finished_sim_metadata, index=False)
    existing_results_orig["results"].to_parquet(output_paths.results_dir / "results.parquet")

    # create new metadata and results to write (larger than batch size of 2)
    unwritten_metadata_orig = [
        pd.DataFrame({"rows": [3], "batch": [2]}),
        pd.DataFrame({"rows": [4], "batch": [2]}),
        pd.DataFrame({"rows": [5], "batch": [3]}),
    ]
    unwritten_results_orig = []
    batch_size = 2
    for i in range(batch_size + 1):
        unwritten_results_orig += [
            {
                "results": pd.concat(
                    [unwritten_metadata_orig[i], pd.DataFrame({"value": [(i + 3) * 10]})],
                    axis=1,
                )
            }
        ]

    (
        existing_metadata,
        unwritten_metadata,
        existing_results,
        unwritten_results,
    ) = write_results_batch(
        output_paths,
        existing_metadata_orig,
        existing_results_orig,
        unwritten_metadata_orig,
        unwritten_results_orig,
        batch_size,
    )

    # check that updated existing metadata and results have been updated w/ a
    # new batch of 2
    assert existing_metadata.equals(
        pd.concat(
            [existing_metadata_orig] + unwritten_metadata_orig[:batch_size], axis=0
        ).reset_index(drop=True)
    )
    assert existing_results["results"].equals(
        pd.concat(
            [existing_results_orig["results"]]
            + [ur["results"] for ur in unwritten_results_orig[:batch_size]],
            axis=0,
        ).reset_index(drop=True)
    )

    # check that the same update metadata and results have been written out
    assert existing_metadata.equals(pd.read_csv(output_paths.finished_sim_metadata))
    assert existing_results["results"].equals(
        pd.read_parquet(output_paths.results_dir / "results.parquet")
    )

    # check that the leftover unwritten metadata and results are returned
    assert unwritten_metadata == unwritten_metadata_orig[batch_size:]
    assert unwritten_results == unwritten_results_orig[batch_size:]
