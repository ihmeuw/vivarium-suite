from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-engine")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

import numpy

numpy.seterr(all="raise")

from vivarium.engine.component import Component
from vivarium.engine.framework.artifact import Artifact
from vivarium.engine.framework.configuration import build_model_specification
from vivarium.engine.framework.results.observer import Observer
from vivarium.engine.interface import InteractiveContext
