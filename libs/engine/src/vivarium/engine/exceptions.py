"""
==========
Exceptions
==========

Module containing framework-wide exception definitions. Exceptions for
particular subsystems are defined in their respective modules.

"""


class VivariumError(Exception):
    """Generic exception raised for errors in ``vivarium`` simulations."""

    pass


class WatchError(VivariumError):
    """Raised when a watch expression raises an exception."""
