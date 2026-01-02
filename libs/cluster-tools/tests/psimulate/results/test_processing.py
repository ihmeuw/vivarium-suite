from pathlib import Path

import pandas as pd
import pytest

from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.results.processing import (
    OutputFileMap,
    _combine_batch_results,
    _concat_metadata,
    _concat_preserve_types,
    write_results_batch,
)

_DATA_TYPES = (
    list[int] | list[float] | list[str] | list[int | float] | list[int | float | str]
)


@pytest.mark.parametrize(
    "data",
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
def test_concat_preserve_types(data: _DATA_TYPES) -> None:
    df = pd.DataFrame([data])
    df2 = pd.DataFrame([[d * 2 for d in data]])

    result = pd.DataFrame(_concat_preserve_types([df, df2]))

    expected_dtypes = df.dtypes

    expected_shape = (df.shape[0] + df2.shape[0], df.shape[1])

    for c in df:
        assert (result[c] == pd.concat([df[c], df2[c]]).reset_index(drop=True)).all()

    assert result.shape == expected_shape
    assert result.dtypes.sort_index().equals(expected_dtypes)


@pytest.mark.parametrize(
    "data",
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
def test_concat_results(data: _DATA_TYPES) -> None:
    columns = [chr(i) for i in range(ord("a"), ord("a") + len(data))]
    old = pd.DataFrame([data], columns=columns)
    new = pd.DataFrame([[d * 2 for d in data]], columns=columns)

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


def test_write_results_batch(tmp_path: Path) -> None:
    """Test that one call writes exactly batch_size results to output files."""
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    # Start with existing metadata (simulating a restart scenario)
    existing_metadata_orig = pd.DataFrame({"rows": [1, 2], "batch": [1, 1]})
    existing_metadata_orig.to_csv(output_paths.finished_sim_metadata, index=False)

    # Create 3 unwritten results with batch_size=2
    # This means 2 should be written, 1 should remain unwritten
    batch_size = 2
    output_file_size = 100 * 1024 * 1024  # 100 MB
    output_file_map = OutputFileMap(output_paths.results_dir, output_file_size)

    unwritten_metadata_orig = [
        pd.DataFrame({"rows": [3], "batch": [2]}),
        pd.DataFrame({"rows": [4], "batch": [2]}),
        pd.DataFrame({"rows": [5], "batch": [3]}),  # This one should NOT be written
    ]
    unwritten_results_orig = [
        {"results": pd.DataFrame({"rows": [3], "batch": [2], "value": [30]})},
        {"results": pd.DataFrame({"rows": [4], "batch": [2], "value": [40]})},
        {"results": pd.DataFrame({"rows": [5], "batch": [3], "value": [50]})},
    ]

    (existing_metadata, unwritten_metadata, unwritten_results,) = write_results_batch(
        output_paths,
        existing_metadata_orig,
        unwritten_metadata_orig,
        unwritten_results_orig,
        batch_size,
        output_file_map,
        output_file_size,
    )

    # Check metadata was updated with only the batch_size items
    expected_metadata = pd.concat(
        [existing_metadata_orig] + unwritten_metadata_orig[:batch_size], axis=0
    ).reset_index(drop=True)
    assert existing_metadata.equals(expected_metadata)
    assert existing_metadata.equals(pd.read_csv(output_paths.finished_sim_metadata))

    # Check results were written to output files in metric directories
    results_metric_dir = output_paths.results_dir / "results"
    assert results_metric_dir.is_dir()
    output_files = list(results_metric_dir.glob("[0-9]*.parquet"))
    assert len(output_files) == 1

    # Check the chunk file contains exactly batch_size results
    output_file_results = pd.read_parquet(output_files[0])
    assert len(output_file_results) == batch_size
    assert output_file_results["value"].tolist() == [30, 40]

    # Check that the remaining item is returned for future batches
    assert len(unwritten_metadata) == 1
    assert len(unwritten_results) == 1
    assert unwritten_results[0]["results"]["value"].tolist() == [50]

    # Check output_file_map was updated
    assert output_file_map.metrics == {"results": 0}


def test_write_results_batch_multiple_calls(tmp_path: Path) -> None:
    """Test that multiple calls accumulate data in output files and pd.read_parquet() combines them."""
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    existing_metadata = pd.DataFrame()
    batch_size = 2
    output_file_size = 100 * 1024 * 1024  # 100 MB
    output_file_map = OutputFileMap(output_paths.results_dir, output_file_size)

    # First call: write 2 results
    unwritten_metadata_1 = [
        pd.DataFrame({"rows": [1], "batch": [1]}),
        pd.DataFrame({"rows": [2], "batch": [1]}),
    ]
    unwritten_results_1 = [
        {"results": pd.DataFrame({"rows": [1], "batch": [1], "value": [10]})},
        {"results": pd.DataFrame({"rows": [2], "batch": [1], "value": [20]})},
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_1,
        unwritten_results_1,
        batch_size=batch_size,
        output_file_map=output_file_map,
        output_file_size=output_file_size,
    )

    # Second call: write 2 more results (should append to same chunk)
    unwritten_metadata_2 = [
        pd.DataFrame({"rows": [3], "batch": [2]}),
        pd.DataFrame({"rows": [4], "batch": [2]}),
    ]
    unwritten_results_2 = [
        {"results": pd.DataFrame({"rows": [3], "batch": [2], "value": [30]})},
        {"results": pd.DataFrame({"rows": [4], "batch": [2], "value": [40]})},
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_2,
        unwritten_results_2,
        batch_size=batch_size,
        output_file_map=output_file_map,
        output_file_size=output_file_size,
    )

    # Check that there's still just 1 chunk file (all data fits)
    results_metric_dir = output_paths.results_dir / "results"
    output_files = sorted(results_metric_dir.glob("[0-9]*.parquet"))
    assert len(output_files) == 1

    # pd.read_parquet() on the directory returns all data
    all_results = pd.read_parquet(results_metric_dir)
    assert len(all_results) == 4
    assert set(all_results["value"].tolist()) == {10, 20, 30, 40}

    # Check metadata accumulated correctly across calls
    assert len(existing_metadata) == 4


def test_output_file_rotation_between_batches(tmp_path: Path) -> None:
    """Test that chunks rotate between batches when file size exceeds output_file_size."""
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    existing_metadata = pd.DataFrame()
    batch_size = 2
    output_file_size = 100 * 1024 * 1024  # 100 MB
    output_file_map = OutputFileMap(output_paths.results_dir, output_file_size)

    # First batch: write 2 results - creates 0000.parquet
    unwritten_metadata_1 = [pd.DataFrame({"rows": [i], "batch": [1]}) for i in range(2)]
    unwritten_results_1 = [
        {"results": pd.DataFrame({"rows": [i], "batch": [1], "value": [i * 10]})}
        for i in range(2)
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_1,
        unwritten_results_1,
        batch_size=batch_size,
        output_file_map=output_file_map,
        output_file_size=100 * 1024 * 1024,  # Large - no rotation within batch
    )

    # Get the size of the first chunk to use as output_file_size for second batch
    results_metric_dir = output_paths.results_dir / "results"
    first_output_file_size = (results_metric_dir / "0000.parquet").stat().st_size

    # Second batch with output_file_size = first_output_file_size means rotation should happen
    unwritten_metadata_2 = [pd.DataFrame({"rows": [i], "batch": [2]}) for i in range(2, 4)]
    unwritten_results_2 = [
        {"results": pd.DataFrame({"rows": [i], "batch": [2], "value": [i * 10]})}
        for i in range(2, 4)
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_2,
        unwritten_results_2,
        batch_size=batch_size,
        output_file_map=output_file_map,
        output_file_size=first_output_file_size,  # This will force rotation
    )

    # Check that 2 output files were created
    output_files = sorted(results_metric_dir.glob("[0-9]*.parquet"))
    assert len(output_files) == 2

    # Check output_file_map was updated
    assert output_file_map.metrics["results"] == 1

    # Check reading directory combines all chunks
    all_results = pd.read_parquet(results_metric_dir)
    assert len(all_results) == 4
    assert set(all_results["value"].tolist()) == {0, 10, 20, 30}


def test_batch_split_across_old_and_new_chunk(tmp_path: Path) -> None:
    """Test that a single batch can be split between an existing chunk and a new one."""
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    existing_metadata = pd.DataFrame()
    output_file_size = 100 * 1024 * 1024  # 100 MB
    output_file_map = OutputFileMap(output_paths.results_dir, output_file_size)

    # First batch: write 100 result to establish 0000.parquet
    unwritten_metadata_1 = [pd.DataFrame({"rows": [i], "batch": [1]}) for i in range(100)]
    unwritten_results_1 = [
        {"results": pd.DataFrame({"rows": [i], "batch": [1], "value": [i * 2]})}
        for i in range(100)
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_1,
        unwritten_results_1,
        batch_size=100,
        output_file_map=output_file_map,
        output_file_size=100 * 1024 * 1024,  # Large - no rotation yet
    )

    results_metric_dir = output_paths.results_dir / "results"
    first_output_file_size = (results_metric_dir / "0000.parquet").stat().st_size

    # Second batch: write many results with more restrictive output_file_size
    # This should split: some rows append to 0000.parquet, rest go to 0001.parquet
    unwritten_metadata_2 = [pd.DataFrame({"rows": [i], "batch": [2]}) for i in range(100)]
    unwritten_results_2 = [
        {"results": pd.DataFrame({"rows": [i + 100], "batch": [2], "value": [200 + 2 * i]})}
        for i in range(100)
    ]

    # Set output_file_size to near existing file size to force rotation during batch write
    output_file_size = int(first_output_file_size * 1.2)

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_2,
        unwritten_results_2,
        batch_size=100,
        output_file_map=output_file_map,
        output_file_size=output_file_size,
    )

    # Check that multiple output files were created
    output_files = sorted(results_metric_dir.glob("[0-9]*.parquet"))
    assert len(output_files) >= 2, f"Expected at least 2 chunks, got {len(output_files)}"

    # Check that 0000.parquet has more than 100 row (original + some from batch 2)
    output_file_0 = pd.read_parquet(results_metric_dir / "0000.parquet")
    assert len(output_file_0) > 100, "Chunk 0 should have data from both batches"

    # Check reading directory combines all chunks correctly
    all_results = pd.read_parquet(results_metric_dir)
    assert len(all_results) == 200
    assert set(all_results["value"].tolist()) == {2 * i for i in range(200)}


def test_large_batch_splits_into_multiple_new_chunks(tmp_path: Path) -> None:
    """Test that a single large batch can be split across multiple new output files."""
    output_paths = OutputPaths.from_entry_point_args(
        command="foo",
        input_artifact_path=Path("some/artifact/path"),
        result_directory=tmp_path,
        input_model_spec_path=Path("some/model/spec/path"),
    )
    output_paths.results_dir.mkdir()

    existing_metadata = pd.DataFrame()
    output_file_size = 100 * 1024 * 1024  # 100 MB
    output_file_map = OutputFileMap(output_paths.results_dir, output_file_size)

    # First batch: write 100 result to establish 0000.parquet
    unwritten_metadata_1 = [pd.DataFrame({"rows": [i], "batch": [1]}) for i in range(100)]
    unwritten_results_1 = [
        {"results": pd.DataFrame({"rows": [i], "batch": [1], "value": [i * 2]})}
        for i in range(100)
    ]

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_1,
        unwritten_results_1,
        batch_size=100,
        output_file_map=output_file_map,
        output_file_size=100 * 1024 * 1024,  # Large - no rotation yet
    )

    results_metric_dir = output_paths.results_dir / "results"
    first_output_file_size = (results_metric_dir / "0000.parquet").stat().st_size

    # Second batch: write many results with more restrictive output_file_size
    # This should split: some rows append to 0000.parquet, rest go to 0001.parquet
    unwritten_metadata_2 = [pd.DataFrame({"rows": [i], "batch": [2]}) for i in range(200)]
    unwritten_results_2 = [
        {"results": pd.DataFrame({"rows": [i + 100], "batch": [2], "value": [200 + 2 * i]})}
        for i in range(200)
    ]

    # Set output_file_size to near existing file size to force rotation during batch write
    output_file_size = int(first_output_file_size)

    existing_metadata, _, _ = write_results_batch(
        output_paths,
        existing_metadata,
        unwritten_metadata_2,
        unwritten_results_2,
        batch_size=200,
        output_file_map=output_file_map,
        output_file_size=output_file_size,
    )

    # Check that multiple output files were created
    output_files = sorted(results_metric_dir.glob("[0-9]*.parquet"))
    assert len(output_files) >= 3, f"Expected at least 3 chunks, got {len(output_files)}"

    # Check reading directory combines all chunks correctly
    all_results = pd.read_parquet(results_metric_dir)
    assert len(all_results) == 300
    assert set(all_results["value"].tolist()) == {2 * i for i in range(300)}


def test_output_file_map_from_existing_results(tmp_path: Path) -> None:
    """Test that OutputFileMapleMap.from_existing_results correctly scans existing output files."""
    # Create existing output files for two metrics
    metric_a_dir = tmp_path / "results" / "metric_a"
    metric_a_dir.mkdir(parents=True)
    metric_b_dir = tmp_path / "results" / "metric_b"
    metric_b_dir.mkdir(parents=True)

    # Write chunks for metric_a (two chunks)
    pd.DataFrame({"a": [1, 2, 3]}).to_parquet(metric_a_dir / "0000.parquet")
    pd.DataFrame({"a": [4, 5]}).to_parquet(metric_a_dir / "0001.parquet")

    # Write chunks for metric_b (one chunk)
    pd.DataFrame({"b": [10, 20]}).to_parquet(metric_b_dir / "0000.parquet")

    # Load output file map
    output_file_map = OutputFileMap.from_existing_results(tmp_path / "results", 1024 * 1024)

    # Should detect highest chunk number for each metric
    assert output_file_map.metrics == {"metric_a": 1, "metric_b": 0}
