"""Define typed classes and functions with decorators."""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import ClassVar, TypeVar

F = TypeVar("F", bound=Callable[..., object])

REGISTRY: dict[str, Callable[..., object]] = {}


def registered(name: str) -> Callable[[F], F]:
    """Return a decorator that records a function under a name."""

    def decorate(function: F) -> F:
        REGISTRY[name] = function
        return function

    return decorate


@dataclass(frozen=True)
class Measure:
    """A named value with a unit."""

    name: str
    value: float = 0.0
    unit: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    count: ClassVar[int] = 0

    @property
    def label(self) -> str:
        """Return the name and the unit."""
        return f"{self.name} ({self.unit or 'none'})"

    @classmethod
    def zero(cls, name: str) -> Measure:
        """Return a measure with no value."""
        return cls(name)


@registered("unit_factor")
@functools.cache
def unit_factor(unit: str, *, base: float = 1.0) -> float:
    """Return the factor that converts a unit to the base unit."""
    return {"km": 1000.0, "m": 1.0, "cm": 0.01}.get(unit, 0.0) * base


@registered("measures")
def measures(names: list[str], /, value: float = 1.0) -> Iterator[Measure]:
    """Yield one measure per name."""
    total: float
    for name in names:
        total = value * unit_factor("m")
        yield Measure(name, total)
