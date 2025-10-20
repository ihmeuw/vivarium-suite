"""Integration tests for run_benchmark module."""

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from vivarium_profiling.tools.run_benchmark import (
    run_benchmark_loop,
    RESULTS_SUMMARY_NAME,
    RESULTS_SUMMARY_COLUMNS,
)


@pytest.mark.slow
def test_run_benchmark_loop_integration(test_model_specs, tmp_path):
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
    baseline_runs = 2

    # Get the model specs - baseline and 2 other models
    model_specs = [
        test_model_specs["baseline"],
        test_model_specs["disease"],
        test_model_specs["risk"],
    ]

    # Run the benchmark
    results_dir = run_benchmark_loop(
        model_specs=model_specs,
        model_runs=model_runs,
        baseline_model_runs=baseline_runs,
        output_dir=output_dir,
        verbose=0,  # Minimal logging for tests
    )

    # Verify results directory was created
    assert os.path.exists(results_dir)
    assert results_dir.startswith(output_dir)

    # Verify summary CSV file exists
    results_file = os.path.join(results_dir, RESULTS_SUMMARY_NAME)
    assert os.path.exists(results_file), f"Results file {results_file} should exist"

    # Load and verify the results CSV
    results_df = pd.read_csv(results_file)

    # Verify CSV structure
    assert (
        list(results_df.columns) == RESULTS_SUMMARY_COLUMNS
    ), "CSV should have expected columns"

    # Verify expected number of rows
    # baseline: 2 runs, disease: 2 runs, risk: 2 runs = 6 total rows
    expected_rows = baseline_runs + model_runs + model_runs
    assert (
        len(results_df) == expected_rows
    ), f"Expected {expected_rows} rows, got {len(results_df)}"

    # Verify each model spec appears in results with correct number of runs
    baseline_rows = results_df[results_df["model_spec"].str.contains("baseline")]
    disease_rows = results_df[results_df["model_spec"].str.contains("disease")]
    risk_rows = results_df[results_df["model_spec"].str.contains("risk")]

    assert len(baseline_rows) == baseline_runs, f"Expected {baseline_runs} baseline rows"
    assert len(disease_rows) == model_runs, f"Expected {model_runs} disease rows"
    assert len(risk_rows) == model_runs, f"Expected {model_runs} risk rows"

    # Verify run numbering is correct
    for _, group in results_df.groupby("model_spec"):
        run_numbers = sorted(group["run"].tolist())
        expected_runs = list(range(1, len(group) + 1))
        assert (
            run_numbers == expected_runs
        ), f"Run numbers should be sequential starting from 1"

    # Verify key columns have non-null values where expected
    # Runtime should be recorded for all runs
    assert results_df["rt_s"].notna().all(), "All runs should have runtime recorded"

    # Memory usage should be recorded for all runs (if memory profiler works)
    # Note: This might be None in some test environments, so we'll just check the column exists
    assert "mem_mb" in results_df.columns, "Memory column should exist"

    # Verify individual model spec result directories exist
    expected_spec_dirs = ["model_spec_baseline", "model_spec_disease", "model_spec_risk"]
    for spec_dir in expected_spec_dirs:
        spec_path = os.path.join(results_dir, spec_dir)
        assert os.path.exists(spec_path), f"Model spec directory {spec_path} should exist"

        # Verify that each spec directory contains some results
        spec_contents = os.listdir(spec_path)
        assert (
            len(spec_contents) > 0
        ), f"Model spec directory {spec_path} should contain results"


def test_run_benchmark_loop_validation_error(test_model_specs, tmp_path):
    """Test that benchmark fails appropriately when baseline model is missing."""
    output_dir = str(tmp_path / "validation_test")
    os.makedirs(output_dir, exist_ok=True)

    # Try to run without baseline model - should raise exception
    model_specs = [test_model_specs["disease"], test_model_specs["risk"]]

    with pytest.raises(Exception):  # Should raise ClickException about missing baseline
        run_benchmark_loop(
            model_specs=model_specs,
            model_runs=2,
            baseline_model_runs=2,
            output_dir=output_dir,
            verbose=0,
        )
