"""Integration test for run_benchmark module."""

import os
from pathlib import Path
import pandas as pd
import pytest

from vivarium_profiling.tools.run_benchmark import (
    run_benchmark_loop,
    RESULTS_SUMMARY_NAME,
    RESULTS_SUMMARY_COLUMNS,
)


@pytest.mark.slow
def test_run_benchmark_loop_integration(test_model_specs: list[str], tmp_path: Path):
    """Integration test for run_benchmark_loop with minimal real model specs.

    This test verifies that:
    1. The benchmark runs successfully with minimal model configurations
    2. Results are generated for each simulation run
    3. The summary CSV contains the expected number of rows and columns
    4. Each model spec and run combination has a corresponding row in the results
    """
    # Use a temporary directory for output
    output_dir = str(tmp_path / "benchmark_output")
    os.makedirs(output_dir, exist_ok=True)

    # Test parameters
    model_runs = 2
    baseline_runs = 3  # Just to be different

    # Run the benchmark
    results_dir = run_benchmark_loop(
        model_specs=test_model_specs,
        model_runs=model_runs,
        baseline_model_runs=baseline_runs,
        output_dir=output_dir,
        verbose=0,  # Minimal logging for tests
    )

    assert os.path.exists(results_dir)
    assert results_dir.startswith(output_dir)

    results_file = os.path.join(results_dir, RESULTS_SUMMARY_NAME)
    assert os.path.exists(results_file)

    results_df = pd.read_csv(results_file)

    assert (
        list(results_df.columns) == RESULTS_SUMMARY_COLUMNS
    ), "CSV should have expected columns"

    expected_rows = baseline_runs + model_runs
    assert len(results_df) == expected_rows

    # Verify each model spec appears in results with correct number of runs
    baseline_rows = results_df[results_df["model_spec"].str.contains("baseline")]
    other_rows = results_df[results_df["model_spec"].str.contains("other")]

    assert len(baseline_rows) == baseline_runs, f"Expected {baseline_runs} baseline rows"
    assert len(other_rows) == model_runs, f"Expected {model_runs} non-baseline rows"

    # Verify run numbering is correct
    for _, group in results_df.groupby("model_spec"):
        run_numbers = sorted(group["run"].tolist())
        expected_runs = list(range(1, len(group) + 1))
        assert (
            run_numbers == expected_runs
        ), f"Run numbers should be sequential starting from 1"

    assert results_df["rt_s"].notna().all(), "All runs should have runtime recorded"

    assert "mem_mb" in results_df.columns, "Memory column should exist"

    expected_spec_dirs = ["model_spec_baseline", "model_spec_other"]

    for spec_dir in expected_spec_dirs:
        spec_path = os.path.join(results_dir, spec_dir)
        assert os.path.exists(spec_path), f"Model spec directory {spec_path} should exist"

        spec_contents = os.listdir(spec_path)
        assert (
            len(spec_contents) > 0
        ), f"Model spec directory {spec_path} should contain results"


def test_run_benchmark_loop_validation_error(test_model_specs, tmp_path):
    """Test that benchmark fails appropriately when baseline model is missing."""
    output_dir = str(tmp_path / "validation_test")
    os.makedirs(output_dir, exist_ok=True)

    # Try to run without baseline model - should raise exception
    model_specs = test_model_specs[1:]

    with pytest.raises(Exception):  # Should raise ClickException about missing baseline
        run_benchmark_loop(
            model_specs=model_specs,
            model_runs=2,
            baseline_model_runs=2,
            output_dir=output_dir,
            verbose=0,
        )
