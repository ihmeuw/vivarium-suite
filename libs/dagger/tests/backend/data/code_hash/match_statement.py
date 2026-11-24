"""Describe values with a match statement."""

from dataclasses import dataclass


@dataclass
class Point:
    """A point in the plane."""

    x: float
    y: float


def describe(value: object) -> str:
    """Return a short description of a value."""
    match value:
        case None:
            return "nothing"
        case 0 | 1 as small:
            return f"small {small}"
        case [first, *rest] if rest:
            return f"sequence from {first!r}"
        case {"kind": "point", **others}:
            return f"point with {len(others)} more keys"
        case Point(x=0, y=0):
            return "origin"
        case Point(x=x, y=y) if x == y:
            return f"diagonal at {x}"
        case str() | bytes():
            return "text"
        case -1.5 | 1 + 2j:
            return "special"
        case _:
            return "other"
