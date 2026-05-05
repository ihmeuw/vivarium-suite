"""Unit tests for extraction utilities."""

from pathlib import Path

import pytest

from vivarium_profiling.tools.extraction import (
    ExtractionConfig,
    FunctionCallConfiguration,
    bottleneck_config,
    extract_runtime,
    parse_function_metrics,
    phase_config,
)


@pytest.fixture
def temp_yaml_file(tmp_path):
    """Fixture for creating temporary YAML files."""

    def _create_yaml(yaml_content: str) -> Path:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)
        return yaml_path

    return _create_yaml


class TestFunctionCallConfiguration:
    """Tests for FunctionCallConfiguration dataclass."""

    def test_function_call_configuration_defaults(self):
        """Test FunctionCallConfiguration with default extraction flags."""
        pattern = FunctionCallConfiguration(
            name="test_func", filename="test.py", function_name="test_func"
        )

        assert pattern.name == "test_func"
        assert pattern.filename == "test.py"
        assert pattern.function_name == "test_func"
        assert pattern.pattern == r"test\.py:\d+\(test_func\)"
        assert pattern.line_number is None
        assert pattern.extract_cumtime is True
        assert pattern.extract_percall is False
        assert pattern.extract_ncalls is False
        assert pattern.cumtime_col == "test_func_cumtime"
        assert pattern.percall_col == "test_func_percall"
        assert pattern.ncalls_col == "test_func_ncalls"
        assert pattern.columns == ["test_func_cumtime"]

    def test_function_call_configuration_all_extracts(self):
        """Test FunctionCallConfiguration with all extraction flags enabled."""
        pattern = FunctionCallConfiguration(
            name="bottleneck",
            filename="test.py",
            function_name="bottleneck",
            extract_cumtime=True,
            extract_percall=True,
            extract_ncalls=True,
        )

        assert pattern.pattern == r"test\.py:\d+\(bottleneck\)"
        assert pattern.columns == [
            "bottleneck_cumtime",
            "bottleneck_percall",
            "bottleneck_ncalls",
        ]

    def test_function_call_configuration_custom_templates(self):
        """Test FunctionCallConfiguration with custom column templates."""
        pattern = FunctionCallConfiguration(
            name="phase",
            filename="engine.py",
            function_name="phase",
            cumtime_template="rt_{name}_s",
        )

        assert pattern.pattern == r"engine\.py:\d+\(phase\)"
        assert pattern.cumtime_col == "rt_phase_s"
        assert pattern.columns == ["rt_phase_s"]

    def test_function_call_configuration_with_line_number(self):
        """Test FunctionCallConfiguration with specific line number."""
        pattern = FunctionCallConfiguration(
            name="pipeline_call",
            filename="values/pipeline.py",
            function_name="__call__",
            line_number=66,
        )

        assert pattern.pattern == r"values/pipeline\.py:66\(__call__\)"
        assert pattern.line_number == 66


class TestExtractionConfig:
    """Tests for ExtractionConfig class."""

    def test_extraction_config_defaults(self):
        """Test ExtractionConfig with default patterns."""
        config = ExtractionConfig()

        assert len(config.patterns) > 0
        assert len(config.metric_names) > 0
        assert len(config.metric_columns) > 0
        assert len(config.results_columns) > 4  # base + metrics

    def test_extraction_config_custom_patterns(self):
        """Test ExtractionConfig with custom patterns."""
        patterns = [
            FunctionCallConfiguration("func1", "test.py", "func1"),
            bottleneck_config("func2", "test.py", "func2"),
        ]
        config = ExtractionConfig(patterns=patterns)

        assert len(config.patterns) == 2
        assert config.metric_names == ["func1", "func2"]  # Only func2 has all extracts

    def test_metric_columns(self):
        """Test metric_columns property."""
        patterns = [
            FunctionCallConfiguration(
                "a", "test.py", "a", extract_cumtime=True, extract_percall=True
            ),
            FunctionCallConfiguration("b", "test.py", "b", extract_cumtime=True),
        ]
        config = ExtractionConfig(patterns=patterns)

        assert config.metric_columns == ["a_cumtime", "a_percall", "b_cumtime"]

    def test_results_columns(self):
        """Test results_columns includes base columns."""
        patterns = [FunctionCallConfiguration("test", "test.py", "test")]
        config = ExtractionConfig(patterns=patterns)

        cols = config.results_columns
        assert cols[:4] == ["model_spec", "run", "rt_s", "mem_mb"]
        assert "test_cumtime" in cols


class TestParseMetrics:
    """Tests for metric parsing functions."""

    def test_parse_function_metrics_basic(self, sample_stats_file):
        """Test parsing basic function metrics."""
        cumtime, percall, ncalls = parse_function_metrics(
            sample_stats_file, r"results/manager\.py:\d+\(gather_results\)"
        )

        assert cumtime == 2.500
        assert percall == 0.016
        assert ncalls == 160

    def test_parse_function_metrics_recursive_calls(self, sample_stats_file):
        """Test parsing function with recursive calls (format: 50/25)."""
        cumtime, percall, ncalls = parse_function_metrics(
            sample_stats_file, r"some/custom/module\.py:\d+\(custom_function\)"
        )

        assert cumtime == 0.600
        assert percall == 0.024
        assert ncalls == 25  # Should extract the primitive call count

    def test_extract_runtime(self, sample_stats_file):
        """Test extracting total runtime from stats file."""
        runtime = extract_runtime(sample_stats_file)

        assert runtime == 10.500

    def test_parse_function_metrics_not_found(self, sample_stats_file):
        """Test parsing when function is not found."""
        cumtime, percall, ncalls = parse_function_metrics(
            sample_stats_file, r"nonexistent\.py:\d+\(missing\)"
        )

        assert cumtime is None
        assert percall is None
        assert ncalls is None

    def test_parsing_invalid_file(self, tmp_path):
        """Test parsing with invalid file path."""
        cumtime, percall, ncalls = parse_function_metrics(
            tmp_path / "nonexistent.txt", r"pattern"
        )

        assert cumtime is None
        assert percall is None
        assert ncalls is None

        runtime = extract_runtime(tmp_path / "nonexistent.txt")

        assert runtime is None


class TestExtractionConfigExtract:
    """Tests for ExtractionConfig.extract_metrics method."""

    def test_extract_metrics_default_patterns(self, sample_stats_file):
        """Test extracting metrics with default patterns."""
        config = ExtractionConfig()
        metrics = config.extract_metrics(sample_stats_file)

        # default phase metrics
        assert metrics["rt_setup_s"] == 8.000
        assert metrics["rt_initialize_simulants_s"] == 2.000
        assert metrics["rt_run_s"] == 5.000
        assert metrics["rt_finalize_s"] == 0.300
        assert metrics["rt_report_s"] == 0.100

        # default bottleneck metrics
        assert metrics["gather_results_cumtime"] == 2.500
        assert metrics["pipeline_call_cumtime"] == 1.200
        assert metrics["population_get_cumtime"] == 0.800

    def test_extract_metrics_custom_patterns(self, sample_stats_file):
        """Test extracting metrics with custom patterns."""
        patterns = [
            FunctionCallConfiguration(
                "custom_func",
                "some/custom/module.py",
                "custom_function",
                extract_cumtime=True,
                extract_ncalls=True,
                extract_percall=False,
            ),
            phase_config("initialize_simulants"),
        ]
        config = ExtractionConfig(patterns=patterns)
        metrics = config.extract_metrics(sample_stats_file)

        assert metrics["custom_func_cumtime"] == 0.600
        assert metrics["custom_func_ncalls"] == 25  # Primitive calls
        assert "custom_func_percall" not in metrics  # Not extracted

        assert metrics["rt_initialize_simulants_s"] == 2.000

    def test_extract_metrics_missing_patterns(self, sample_stats_file):
        """Test extracting metrics when patterns don't match."""
        patterns = [FunctionCallConfiguration("missing", "nonexistent.py", "missing")]
        config = ExtractionConfig(patterns=patterns)
        metrics = config.extract_metrics(sample_stats_file)

        assert metrics["missing_cumtime"] is None

    def test_extract_metrics_duplicate_func_no_line_number(self, sample_stats_file):
        """Test that we match the first occurrence when line_number is None."""
        patterns = [
            FunctionCallConfiguration(
                "duplicate_first",
                "vivarium/framework/values/pipeline.py",
                "duplicate_func",
                line_number=None,  # Should match first occurrence
                extract_cumtime=True,
                extract_percall=True,
                extract_ncalls=True,
            )
        ]
        config = ExtractionConfig(patterns=patterns)
        metrics = config.extract_metrics(sample_stats_file)

        # Should match line 66, the first occurrence
        assert metrics["duplicate_first_cumtime"] == 0.450
        assert metrics["duplicate_first_percall"] == 0.003
        assert metrics["duplicate_first_ncalls"] == 150

    def test_extract_metrics_duplicate_func_with_line_number(self, sample_stats_file):
        """Test that we can match specific occurrences by line number."""
        patterns = [
            FunctionCallConfiguration(
                "duplicate_line_66",
                "vivarium/framework/values/pipeline.py",
                "duplicate_func",
                line_number=66,
                extract_cumtime=True,
                extract_percall=True,
                extract_ncalls=True,
            ),
            FunctionCallConfiguration(
                "duplicate_line_150",
                "vivarium/framework/values/pipeline.py",
                "duplicate_func",
                line_number=150,
                extract_cumtime=True,
                extract_percall=True,
                extract_ncalls=True,
            ),
        ]
        config = ExtractionConfig(patterns=patterns)
        metrics = config.extract_metrics(sample_stats_file)

        # Line 66
        assert metrics["duplicate_line_66_cumtime"] == 0.450
        assert metrics["duplicate_line_66_percall"] == 0.003
        assert metrics["duplicate_line_66_ncalls"] == 150

        # Line 150
        assert metrics["duplicate_line_150_cumtime"] == 0.800
        assert metrics["duplicate_line_150_percall"] == 0.004
        assert metrics["duplicate_line_150_ncalls"] == 200


class TestYAMLParsing:
    """Tests for YAML configuration parsing."""

    def test_from_yaml_mixed_patterns(self, temp_yaml_file):
        """Test parsing YAML with various pattern configurations and templates."""
        yaml_content = """
patterns:
  - name: gather_results
    filename: results/manager.py
    function_name: gather_results
    extract_cumtime: true
    extract_percall: true
    extract_ncalls: true
  - name: setup
    filename: /vivarium/framework/engine.py
    function_name: setup
    extract_cumtime: false
    cumtime_template: "rt_{name}_s"
    "extract_percall": false
    "extract_ncalls": true
  - name: my_function
    filename: my/module.py
    function_name: my_function
    extract_cumtime: false
    extract_percall: true
    extract_ncalls: true
    cumtime_template: "custom_{name}_time"
    "percall_template": "{name}_custom_percall"
    "ncalls_template": "{name}_new_custom_ncalls"
    line_number: 42
"""
        yaml_path = temp_yaml_file(yaml_content)
        config = ExtractionConfig.from_yaml(yaml_path)

        assert len(config.patterns) == 3

        # Pattern with all metrics
        pattern1 = config.patterns[0]
        assert pattern1.name == "gather_results"
        assert pattern1.extract_cumtime is True
        assert pattern1.extract_percall is True
        assert pattern1.extract_ncalls is True

        # Pattern with custom template
        pattern2 = config.patterns[1]
        assert pattern2.name == "setup"
        assert pattern2.cumtime_template == "rt_{name}_s"
        assert pattern2.cumtime_col == "rt_setup_s"
        assert pattern2.extract_percall is False  # default

        # Pattern with selective metrics and custom template
        pattern3 = config.patterns[2]
        assert pattern3.name == "my_function"
        assert pattern3.extract_cumtime is False
        assert pattern3.extract_percall is True
        assert pattern3.extract_ncalls is True
        assert pattern3.cumtime_col == "custom_my_function_time"
        assert pattern3.percall_col == "my_function_custom_percall"
        assert pattern3.ncalls_col == "my_function_new_custom_ncalls"
        assert pattern3.line_number == 42

    def test_from_yaml_file_not_found(self):
        """Test error when YAML file doesn't exist."""
        with pytest.raises(FileNotFoundError, match="YAML config file not found"):
            ExtractionConfig.from_yaml("/nonexistent/file.yaml")

    def test_from_yaml_missing_patterns_key(self, temp_yaml_file):
        """Test error when YAML is missing 'patterns' key."""
        yaml_content = """
some_other_key:
  - value: 1
"""
        yaml_path = temp_yaml_file(yaml_content)
        with pytest.raises(ValueError, match="must contain a 'patterns' key"):
            ExtractionConfig.from_yaml(yaml_path)

    def test_from_yaml_missing_name_field(self, temp_yaml_file):
        """Test error when pattern is missing 'name' field."""
        yaml_content = """
patterns:
  - filename: test.py
    function_name: test
"""
        yaml_path = temp_yaml_file(yaml_content)
        with pytest.raises(ValueError, match="missing required field 'name'"):
            ExtractionConfig.from_yaml(yaml_path)

    def test_from_yaml_missing_required_fields(self, temp_yaml_file):
        """Test error when pattern is missing required fields."""
        yaml_content = """
patterns:
  - name: my_func
    filename: test.py
"""
        yaml_path = temp_yaml_file(yaml_content)
        with pytest.raises(
            ValueError, match="requires 'filename' and 'function_name' fields"
        ):
            ExtractionConfig.from_yaml(yaml_path)

    def test_from_yaml_pattern_not_dict(self, temp_yaml_file):
        """Test error when pattern is not a dictionary."""
        yaml_content = """
patterns:
  - not_a_dict
"""
        yaml_path = temp_yaml_file(yaml_content)
        with pytest.raises(ValueError, match="must be a dictionary"):
            ExtractionConfig.from_yaml(yaml_path)
