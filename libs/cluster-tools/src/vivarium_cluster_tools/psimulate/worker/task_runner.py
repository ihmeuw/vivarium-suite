"""
========================
Jobmon Task Runner
========================

CLI entry point for individual Jobmon tasks. Each task loads its metadata
JSON file, runs the appropriate work horse, and writes results directly
to the results directory (one parquet per metric per task).

Usage::

    python -m vivarium_cluster_tools.psimulate.worker.task_runner \
        --metadata-dir /path/to/metadata \
        --task-id <task_id> \
        --results-dir /path/to/results \

"""

import argparse
import json
import os
import sys
from pathlib import Path

from loguru import logger

from vivarium_cluster_tools.psimulate import COMMANDS
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.results.writing import write_task_results
from vivarium_cluster_tools.psimulate.worker.load_test_work_horse import (
    work_horse as load_test_work_horse,
)
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import work_horse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single Jobmon task for psimulate.")
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        required=True,
        help="Directory containing task metadata JSON files.",
    )
    parser.add_argument(
        "--task-id",
        type=str,
        required=True,
        help="The deterministic task ID.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory to write results to.",
    )
    parser.add_argument(
        "--command",
        type=str,
        required=True,
        help="The psimulate command (e.g. run, restart, expand, load_test).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logger.add(sys.stdout, level="INFO")
    logger.add(sys.stderr, level="WARNING")

    metadata_path = args.metadata_dir / f"{args.task_id}.json"
    logger.info(f"Loading task metadata from {metadata_path}")
    with open(metadata_path) as f:
        task_metadata = json.load(f)

    command = args.command
    job_parameters = JobParameters(**task_metadata)
    task_id = args.task_id

    logger.info(f"Running task {task_id} with command '{command}'")

    if command in (COMMANDS.run, COMMANDS.restart, COMMANDS.expand):
        results_dict = work_horse(job_parameters)
    elif command == COMMANDS.load_test:
        results_df = load_test_work_horse(job_parameters)
        results_dict = {"load_test": results_df}
    else:
        raise ValueError(f"Unknown command: {command}")

    logger.info(f"Task {task_id} completed, writing results.")

    write_task_results(
        results_dir=args.results_dir,
        job_parameters=job_parameters,
        results_dict=results_dict,
    )
    logger.info(f"Task {task_id} results written successfully.")


if __name__ == "__main__":
    main()
