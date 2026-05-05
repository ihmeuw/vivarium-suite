"""Unit tests for summarization utilities."""

import pandas as pd
import pytest

from vivarium_profiling.tools.extraction import (
    ExtractionConfig,
    FunctionCallConfiguration,
)
from vivarium_profiling.tools.summarize import summarize


@pytest.fixture
def sample_benchmark_data():
    """Create sample benchmark data for testing."""
    data = {
        "model_spec": [
            "path/to/model_spec_baseline.yaml",
            "path/to/model_spec_baseline.yaml",
            "path/to/model_spec_baseline.yaml",
            "path/to/model_spec_pop_2x.yaml",
            "path/to/model_spec_pop_2x.yaml",
            "path/to/model_spec_pop_2x.yaml",
        ],
        "run": [0, 1, 2, 0, 1, 2],
        "rt_s": [10.0, 12.0, 11.0, 20.0, 22.0, 21.0],
        "mem_mb": [100.0, 105.0, 102.0, 200.0, 210.0, 205.0],
        "rt_setup_s": [1.0, 1.2, 1.1, 2.0, 2.2, 2.1],
        "rt_initialize_simulants_s": [2.0, 2.4, 2.2, 4.0, 4.4, 4.2],
        "rt_run_s": [5.0, 6.0, 5.5, 10.0, 11.0, 10.5],
        "rt_finalize_s": [1.5, 1.8, 1.65, 3.0, 3.3, 3.15],
        "rt_report_s": [0.5, 0.6, 0.55, 1.0, 1.1, 1.05],
        "gather_results_cumtime": [2.0, 2.4, 2.2, 4.0, 4.4, 4.2],
        "gather_results_percall": [0.01, 0.012, 0.011, 0.02, 0.022, 0.021],
        "gather_results_ncalls": [200, 200, 200, 200, 200, 200],
        "pipeline_call_cumtime": [1.5, 1.8, 1.65, 3.0, 3.3, 3.15],
        "pipeline_call_percall": [0.005, 0.006, 0.0055, 0.01, 0.011, 0.0105],
        "pipeline_call_ncalls": [300, 300, 300, 300, 300, 300],
        "population_get_cumtime": [1.0, 1.2, 1.1, 2.0, 2.2, 2.1],
        "population_get_percall": [0.001, 0.0012, 0.0011, 0.002, 0.0022, 0.0021],
        "population_get_ncalls": [1000, 1000, 1000, 1000, 1000, 1000],
    }
    return pd.DataFrame(data)


@pytest.fixture
def minimal_extraction_config():
    """Create a minimal extraction config for testing."""
    patterns = [
        FunctionCallConfiguration(
            "gather_results",
            "results/manager.py",
            "gather_results",
            extract_cumtime=True,
            extract_percall=True,
            extract_ncalls=True,
        ),
        FunctionCallConfiguration(
            "setup",
            "/vivarium/framework/engine.py",
            "setup",
            extract_cumtime=True,
            cumtime_template="rt_{name}_s",
        ),
        FunctionCallConfiguration(
            "run",
            "/vivarium/framework/engine.py",
            "run",
            extract_cumtime=True,
            cumtime_template="rt_{name}_s",
        ),
    ]
    return ExtractionConfig(patterns=patterns)


class TestSummarize:
    """Tests for the summarize function."""

    @pytest.fixture
    def sample_summary(self, sample_benchmark_data, tmp_path):
        """Create a summary from sample benchmark data."""
        return summarize(sample_benchmark_data, tmp_path)

    def test_summarize_basic_aggregation(self, sample_summary):
        """Test that summarize correctly aggregates data."""
        # Check that we have one row per model
        assert len(sample_summary) == 2
        assert "model_spec" in sample_summary.columns
        assert "model" in sample_summary.columns

        # Check baseline is first
        assert sample_summary.iloc[0]["model"] == "baseline"
        assert sample_summary.iloc[1]["model"] == "pop_2x"

    def test_summarize_statistics(self, sample_summary):
        """Test that summarize calculates correct statistics."""
        baseline = sample_summary[sample_summary["model"] == "baseline"].iloc[0]

        # Runtime
        assert baseline["rt_s_mean"] == pytest.approx(11.0)
        assert baseline["rt_s_median"] == pytest.approx(11.0)
        assert baseline["rt_s_std"] == pytest.approx(1.0)
        assert baseline["rt_s_min"] == pytest.approx(10.0)
        assert baseline["rt_s_max"] == pytest.approx(12.0)

        # Memory
        assert baseline["mem_mb_mean"] == pytest.approx(102.333, rel=0.01)
        assert baseline["mem_mb_median"] == pytest.approx(102.0)

    def test_summarize_derived_columns(self, sample_summary):
        """Test that summarize calculates derived columns like rt_non_run_s."""
        baseline = sample_summary[sample_summary["model"] == "baseline"].iloc[0]

        # rt_non_run_s should be rt_s - rt_run_s
        expected_non_run = 11.0 - 5.5  # median values
        assert baseline["rt_non_run_s_median"] == pytest.approx(expected_non_run)

    def test_summarize_bottleneck_fractions(self, sample_summary):
        """Test that summarize calculates bottleneck fractions correctly."""
        baseline = sample_summary[sample_summary["model"] == "baseline"].iloc[0]

        assert "gather_results_fraction_mean" in sample_summary.columns
        assert "pipeline_call_fraction_mean" in sample_summary.columns
        assert "population_get_fraction_mean" in sample_summary.columns

        # gather_results_fraction should be gather_results_cumtime / rt_run_s
        # Using median values: 2.2 / 5.5 = 0.4
        assert baseline["gather_results_fraction_median"] == pytest.approx(0.4)

    def test_summarize_percent_differences(self, sample_summary):
        """Test that summarize calculates percent differences from baseline."""
        baseline = sample_summary[sample_summary["model"] == "baseline"].iloc[0]
        pop_2x = sample_summary[sample_summary["model"] == "pop_2x"].iloc[0]

        # Baseline should have 0% difference
        assert baseline["rt_s_pdiff"] == pytest.approx(0.0)
        assert baseline["mem_mb_pdiff"] == pytest.approx(0.0)

        # pop_2x should have positive percent differences
        # rt_s: baseline median = 11.0, pop_2x median = 21.0
        # pdiff = (21.0 - 11.0) / 11.0 * 100 = 90.909...
        assert pop_2x["rt_s_pdiff"] == pytest.approx(90.909, rel=0.01)

        # mem_mb: baseline median = 102.0, pop_2x median = 205.0
        # pdiff = (205.0 - 102.0) / 102.0 * 100 = 100.98...
        assert pop_2x["mem_mb_pdiff"] == pytest.approx(100.98, rel=0.01)

    def test_summarize_all_median_columns_get_pdiff(self, sample_summary):
        """Test that all median columns get corresponding pdiff columns."""
        median_cols = [col for col in sample_summary.columns if col.endswith("_median")]
        pdiff_cols = [col for col in sample_summary.columns if col.endswith("_pdiff")]

        # Each median column should have a corresponding pdiff column
        assert len(pdiff_cols) == len(median_cols)

        for median_col in median_cols:
            expected_pdiff = median_col.replace("_median", "_pdiff")
            assert expected_pdiff in sample_summary.columns

    def test_summarize_custom_config(
        self, sample_benchmark_data, tmp_path, minimal_extraction_config
    ):
        """Test summarize with custom extraction config."""
        summary = summarize(sample_benchmark_data, tmp_path, minimal_extraction_config)

        assert len(summary) == 2
        assert "model" in summary.columns
        assert "gather_results_fraction_median" in summary.columns

    def test_summarize_saves_csv(self, sample_summary, tmp_path):
        """Test that summarize saves summary.csv file."""
        summary_file = tmp_path / "summary.csv"
        assert summary_file.exists()
        loaded = pd.read_csv(summary_file)
        assert len(loaded) == len(sample_summary)
        assert list(loaded.columns) == list(sample_summary.columns)

    def test_summarize_model_name_extraction(self, sample_summary):
        """Test that model names are correctly extracted from paths."""
        models = sample_summary["model"].tolist()
        assert "baseline" in models
        assert "pop_2x" in models

    def test_summarize_with_missing_columns(self, sample_benchmark_data, tmp_path):
        """Test summarize when some expected columns are missing."""
        # Remove a column that might not always be present
        data = sample_benchmark_data.drop(columns=["population_get_ncalls"])

        summary = summarize(data, tmp_path)

        assert len(summary) == 2
        assert "population_get_cumtime_median" in summary.columns
        assert "population_get_ncalls_mean" not in summary.columns

    def test_summarize_single_run_per_model(self, tmp_path):
        """Test summarize with only one run per model (edge case)."""
        data = {
            "model_spec": [
                "path/to/model_spec_baseline.yaml",
                "path/to/model_spec_pop_2x.yaml",
            ],
            "run": [0, 0],
            "rt_s": [10.0, 20.0],
            "mem_mb": [100.0, 200.0],
            "rt_setup_s": [1.0, 2.0],
            "rt_run_s": [5.0, 10.0],
            "gather_results_cumtime": [2.0, 4.0],
        }
        df = pd.DataFrame(data)

        summary = summarize(df, tmp_path)

        # With single values, mean/median/min/max should all be the same
        baseline = summary[summary["model"] == "baseline"].iloc[0]
        assert baseline["rt_s_mean"] == 10.0
        assert baseline["rt_s_median"] == 10.0
        assert baseline["rt_s_min"] == 10.0
        assert baseline["rt_s_max"] == 10.0
        # std should be 0.0 for single value
        assert baseline["rt_s_std"] == 0.0

    def test_summarize_preserves_column_order(self, sample_summary):
        """Test that model_spec and model are first columns."""
        cols = list(sample_summary.columns)
        assert cols[0] == "model_spec"
        assert cols[1] == "model"

    def test_summarize_baseline_ordering(self, tmp_path):
        """Test that baseline is always first row even if not alphabetically first."""
        data = {
            "model_spec": [
                "path/to/model_spec_zzz.yaml",
                "path/to/model_spec_zzz.yaml",
                "path/to/model_spec_baseline.yaml",
                "path/to/model_spec_baseline.yaml",
            ],
            "run": [0, 1, 0, 1],
            "rt_s": [20.0, 22.0, 10.0, 12.0],
            "mem_mb": [200.0, 210.0, 100.0, 105.0],
            "rt_run_s": [10.0, 11.0, 5.0, 6.0],
            "gather_results_cumtime": [4.0, 4.4, 2.0, 2.4],
        }
        df = pd.DataFrame(data)

        summary = summarize(df, tmp_path)

        # Baseline should be first
        assert summary.iloc[0]["model"] == "baseline"
        assert summary.iloc[1]["model"] == "zzz"
