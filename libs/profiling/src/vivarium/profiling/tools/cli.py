import glob
import pstats
import subprocess
from datetime import datetime as dt
from pathlib import Path

import click
from loguru import logger
from vivarium.framework.logging import configure_logging_to_file
from vivarium.framework.utilities import handle_exceptions

from vivarium_profiling.constants import metadata, paths
from vivarium_profiling.tools import build_artifacts, configure_logging_to_terminal
from vivarium_profiling.tools.run_benchmark import run_benchmark_loop
from vivarium_profiling.tools.summarize import run_summarize_analysis


@click.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.argument(
    "model_specification",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
)
@click.option(
    "--results_directory",
    "-o",
    type=click.Path(resolve_path=True),
    default=Path("~/vivarium_results/").expanduser(),
    show_default=True,
    help=(
        "The directory to write results to. A folder will be created "
        "in this directory with the same name as the configuration file."
    ),
)
@click.option(
    "--skip_writing",
    is_flag=True,
    help=(
        "Skip writing the simulation results to the output directory; the time spent "
        "normally writing simulation results to disk will not be included in the profiling "
        "statistics."
    ),
)
@click.option(
    "--skip_processing",
    is_flag=True,
    help=(
        "Skip processing the resulting binary file to a human-readable .txt file "
        "sorted by cumulative runtime; the resulting .stats file can still be read "
        "and processed later using the pstats module."
    ),
)
@click.option(
    "--profiler",
    type=click.Choice(["cprofile", "scalene"]),
    default="cprofile",
    show_default=True,
    help=(
        "Profiling backend to use. cProfile provides the most detaile function-level"
        "runtime information, while scalene provides detailed annotation of source"
        "code that may be the source of bottlenecks."
    ),
)
@click.pass_context
def profile_sim(
    ctx: click.Context,
    model_specification: Path,
    results_directory: Path,
    skip_writing: bool,
    skip_processing: bool,
    profiler: str,
) -> None:
    """Run a simulation based on the provided MODEL_SPECIFICATION and profile the run."""
    model_specification = Path(model_specification)
    results_directory = Path(results_directory)
    results_root = results_directory / f"{dt.now().strftime('%Y_%m_%d_%H_%M_%S')}"
    configure_logging_to_file(output_directory=results_root)

    if skip_writing:
        configuration_override = {}
    else:
        output_data_root = results_root / "results"
        output_data_root.mkdir(parents=True, exist_ok=False)
        configuration_override = {
            "output_data": {"results_directory": str(output_data_root)},
        }

    script_path = Path(__file__).parent / "run_profile.py"

    config_str = repr(configuration_override)

    # Get any extra arguments passed to the profiler
    extra_args = ctx.args

    if profiler == "scalene":
        out_json_file = results_root / f"{model_specification.name}".replace("yaml", "json")
        try:
            cmd = [
                "scalene",
                "--json",
                "--outfile",
                str(out_json_file),
                "--off",
            ]
            # Add any additional profiler arguments
            cmd.extend(extra_args)
            cmd.extend(
                [
                    str(script_path),
                    str(model_specification),
                    "--config-override",
                    config_str,
                ]
            )
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Scalene profiling failed: {e}")
            raise
    elif profiler == "cprofile":
        out_stats_file = results_root / f"{model_specification.name}".replace("yaml", "stats")
        try:
            subprocess.run(
                [
                    "python",
                    str(script_path),
                    str(model_specification),
                    "--config-override",
                    config_str,
                    "--profiler",
                    "cprofile",
                    "--output",
                    str(out_stats_file),
                ],
                check=True,
            )

            if not skip_processing:
                out_txt_file = Path(str(out_stats_file) + ".txt")
                with out_txt_file.open("w") as f:
                    p = pstats.Stats(str(out_stats_file), stream=f)
                    p.sort_stats("cumulative")
                    p.print_stats()

        except subprocess.CalledProcessError as e:
            logger.error(f"cProfile profiling failed: {e}")
            raise


@click.command()
@click.option(
    "-l",
    "--location",
    default="all",
    show_default=True,
    type=click.Choice(metadata.LOCATIONS + ["all"]),
    help=(
        "Location for which to make an artifact. Note: prefer building archives on the cluster.\n"
        'If you specify location "all" you must be on a cluster node.'
    ),
)
@click.option(
    "--years",
    default=None,
    help=(
        "Years for which to make an artifact. Can be a single year or 'all'. \n"
        "If not specified, make for most recent year."
    ),
)
@click.option(
    "-o",
    "--output-dir",
    default=str(paths.ARTIFACT_ROOT),
    show_default=True,
    type=click.Path(),
    help="Specify an output directory. Directory must exist.",
)
@click.option(
    "-a", "--append", is_flag=True, help="Append to the artifact instead of overwriting."
)
@click.option("-r", "--replace-keys", multiple=True, help="Specify keys to overwrite")
@click.option("-v", "verbose", count=True, help="Configure logging verbosity.")
@click.option(
    "--pdb",
    "with_debugger",
    is_flag=True,
    help="Drop into python debugger if an error occurs.",
)
def make_artifacts(
    location: str,
    years: str | None,
    output_dir: str,
    append: bool,
    replace_keys: tuple[str, ...],
    verbose: int,
    with_debugger: bool,
) -> None:
    configure_logging_to_terminal(verbose)
    main = handle_exceptions(build_artifacts, logger, with_debugger=with_debugger)
    main(location, years, output_dir, append or replace_keys, replace_keys, verbose)


@click.command()
@click.option(
    "-m",
    "--model_specifications",
    multiple=True,
    required=True,
    help="Model specification files (supports glob patterns). Can be specified multiple times.",
)
@click.option(
    "-r",
    "--model-runs",
    type=int,
    required=True,
    help="Number of runs for non-baseline models.",
)
@click.option(
    "-b",
    "--baseline-model-runs",
    type=int,
    required=True,
    help="Number of runs for baseline model.",
)
@click.option(
    "-o",
    "--output-dir",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Directory where the timestamped results directory will be created.",
)
@click.option("-v", "verbose", count=True, help="Configure logging verbosity.")
@click.option(
    "--pdb",
    "with_debugger",
    is_flag=True,
    help="Drop into python debugger if an error occurs.",
)
def run_benchmark(
    model_specifications: tuple[str, ...],
    model_runs: int,
    baseline_model_runs: int,
    output_dir: str,
    verbose: int,
    with_debugger: bool,
) -> None:
    """Run benchmarks on Vivarium model specifications.

    This command profiles multiple model specifications and collects runtime
    and memory usage statistics. Results are saved to a timestamped CSV file.

    Example usage:
        run_benchmark -m "model_spec_baseline.yaml" -m "model_spec_*.yaml" -r 10 -b 20
    """
    # Expand model patterns
    model_specifications = _expand_model_specs(list(model_specifications))

    # Run benchmarks with error handling
    main = handle_exceptions(run_benchmark_loop, logger, with_debugger=with_debugger)
    main(model_specifications, model_runs, baseline_model_runs, output_dir, verbose)


def _expand_model_specs(model_patterns: list[str]) -> list[Path]:
    """Expand glob patterns and validate model spec files."""
    models = []
    for pattern in model_patterns:
        expanded = glob.glob(pattern)
        if expanded:
            # Filter to only include files that exist
            models.extend([Path(f) for f in expanded if Path(f).is_file()])
        else:
            # If no glob match, check if it's a direct file path
            path = Path(pattern)
            if path.is_file():
                models.append(path)

    if not models:
        raise click.ClickException(
            f"No model specification files found for patterns: {model_patterns}"
        )

    return models


@click.command()
@click.argument(
    "benchmark_results",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
)
@click.option("-v", "verbose", count=True, help="Configure logging verbosity.")
@click.option(
    "--pdb",
    "with_debugger",
    is_flag=True,
    help="Drop into python debugger if an error occurs.",
)
@click.option(
    "--nb",
    is_flag=True,
    help=(
        "Generate a Jupyter notebook for interactive analysis. "
        "If summary.csv already exists, skip summary generation."
    ),
)
def summarize(
    benchmark_results: str,
    verbose: int,
    with_debugger: bool,
    nb: bool,
) -> None:
    """Summarize benchmark results and create visualizations.

    This command reads a benchmark_results.csv file, calculates summary statistics
    (mean, median, std, min, max) for all metrics, computes percent differences
    from baseline, and generates performance analysis figures.

    The following files will be created in the same directory as BENCHMARK_RESULTS:
    - summary.csv: Aggregated statistics for all model specifications
    - performance_analysis.png: Runtime and memory usage charts
    - runtime_analysis_*.png: Individual phase runtime charts
    - bottleneck_runtime_analysis_*.png: Bottleneck cumtime charts
    - bottleneck_fraction_*.png: Bottleneck fraction scaling charts

    If --nb is specified, also creates:
    - analysis.ipynb: Interactive Jupyter notebook with all plots

    Example usage:
        summarize results/profile_2026_01_07/benchmark_results.csv
    """
    configure_logging_to_terminal(verbose)
    benchmark_results_path = Path(benchmark_results)
    main = handle_exceptions(run_summarize_analysis, logger, with_debugger=with_debugger)
    main(benchmark_results_path, nb=nb)
