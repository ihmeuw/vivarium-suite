"""
=================
Vivarium Workflow
=================

Build a data pipeline from tasks that declare their inputs and outputs, and let
pytask run only the tasks that are stale.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-workflow")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
