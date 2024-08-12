"""
==============================
Results Processing CLI Options
==============================

Command line options for configuring results handling in psimulate runs.

"""
import click

from vivarium_cluster_tools.cli_tools import MINUTES_OR_NONE

with_no_batch = click.option(
    "--no-batch", is_flag=True, help="Don't batch results, write them as they come in."
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
