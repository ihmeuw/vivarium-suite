"""
====================
Vivarium Build Utils
====================

Shared build utilities and Jenkins pipeline library for Vivarium projects.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-build-utils")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
