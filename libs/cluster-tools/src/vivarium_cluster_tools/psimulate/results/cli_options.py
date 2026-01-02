"""
==============================
Results Processing CLI Options
==============================

Command line options for configuring results handling in psimulate runs.

"""

import click

from vivarium_cluster_tools.cli_tools import MINUTES_OR_NONE

DEFAULT_BATCH_SIZE = 200
DEFAULT_OUTPUT_FILE_SIZE_MB = 500.0

with_batch_size = click.option(
    "--batch-size",
    type=int,
    default=DEFAULT_BATCH_SIZE,
    show_default=True,
    help="Number of simulation jobs to accumulate before writing results to disk.",
)
with_output_file_size = click.option(
    "--output-file-size",
    "output_file_size",
    type=float,
    default=DEFAULT_OUTPUT_FILE_SIZE_MB,
    show_default=True,
    help="Maximum file size in MB per result file (within each type of result). When exceeded, a new file is started.",
    callback=lambda ctx, param, value: int(value * 1024 * 1024),  # Convert MB to bytes
)
with_no_batch = click.option(
    "--no-batch",
    is_flag=True,
    help="Write results immediately as they come in (batch_size=0). Files are still split by size per --output_file_size.",
)
with_no_cleanup = click.option(
    "--no-cleanup",
    is_flag=True,
    hidden=True,
    help="Hidden developer option, if flagged, don't automatically "
    "cleanup results directory on failure.",
)
backup_freq = click.option(
    "--backup-freq",
    type=MINUTES_OR_NONE,
    default="30",
    show_default=True,
    help="Interval in minutes between saving backups. Set to 'None' or 'none' to disable backups.",
)
