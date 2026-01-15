"""Generate Jupyter notebooks for interactive benchmark analysis."""

from pathlib import Path

import nbformat as nbf
from loguru import logger

from vivarium_profiling.templates import ANALYSIS_NOTEBOOK_TEMPLATE

NOTEBOOK_NAME = "analysis.ipynb"


def create_analysis_notebook(
    benchmark_results_path: Path,
    summary_path: Path,
    output_path: Path,
) -> None:
    """Create a Jupyter notebook for interactive benchmark analysis.

    Loads a template notebook and substitutes file paths.

    Parameters
    ----------
    benchmark_results_path
        Path to benchmark_results.csv file.
    summary_path
        Path to summary.csv file.
    output_path
        Path where the notebook should be saved (e.g., analysis.ipynb).
    config
        Extraction configuration (currently unused, kept for API consistency).

    """
    # Define substitutions
    substitutions = {
        "{{BENCHMARK_RESULTS_PATH}}": str(benchmark_results_path),
        "{{SUMMARY_PATH}}": str(summary_path),
    }

    # Load template
    with open(ANALYSIS_NOTEBOOK_TEMPLATE) as f:
        nb = nbf.read(f, as_version=4)

    # Apply substitutions to all code cells
    for cell in nb.cells:
        if cell.cell_type == "code":
            source = cell.source
            for placeholder, value in substitutions.items():
                source = source.replace(placeholder, value)
            cell.source = source

    # Save the notebook
    with open(output_path, "w") as f:
        nbf.write(nb, f)

    logger.info(f"Created analysis notebook: {output_path}")
