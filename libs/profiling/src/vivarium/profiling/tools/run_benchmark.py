"""Benchmarking functionality for profiling Vivarium models."""

import glob
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import click
from loguru import logger

from vivarium_profiling.tools import configure_logging_to_terminal

RESULTS_SUMMARY_NAME = "benchmark_results.csv"
RESULTS_SUMMARY_COLUMNS = [
    "model_spec",
    "run",
    "rt_s",
    "mem_mb",
    "gather_results_cumtime",
    "gather_results_percall",
    "gather_results_ncalls",
    "pipeline_call_cumtime",
    "pipeline_call_percall",
    "pipeline_call_ncalls",
    "population_get_cumtime",
    "population_get_percall",
    "population_get_ncalls",
]


def expand_model_specs(model_patterns: List[str]) -> List[str]:
    """Expand glob patterns and validate model spec files."""
    models = []
    for pattern in model_patterns:
        expanded = glob.glob(pattern)
        if expanded:
            # Filter to only include files that exist
            models.extend([f for f in expanded if os.path.isfile(f)])
        else:
            # If no glob match, check if it's a direct file path
            if os.path.isfile(pattern):
                models.append(pattern)

    if not models:
        raise click.ClickException(
            f"No model specification files found for patterns: {model_patterns}"
        )

    return models


def validate_baseline_model(models: List[str]) -> None:
    """Validate that one of the model specs is the baseline."""
    baseline_found = any("model_spec_baseline.yaml" in model for model in models)
    if not baseline_found:
        raise click.ClickException(
            "Error: One of the model specs must be 'model_spec_baseline.yaml'."
        )


def create_results_directory(output_dir: str = ".") -> str:
    """Create a timestamped results directory."""
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    results_dir = os.path.join(output_dir, f"profile_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)
    return results_dir


def initialize_results_file(results_dir: str) -> str:
    """Initialize the CSV results file with headers."""
    results_file = os.path.join(results_dir, RESULTS_SUMMARY_NAME)
    df = pd.DataFrame(columns=RESULTS_SUMMARY_COLUMNS)
    df.to_csv(results_file, index=False)
    return results_file


def run_memory_profiler(spec: str, output_dir: str) -> None:
    """Run memory profiler on a model specification."""
    cmd = ["mprof", "run", "-CM", "simulate", "profile", spec, "-o", output_dir]
    result = subprocess.run(cmd, check=True, capture_output=False)
    if result.returncode != 0:
        raise click.ClickException(f"Memory profiler failed for {spec}")


def get_peak_memory() -> Optional[float]:
    """Get peak memory usage from memory profiler."""
    try:
        result = subprocess.run(["mprof", "peak"], capture_output=True, text=True, check=True)
        # Extract the first decimal number from the output
        match = re.search(r"(\d+\.\d+)", result.stdout)
        if match:
            return float(match.group(1))
    except (subprocess.CalledProcessError, ValueError):
        logger.warning("Could not extract peak memory usage")
    return None


def move_mprof_files(target_dir: str) -> None:
    """Move memory profiler data files to the target directory."""
    mprof_files = glob.glob("mprofile_*.dat")
    for file in mprof_files:
        destination = os.path.join(target_dir, file)
        os.rename(file, destination)


def get_latest_results_dir(parent_dir: str) -> Optional[str]:
    """Get the most recent results directory."""
    try:
        dirs = [d for d in glob.glob(os.path.join(parent_dir, "*/")) if os.path.isdir(d)]
        if dirs:
            return sorted(dirs)[-1]
    except Exception as e:
        logger.warning(f"Could not find latest results directory: {e}")
    return None


def extract_runtime(stats_file_txt: str) -> Optional[float]:
    """Extract runtime from the stats file."""
    try:
        with open(stats_file_txt, "r") as f:
            content = f.read()

        # Look for pattern like "12345 function calls (12340 primitive calls) in 1.234 seconds"
        match = re.search(r"function calls.*in (\d+\.\d+) seconds", content)
        if match:
            return float(match.group(1))
    except (FileNotFoundError, ValueError, OSError) as e:
        logger.warning(f"Could not extract runtime from {stats_file_txt}: {e}")
    return None


def parse_function_metrics(
    stats_file_txt: str, pattern: str
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    """Parse cumtime, percall, and ncalls for a specific function pattern."""
    try:
        with open(stats_file_txt, "r") as f:
            content = f.read()

        # Find the line matching the pattern
        lines = content.split("\n")
        matching_line = None
        for line in lines:
            if re.search(pattern, line):
                matching_line = line
                break

        if not matching_line:
            return None, None, None

        # Parse the line - typical format: "ncalls  tottime  percall  cumtime  percall filename:lineno(function)"
        parts = matching_line.split()
        if len(parts) >= 5:
            ncalls_str = parts[0]
            cumtime = float(parts[3])
            percall = float(parts[4])

            # Handle ncalls which might be in format "123/456" (recursive calls)
            if "/" in ncalls_str:
                ncalls = int(ncalls_str.split("/")[1])
            else:
                ncalls = int(ncalls_str)

            return cumtime, percall, ncalls

    except (FileNotFoundError, ValueError, IndexError, OSError) as e:
        logger.warning(
            f"Could not parse function metrics from {stats_file_txt} with pattern {pattern}: {e}"
        )

    return None, None, None


def run_single_benchmark(
    spec: str, run_number: int, total_runs: int, spec_results_dir: str, model_spec_name: str
) -> Dict[str, Any]:
    """Run a single benchmark iteration."""
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
    stats_file = os.path.join(current_results_dir, f"{model_spec_name}.stats")
    stats_file_txt = f"{stats_file}.txt"

    rt_s = extract_runtime(stats_file_txt)

    # Extract specific function performance metrics
    (
        gather_results_cumtime,
        gather_results_percall,
        gather_results_ncalls,
    ) = parse_function_metrics(stats_file_txt, r"results/manager\.py:\d+\(gather_results\)")

    (
        pipeline_call_cumtime,
        pipeline_call_percall,
        pipeline_call_ncalls,
    ) = parse_function_metrics(stats_file_txt, r"values/pipeline\.py:\d+\(__call__\)")

    (
        population_get_cumtime,
        population_get_percall,
        population_get_ncalls,
    ) = parse_function_metrics(stats_file_txt, r"population/population_view\.py:\d+\(get\)")

    results = {
        "model_spec": spec,
        "run": run_number,
        "rt_s": rt_s,
        "mem_mb": mem_mb,
        "gather_results_cumtime": gather_results_cumtime,
        "gather_results_percall": gather_results_percall,
        "gather_results_ncalls": gather_results_ncalls,
        "pipeline_call_cumtime": pipeline_call_cumtime,
        "pipeline_call_percall": pipeline_call_percall,
        "pipeline_call_ncalls": pipeline_call_ncalls,
        "population_get_cumtime": population_get_cumtime,
        "population_get_percall": population_get_percall,
        "population_get_ncalls": population_get_ncalls,
    }

    logger.info(f"Finished run {run_number}/{total_runs} for {spec}")
    logger.info(f"    Runtime: {rt_s}s, Peak Memory: {mem_mb}MB")

    return results


def run_benchmarks(
    model_specs: List[str],
    model_runs: int,
    baseline_model_runs: int,
    output_dir: str = ".",
    verbose: int = 0,
) -> str:
    """Main function to run benchmarks on model specifications."""
    configure_logging_to_terminal(verbose)

    # Validate inputs
    validate_baseline_model(model_specs)

    # Create results directory and initialize results file
    results_dir = create_results_directory(output_dir)
    results_file = initialize_results_file(results_dir)

    logger.info("Running benchmarks:")
    logger.info(f"  Model Specs: {model_specs}")
    logger.info(f"  Runs: {model_runs} ({baseline_model_runs} for baseline)")
    logger.info(f"  Results Directory: {results_dir}")

    # Run benchmarks for each specification
    for spec in model_specs:
        logger.info(f"Running {spec}...")

        model_spec_name = Path(spec).stem
        spec_specific_results_dir = os.path.join(results_dir, model_spec_name)
        os.makedirs(spec_specific_results_dir, exist_ok=True)

        # Determine number of runs
        if "model_spec_baseline.yaml" in spec:
            num_runs = baseline_model_runs
        else:
            num_runs = model_runs

        # Run benchmarks
        for run in range(1, num_runs + 1):
            try:
                results = run_single_benchmark(
                    spec, run, num_runs, spec_specific_results_dir, model_spec_name
                )
                result_df = pd.DataFrame([results])
                result_df.to_csv(results_file, mode="a", header=False, index=False)
            except Exception as e:
                logger.error(f"Failed to run benchmark {run} for {spec}: {e}")
                raise

    logger.info(f"Benchmark complete! Results saved to {results_file}")
    return results_dir
