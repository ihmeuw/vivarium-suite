"""Shared pytest configuration and fixtures for the vivarium ecosystem."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pytest-vivarium")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

# Turn numpy floating-point warnings into errors process-wide for the test runs
numpy.seterr(all="raise")
