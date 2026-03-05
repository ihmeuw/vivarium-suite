"""
========================
Jobmon CLI Options
========================

Command line options for Jobmon-based job scheduling in psimulate runs.

"""

import click

with_max_workers = click.option(
    "--max-workers",
    "-w",
    type=click.IntRange(min=1),
    default=8000,
    show_default=True,
    help="The maximum number of workers (and therefore jobs) to run concurrently.",
)
