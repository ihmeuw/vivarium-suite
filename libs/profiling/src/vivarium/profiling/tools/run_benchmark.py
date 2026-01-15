"""Benchmarking functionality for profiling Vivarium models."""

import glob
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pandas as pd
from loguru import logger

from vivarium_profiling.tools import configure_logging_to_terminal
from vivarium_profiling.tools.extraction import (
    ExtractionConfig,
    extract_runtime,
    get_peak_memory,
)
from vivarium_profiling.tools.summarize import run_summarize_analysis

RESULTS_SUMMARY_NAME = "benchmark_results.csv"


def validate_baseline_model(models: list[Path]) -> None:
    """Validate that one of the model specs is the baseline."""
    baseline_found = "model_spec_baseline.yaml" in [model.name for model in models]
    if not baseline_found:
        raise click.ClickException(
            "Error: One of the model specs must be 'model_spec_baseline.yaml'."
        )


def create_results_directory(output_dir: str = ".") -> str:
    """Create a timestamped results directory."""
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    results_dir = Path(output_dir) / f"profile_{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    return str(results_dir)


def initialize_results_file(results_dir: str, config: ExtractionConfig) -> str:
    """Initialize the CSV results file with headers."""
    results_file = Path(results_dir) / RESULTS_SUMMARY_NAME
    df = pd.DataFrame(columns=config.results_columns)
    df.to_csv(results_file, index=False)
    return str(results_file)


def run_memory_profiler(spec: str, output_dir: str) -> None:
    """Run memory profiler on a model specification."""
    cmd = ["mprof", "run", "-CM", "profile_sim", spec, "-o", output_dir]
    result = subprocess.run(cmd, check=True, capture_output=False)
    if result.returncode != 0:
        raise click.ClickException(f"Memory profiler failed for {spec}")


def move_mprof_files(target_dir: str) -> None:
    """Move memory profiler data files to the target directory."""
    mprof_files = glob.glob("mprofile_*.dat")
    for file in mprof_files:
        destination = Path(target_dir) / file
        shutil.move(file, destination)


def get_latest_results_dir(parent_dir: str) -> str | None:
    """Get the most recent results directory."""
    try:
        parent_path = Path(parent_dir)
        dirs = [str(d) for d in parent_path.glob("*/") if d.is_dir()]
        if dirs:
            return sorted(dirs)[-1]
    except Exception as e:
        logger.warning(f"Could not find latest results directory: {e}")
    return None


def run_single_benchmark(
    spec: str,
    run_number: int,
    total_runs: int,
    spec_results_dir: str,
    config: ExtractionConfig,
) -> dict[str, Any]:
    """Run a single benchmark iteration.

    Parameters
    ----------
    spec
        Path to the model specification file.
    run_number
        Current run number (1-indexed).
    total_runs
        Total number of runs for this spec.
    spec_results_dir
        Directory to store results for this spec.
    config
        Extraction configuration. Defaults to DEFAULT_CONFIG.

    Returns
    -------
        Dictionary of extracted metrics.

    """

    logger.info(f"Run {run_number}/{total_runs} for {spec}...")

    # Run memory profiler
    run_memory_profiler(spec, spec_results_dir)

    # Get the current results directory
    current_results_dir = get_latest_results_dir(spec_results_dir)
    if not current_results_dir:
        raise click.ClickException(f"Could not find results directory for {spec}")

    # Get peak memory
    mem_mb = get_peak_memory()

    # Move memory profiler files
    move_mprof_files(current_results_dir)

    # Get runtime and function metrics
    model_spec_name = Path(spec).stem
    stats_file = Path(current_results_dir) / f"{model_spec_name}.stats"
    stats_file_txt = f"{stats_file}.txt"

    rt_s = extract_runtime(stats_file_txt)

    # Extract all configured metrics (bottlenecks + phases)
    extracted_metrics = config.extract_metrics(stats_file_txt)

    results = {
        "model_spec": spec,
        "run": run_number,
        "rt_s": rt_s,
        "mem_mb": mem_mb,
        **extracted_metrics,
    }

    logger.info(f"Finished run {run_number}/{total_runs} for {spec}")
    logger.info(f"    Runtime: {rt_s}s, Peak Memory: {mem_mb}MB")

    return results


def run_benchmark_loop(
    model_specifications: list[Path],
    model_runs: int,
    baseline_model_runs: int,
    output_dir: str = ".",
    verbose: int = 0,
) -> str:
    """Main function to run benchmarks on model specifications.

    Parameters
    ----------
    model_specifications
        List of model specification file paths.
    model_runs
        Number of runs for non-baseline models.
    baseline_model_runs
        Number of runs for the baseline model.
    output_dir
        Directory to save results.
    verbose
        Verbosity level for logging.
    config
        Extraction configuration. Defaults to DEFAULT_CONFIG.

    Returns
    -------
        Path to the results directory.

    """
    config = ExtractionConfig()

    configure_logging_to_terminal(verbose)

    # Validate inputs
    validate_baseline_model(model_specifications)

    # Create results directory and initialize results file
    results_dir = create_results_directory(output_dir)
    results_file = initialize_results_file(results_dir, config)

    logger.info("Running benchmarks:")
    logger.info(f"  Model Specs: {model_specifications}")
    logger.info(f"  Runs: {model_runs} ({baseline_model_runs} for baseline)")
    logger.info(f"  Results Directory: {results_dir}")

    # Run benchmarks for each specification
    for spec in model_specifications:
        logger.info(f"Running {spec}...")

        model_spec_name = spec.stem
        spec_specific_results_dir = Path(results_dir) / model_spec_name
        spec_specific_results_dir.mkdir(parents=True, exist_ok=True)

        # Determine number of runs
        if spec.name == "model_spec_baseline.yaml":
            num_runs = baseline_model_runs
        else:
            num_runs = model_runs

        # Run benchmarks
        for run in range(1, num_runs + 1):
            try:
                results = run_single_benchmark(
                    str(spec),
                    run,
                    num_runs,
                    str(spec_specific_results_dir),
                    config,
                )
                result_df = pd.DataFrame([results])
                result_df.to_csv(results_file, mode="a", header=False, index=False)
            except Exception as e:
                logger.error(f"Failed to run benchmark {run} for {spec}: {e}")
                raise

    logger.info(f"Benchmark complete! Results saved to {results_file}")

    # Run summarization and create figures
    logger.info("Running summarization and creating visualizations...")
    try:
        run_summarize_analysis(Path(results_file), config)
    except Exception as e:
        logger.error(f"Failed to run summarization: {e}")
        # Don't raise - benchmark data is still valid even if summarization fails

    return results_dir
