"""Shared pytest configuration and fixtures for the vivarium ecosystem."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pytest-vivarium")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

# Every consumer that ran pytest with vivarium-testing-utils installed inherited this:
# pytest auto-loaded the plugin, which imported the package and turned numpy
# floating-point warnings into errors process-wide for the test run. Preserved here so
# extracting the plugin does not silently relax that strictness across the ecosystem.
numpy.seterr(all="raise")
