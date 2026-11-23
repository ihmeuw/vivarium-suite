"""Names for the tests of reported order, in an order that no sort by name gives.

This module imports nothing, so the tests of the backend, of the frontend and of the
bridge can all use it.
"""

EIGHT = ("zeta", "alpha", "mike", "bravo", "yankee", "charlie", "xray", "delta")
"""Eight names of independent tasks or steps. Their order is neither name order nor its
reverse, so a report in this order was not sorted by name."""

TEN = (*EIGHT, "after_mike", "after_zeta")
"""The names of :data:`EIGHT`, then those of two that read the outputs of mike and zeta."""
