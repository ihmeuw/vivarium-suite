"""
=================
Vivarium Workflow
=================

Build and run multi-step data pipelines.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-workflow")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
