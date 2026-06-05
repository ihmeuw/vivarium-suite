"""
==========
Entity Key
==========

Provides the :class:`EntityKey` class and :func:`is_entity_key` helper for
working with artifact entity keys formatted as ``"type.name.measure"`` or
``"type.measure"``.

"""

from __future__ import annotations


def is_entity_key(key: str) -> bool:
    """Check whether a string is a valid artifact entity key.

    A valid entity key has the format ``"type.name.measure"`` or
    ``"type.measure"`` - that is, 2 or 3 non-empty dot-separated elements
    with no leading, trailing, or consecutive dots.

    Parameters
    ----------
    key
        The string to check.

    Returns
    -------
        True if the string matches the entity key format, False otherwise.
    """
    elements = [e for e in key.split(".") if e]
    return len(elements) in [2, 3] and len(key.split(".")) == len(elements)


class EntityKey(str):
    """A convenience wrapper that translates artifact keys.

    This class provides several representations of the artifact keys that
    are useful when working with the :mod:`pandas` and
    `tables <https://www.pytables.org>`_ HDF interfaces.

    """

    def __init__(self, key: str) -> None:
        """
        Parameters
        ----------
        key
            The string representation of the entity key.  Must be formatted
            as ``"type.name.measure"`` or ``"type.measure"``.

        Raises
        ------
        ValueError
            If the key is improperly formatted.
        """
        if not is_entity_key(key):
            raise ValueError(
                f"Invalid format for HDF key: {key}. "
                'Acceptable formats are "type.name.measure" and "type.measure"'
            )
        super().__init__()

    @property
    def type(self) -> str:
        """The type of the entity represented by the key."""
        return self.split(".")[0]

    @property
    def name(self) -> str:
        """The name of the entity represented by the key"""
        return self.split(".")[1] if len(self.split(".")) == 3 else ""

    @property
    def measure(self) -> str:
        """The measure associated with the data represented by the key."""
        return self.split(".")[-1]

    @property
    def group_prefix(self) -> str:
        """The HDF group prefix for the key."""
        return "/" + self.type if self.name else "/"

    @property
    def group_name(self) -> str:
        """The HDF group name for the key."""
        return self.name if self.name else self.type

    @property
    def group(self) -> str:
        """The full path to the group for this key."""
        return (
            self.group_prefix + "/" + self.group_name
            if self.name
            else self.group_prefix + self.group_name
        )

    @property
    def path(self) -> str:
        """The full HDF path associated with this key."""
        return self.group + "/" + self.measure

    def with_measure(self, measure: str) -> "EntityKey":
        """Replace this key's measure with the provided one.

        Parameters
        ----------
        measure
            The measure to replace this key's measure with.

        Returns
        -------
            A new EntityKey with the updated measure.
        """
        if self.name:
            return EntityKey(f"{self.type}.{self.name}.{measure}")
        else:
            return EntityKey(f"{self.type}.{measure}")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and str(self) == str(other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash(str(self))

    def __repr__(self) -> str:
        return f"EntityKey({str(self)})"
