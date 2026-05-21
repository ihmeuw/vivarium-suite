"""``vivarium`` namespace package.

vivarium-engine owns this ``__init__.py``. The ``extend_path`` call lets
sibling distributions (vivarium-artifact, vivarium-config-tree, etc.)
contribute their own subpackages under ``vivarium.*``.

The module-level ``__getattr__`` below preserves the pre-monorepo
``from vivarium import Component`` attribute-style imports, emitting a
DeprecationWarning that points callers at ``vivarium.engine``. The
TYPE_CHECKING block exists so static analyzers still see these names
on the ``vivarium`` module.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

__path__ = __import__("pkgutil").extend_path(__path__, __name__)

_DEPRECATED_ATTRS = frozenset(
    {
        "Artifact",
        "Component",
        "InteractiveContext",
        "Observer",
        "build_model_specification",
    }
)


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_ATTRS:
        warnings.warn(
            f"'from vivarium import {name}' is deprecated. "
            f"Use 'from vivarium.engine import {name}' instead. "
            "This shim will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        import vivarium.engine

        value = getattr(vivarium.engine, name)
        # Cache so subsequent accesses don't re-warn.
        globals()[name] = value
        return value
    raise AttributeError(f"module 'vivarium' has no attribute {name!r}")


if TYPE_CHECKING:
    from vivarium.engine import (
        Artifact,
        Component,
        InteractiveContext,
        Observer,
        build_model_specification,
    )
