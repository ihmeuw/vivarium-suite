"""
====================
Results CLI Options
====================

Command line options for configuring results handling in psimulate runs.

"""

import click

from vivarium_cluster_tools.cli_tools import MINUTES_OR_NONE

backup_freq = click.option(
    "--backup-freq",
    type=MINUTES_OR_NONE,
    default="30",
    show_default=True,
    help="Interval in minutes between saving backups. Set to 'None' or 'none' to disable backups.",
)
