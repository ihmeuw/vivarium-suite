#################
# Fuzzy Checker #
#################
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-fuzzy-checker")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.fuzzy_checker.data_structures import (
    StratValue,
    TargetIntervalConfig,
    TestResult,
)
from vivarium.fuzzy_checker.fuzzy_checker import FuzzyChecker
