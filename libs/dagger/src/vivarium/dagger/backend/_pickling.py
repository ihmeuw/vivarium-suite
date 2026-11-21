"""Let exceptions whose ``__init__`` does not take their ``args`` survive copy and pickle."""

from collections.abc import Callable
from typing import Any


def reduce_exception(
    error: BaseException,
) -> tuple[Callable[..., BaseException], tuple[Any, ...]]:
    """Return a ``__reduce__`` value that rebuilds ``error`` without calling its ``__init__``.

    The rebuilt exception has the same class, the same ``args``, a copy of the same
    instance attributes (``__dict__``), and, for a :class:`SystemExit`, the same
    ``code``. :mod:`copy` and :mod:`pickle` use the value, so an exception class whose
    ``__init__`` signature differs from its ``args`` returns it from ``__reduce__``.
    """
    # SystemExit keeps its code outside __dict__ and sets it only in __init__.
    code = error.code if isinstance(error, SystemExit) else None
    return _rebuild_exception, (type(error), error.args, dict(error.__dict__), code)


def _rebuild_exception(
    cls: type[BaseException],
    args: tuple[Any, ...],
    attributes: dict[str, Any],
    code: str | int | None,
) -> BaseException:
    """Create a ``cls`` with ``args``, ``attributes`` and, for a SystemExit, ``code``."""
    error = cls.__new__(cls, *args)
    error.__dict__.update(attributes)
    if isinstance(error, SystemExit):
        error.code = code
    return error
