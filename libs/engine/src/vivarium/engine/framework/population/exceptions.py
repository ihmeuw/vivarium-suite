"""
================================
Population Management Exceptions
================================

Errors related to the mishandling of the population state table.
"""

from vivarium.engine.exceptions import VivariumError


class PopulationError(VivariumError):
    """Error raised when the population is invalidly queried or updated."""

    pass


class ColumnDtypeError(TypeError):
    """Error raised when a column's dtype cannot hold the values written to it.

    Parameters
    ----------
    changes
        For each rejected column, its existing dtype and the dtype of the values
        written to it.
    """

    def __init__(self, changes: dict[str, tuple[str, str]]) -> None:
        self.changes = changes
        super().__init__(self.description)

    @property
    def description(self) -> str:
        """The rejected columns, each with its existing and its new dtype."""
        return ", ".join(
            f"{column} from {existing} to {new}"
            for column, (existing, new) in self.changes.items()
        )
