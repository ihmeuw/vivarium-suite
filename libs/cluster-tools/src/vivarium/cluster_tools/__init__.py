"""
======================
Vivarium Cluster Tools
======================

Shared cluster, Jobmon, logging and notification helpers for Vivarium tools.
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-cluster-tools")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.cluster_tools.utilities import get_cluster_name, mkdir
