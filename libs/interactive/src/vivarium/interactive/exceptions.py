"""
==========
Exceptions
==========

Exceptions raised by :class:`~vivarium.interactive.simulation.Simulation`.

"""
from vivarium.engine.exceptions import VivariumError


class ObservationError(VivariumError):
    """Raised when an observation registered on a :class:`~vivarium.interactive.simulation.Simulation` raises an exception."""
