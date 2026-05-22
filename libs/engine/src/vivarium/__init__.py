"""``vivarium`` namespace package.

vivarium-engine owns this ``__init__.py``. The ``extend_path`` call lets
sibling distributions (vivarium-artifact, vivarium-config-tree, etc.)
contribute their own subpackages under ``vivarium.*``.

The module-level ``__getattr__`` below preserves the pre-monorepo
``from vivarium import Component`` attribute-style imports, emitting a
DeprecationWarning that points callers at the new canonical module
(``vivarium.engine`` for most names; ``vivarium.artifact`` for ``Artifact``,
which moved to its own distribution). The TYPE_CHECKING block mirrors the
canonical home so static analyzers see names on the right modules.
"""

from __future__ import annotations

import importlib
import warnings
from typing import TYPE_CHECKING, Any

__path__ = __import__("pkgutil").extend_path(__path__, __name__)

# Old top-level name -> module that now owns the canonical definition.
# Adding a name here makes ``from vivarium import <name>`` resolve transparently
# (with a DeprecationWarning pointing at the right new home).
_DEPRECATED_REDIRECTS: dict[str, str] = {
    "Artifact": "vivarium.artifact",
    "Component": "vivarium.engine",
    "InteractiveContext": "vivarium.engine",
    "Observer": "vivarium.engine",
    "build_model_specification": "vivarium.engine",
}


def __getattr__(name: str) -> Any:
    if name == "__version__":
        # Silent passthrough: vivarium's pre-monorepo __init__.py exposed
        # __version__ at the top level, and downstream tooling reads it.
        # No deprecation warning - this is purely a tooling-facing attribute.
        import vivarium.engine

        return vivarium.engine.__version__
    if name in _DEPRECATED_REDIRECTS:
        new_module = _DEPRECATED_REDIRECTS[name]
        warnings.warn(
            f"'from vivarium import {name}' is deprecated. "
            f"Use 'from {new_module} import {name}' instead. "
            "This shim will be removed in a future release; see "
            "vivarium-engine CHANGELOG entry for v5.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Deliberately NOT caching on the module: Python's default
        # DeprecationWarning filter dedups by (filename, lineno, message),
        # so each unique caller still emits one warning. Caching would
        # install the resolved object in vivarium.__dict__, making future
        # callers from other locations bypass __getattr__ entirely and
        # miss their warning - hiding stragglers we want to find.
        return getattr(importlib.import_module(new_module), name)
    raise AttributeError(f"module 'vivarium' has no attribute {name!r}")


if TYPE_CHECKING:
    # ``X as X`` form per PEP 484: marks each name as an explicit public
    # re-export so downstream strict-mypy consumers (without implicit_reexport)
    # see them as part of vivarium's API rather than private imports.
    # Each name is imported from its canonical new home so static analyzers
    # learn the real lineage (matters most for Artifact, which is owned by
    # vivarium-artifact, not vivarium-engine).
    from vivarium.artifact import Artifact as Artifact

    from vivarium.engine import Component as Component
    from vivarium.engine import InteractiveContext as InteractiveContext
    from vivarium.engine import Observer as Observer
    from vivarium.engine import build_model_specification as build_model_specification
