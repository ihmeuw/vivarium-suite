from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-validation")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

numpy.seterr(all="raise")

from vivarium.validation.interface import ValidationContext
