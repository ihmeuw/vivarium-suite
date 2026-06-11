"""Testing utilities for the vivarium ecosystem.

This package was migrated into the ``ihmeuw/vivarium-suite`` monorepo;
the previously-standalone ``ihmeuw/vivarium_testing_utils`` GitHub repository
has been archived. The import path changed from ``vivarium_testing_utils`` to
``vivarium.testing_utils`` starting with v1.0.0.
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-testing-utils")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

numpy.seterr(all="raise")

from vivarium.testing_utils.fuzzy_checker import FuzzyChecker
