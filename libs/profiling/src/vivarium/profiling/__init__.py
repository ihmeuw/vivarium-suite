"""
==================
Vivarium Profiling
==================

Profiling and benchmarking tools for Vivarium simulations.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-profiling")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
