"""
====================
Vivarium Interactive
====================

Run and inspect a vivarium simulation interactively, for example in a notebook.

Top-level re-exports for common API surface:

- ``Simulation``: a simulation that steps on demand and exposes its state.
- ``ObservationError``: raised when a registered observation fails.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-interactive")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.interactive.exceptions import ObservationError
from vivarium.interactive.simulation import Simulation
