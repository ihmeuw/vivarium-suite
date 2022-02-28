"""
==============================
Results Processing CLI Options
==============================

Command line options for configuring results handling in psimulate runs.

"""
import click

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
