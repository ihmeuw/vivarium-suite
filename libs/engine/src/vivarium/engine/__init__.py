"""
===============
Vivarium Engine
===============

The simulation lifecycle, component model, and runtime for the vivarium
microsimulation framework.

Top-level re-exports for common API surface:

- ``Component``: base class for simulation components.
- ``InteractiveContext``: programmatic simulation driver.
- ``Observer``: results-collection component base class.
- ``build_model_specification``: load a model spec from yaml + config.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-engine")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

numpy.seterr(all="raise")

from vivarium.engine.component import Component
from vivarium.engine.framework.configuration import build_model_specification
from vivarium.engine.framework.results.observer import Observer
from vivarium.engine.interface import InteractiveContext
