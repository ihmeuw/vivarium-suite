"""
===============
Vivarium Dagger
===============

Build and run multi-step data pipelines. The ``dagger`` CLI runs the steps that a
YAML config defines as one Jobmon workflow.

"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-dagger")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
