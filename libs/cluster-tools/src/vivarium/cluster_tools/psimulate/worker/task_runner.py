"""
==================
Jobmon Task Runner
==================

CLI entry point for Jobmon worker tasks. Loads the task's metadata JSON
and runs the appropriate work horse in-process. Invoked by every
psimulate command that submits simulations (``run`` / ``restart`` /
``expand`` / ``load_test``) as well as workflow simulation steps.

Logging is configured via ``_configure_worker_logging`` so that records
below ERROR land in the SLURM stdout file and ERROR and above land in the
SLURM stderr file (which the Jobmon GUI surfaces), with no overlap. The
stdout level is taken from the task's ``sim_verbosity``, so logging is
configured only once the task metadata has been read.

``main`` is also the single place a task failure is reported: it logs the
traceback once and returns a non-zero exit code rather than propagating,
so the SLURM stderr file holds one copy instead of one from the work horse
plus another from the interpreter's own unhandled-exception traceback. That
matters because Jobmon surfaces only the last 10k characters of stderr in
its GUI, and a duplicated traceback halves what fits.

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from vivarium.cluster_tools.psimulate import COMMANDS
from vivarium.cluster_tools.psimulate.jobs import JobParameters
from vivarium.cluster_tools.psimulate.results.writing import write_task_results
from vivarium.cluster_tools.psimulate.worker import PERF_LOG_MARKER
from vivarium.cluster_tools.psimulate.worker.load_test_work_horse import (
    work_horse as load_test_work_horse,
)
from vivarium.cluster_tools.psimulate.worker.vivarium_work_horse import work_horse

if TYPE_CHECKING:
    from loguru import Record


def _configure_worker_logging(sim_verbosity: int) -> None:
    """Route non-error records to stdout and error records to stderr.

    Every record lands in exactly one of the two SLURM files: stdout (``.o``)
    receives everything below ERROR, stderr (``.e``) receives ERROR and above.
    Keeping the two disjoint leaves ``.e`` a pure error digest, which matters
    because it is what the Jobmon GUI surfaces. Records marked with
    ``PERF_LOG_MARKER`` are dropped from stdout; they have a dedicated file.

    Loguru's default handler is removed first, so records are never duplicated
    onto the interpreter's own stderr.

    Parameters
    ----------
    sim_verbosity
        Per-simulation verbosity count from ``psimulate -s``. ``0`` floors
        stdout at INFO; ``1`` or more lowers the floor to DEBUG.
    """
    logger.remove()

    error_level_no = logger.level("ERROR").no

    def belongs_on_stdout(record: Record) -> bool:
        if record["extra"].get(PERF_LOG_MARKER, False):
            return False
        # A level floor sets the bottom of a sink's band but cannot cap its top,
        # so the ERROR-and-above band has to be rejected here instead.
        return bool(record["level"].no < error_level_no)

    # Must land on loguru handler id 1 — vivarium.engine's LoggingManager reads
    # that id's presence as "terminal logging already configured" and would
    # otherwise add its own unfiltered stdout sink.
    logger.add(
        sys.stdout,
        level="DEBUG" if sim_verbosity >= 1 else "INFO",
        filter=belongs_on_stdout,
    )
    logger.add(sys.stderr, level="ERROR")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single Jobmon worker task.")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Read the metadata before configuring logging: the stdout level depends on
    # the task's sim_verbosity. A failure here predates any sink, so loguru's
    # default handler carries it to stderr, which is where it belongs.
    metadata_path = args.metadata_dir / f"{args.task_id}.json"
    with open(metadata_path) as f:
        task_metadata = json.load(f)

    command = args.command
    job_parameters = JobParameters(**task_metadata)
    task_id = args.task_id

    # sim_verbosity is absent for commands that don't accept -s (e.g. load_test).
    _configure_worker_logging(job_parameters.extras.get("sim_verbosity", 0))

    logger.info(f"Loaded task metadata from {metadata_path}")
    logger.info(f"Running task {task_id} with command '{command}'")

    try:
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
    except Exception:
        # Report rather than re-raise; a propagated exception would be printed a
        # second time. See the module docstring.
        logger.exception(f"Task {task_id} failed running command '{command}'")
        return 1

    logger.info(f"Task {task_id} results written successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
