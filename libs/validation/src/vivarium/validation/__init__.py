"""
===================
Vivarium Validation
===================

Tooling for automated verification and validation (V&V) of Vivarium
simulations, including data loading, measure comparison, and reporting.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-validation")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

numpy.seterr(all="raise")

from vivarium.validation.interface import ValidationContext
