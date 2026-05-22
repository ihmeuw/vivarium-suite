"""Vivarium simulation engine.

The simulation lifecycle, component model, and runtime for the vivarium
microsimulation framework. This package is the core of the vivarium-suite
monorepo; sibling libs (``vivarium.public_health``, ``vivarium.cluster_tools``,
etc.) compose against the engine via the ``vivarium`` namespace package.

Top-level re-exports for common API surface:

- ``Component``: base class for simulation components.
- ``InteractiveContext``: programmatic simulation driver.
- ``Observer``: results-collection component base class.
- ``build_model_specification``: load a model spec from yaml + config.
"""

from __future__ import annotations

import warnings
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

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


def __getattr__(name: str) -> Any:
    if name == "Artifact":
        warnings.warn(
            "'from vivarium.engine import Artifact' is deprecated. "
            "Artifact now lives in the vivarium-artifact distribution; "
            "use 'from vivarium.artifact import Artifact' instead. "
            "This shim will be removed in a future release; see "
            "vivarium-engine CHANGELOG entry for v5.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from vivarium.artifact import Artifact

        return Artifact
    raise AttributeError(f"module 'vivarium.engine' has no attribute {name!r}")


if TYPE_CHECKING:
    # Keep ``Artifact`` visible to static analyzers on ``vivarium.engine``
    # for the soft-landing period - imports from ``vivarium.engine`` won't
    # type-error even though the runtime resolution warns.
    from vivarium.artifact import Artifact as Artifact
