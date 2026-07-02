"""Testing utilities for the vivarium ecosystem."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-testing-utils")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.testing_utils.fuzzy_checker import FuzzyChecker
