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


class RecordError(VivariumError):
    """Raised when a record expression raises an exception."""
