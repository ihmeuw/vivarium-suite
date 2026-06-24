"""Smoke tests for the ``vivarium-engine`` distribution.

Covers four things that are easy to silently break:

1. ``__version__`` resolves via ``importlib.metadata`` and isn't the
   ``"0.0.0+not-installed"`` fallback - guards against a misspelled
   distribution name in ``__init__.py``.
2. Top-level re-exports on ``vivarium.engine`` are the same objects as
   in their source modules (no shadowing).
3. ``Artifact`` resolved via either of its two soft-landing paths
   (``vivarium.engine.framework.artifact.Artifact``,
   ``vivarium.engine.Artifact`` with a deprecation warning) is identity-
   equal to ``vivarium.artifact.artifact.Artifact`` - cross-distribution
   re-export integrity.
4. The ``vivarium/__init__.py`` and ``vivarium.engine/__init__.py``
   deprecation shims emit warnings pointing at the right new home and
   resolve to the canonical object.

Sibling namespace coexistence (``vivarium.artifact``, ``vivarium.config_tree``)
is also exercised: implicitly when the imports at the top of this module
succeed, and explicitly below.
"""

from __future__ import annotations

import warnings

import pytest

import vivarium
import vivarium.engine
from vivarium.engine import component as engine_component_module
from vivarium.engine.framework import artifact as engine_artifact_pkg
from vivarium.engine.framework.configuration import build_model_specification
from vivarium.engine.framework.results import observer as engine_observer_module
from vivarium.engine.interface import interactive as engine_interactive_module


def test_version_resolves_to_installed_distribution() -> None:
    """``vivarium.engine.__version__`` came from importlib.metadata, not the
    fallback sentinel. Distinct from setuptools_scm's ``"0.0.0+no-git-tag"``
    so a legitimate shallow clone doesn't false-fail."""
    from packaging.version import Version

    assert vivarium.engine.__version__ != "0.0.0+not-installed"
    Version(vivarium.engine.__version__)


def test_engine_public_api_reexports_resolve_to_source_symbols() -> None:
    """The 4 top-level engine re-exports (Artifact is intentionally NOT here;
    it lives in vivarium-artifact) are identity-equal to the source-module
    objects. Drift here (e.g. a local stub shadowing the import) passes
    ``getattr`` checks but fails identity."""
    assert vivarium.engine.Component is engine_component_module.Component
    assert vivarium.engine.InteractiveContext is engine_interactive_module.InteractiveContext
    assert vivarium.engine.Observer is engine_observer_module.Observer
    assert vivarium.engine.build_model_specification is build_model_specification


def test_engine_artifact_subpackage_does_not_expose_artifact_data_model() -> None:
    """``vivarium.engine.framework.artifact`` is engine's integration layer
    only - it exposes ``ArtifactManager``, ``ArtifactInterface``, and helpers,
    but NOT ``Artifact`` / ``ArtifactException`` / ``EntityKey``. Those live
    in ``vivarium.artifact`` and must be imported from there. Guards against
    accidentally restoring the re-export."""
    for name in ("Artifact", "ArtifactException", "EntityKey"):
        assert not hasattr(engine_artifact_pkg, name), (
            f"{name!r} should not be exposed on vivarium.engine.framework.artifact; "
            f"import it from vivarium.artifact instead."
        )
    # The engine-owned names are still there.
    for name in ("ArtifactManager", "ArtifactInterface"):
        assert hasattr(engine_artifact_pkg, name)


def test_vivarium_engine_artifact_legacy_top_level_resolves() -> None:
    """The legacy ``vivarium.engine.Artifact`` top-level path still works
    via the engine ``__getattr__`` shim and resolves to vivarium-artifact's
    class (with a DeprecationWarning, silenced here)."""
    import vivarium.artifact.artifact

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert vivarium.engine.Artifact is vivarium.artifact.artifact.Artifact


def test_sibling_namespace_imports_work() -> None:
    """``import vivarium.artifact`` and ``import vivarium.config_tree`` resolve
    while ``vivarium-engine`` is installed - i.e. engine owning ``vivarium/
    __init__.py`` doesn't break the namespace for siblings. The reverse
    direction (siblings without engine) is exercised by each sibling lib's
    own CI."""
    import vivarium.artifact
    import vivarium.config_tree

    assert vivarium.artifact.__name__ == "vivarium.artifact"
    assert vivarium.config_tree.__name__ == "vivarium.config_tree"


# (name, expected target module in the deprecation message)
_DEPRECATED_TOP_LEVEL_NAMES = [
    ("Artifact", "vivarium.artifact"),
    ("Component", "vivarium.engine"),
    ("InteractiveContext", "vivarium.engine"),
    ("Observer", "vivarium.engine"),
    ("build_model_specification", "vivarium.engine"),
]


@pytest.mark.parametrize("name, expected_module", _DEPRECATED_TOP_LEVEL_NAMES)
def test_top_level_vivarium_import_warns_and_resolves(
    name: str, expected_module: str
) -> None:
    """``from vivarium import <name>`` still works for each pre-monorepo
    top-level attribute, but emits a DeprecationWarning pointing the caller
    at the new canonical module - ``vivarium.artifact`` for ``Artifact``,
    ``vivarium.engine`` for the rest."""
    # Force a fresh lookup: if a prior test cached the attribute on the module
    # the shim wouldn't fire. (As of this writing the shim deliberately
    # doesn't cache, but be defensive in case that changes.)
    vivarium.__dict__.pop(name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        value = getattr(vivarium, name)
    # Resolution must match what the canonical module exposes.
    import importlib

    assert value is getattr(importlib.import_module(expected_module), name)
    # Warning text must point the caller at the right new home.
    matching = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and f"from vivarium import {name}" in str(w.message)
        and f"from {expected_module} import {name}" in str(w.message)
    ]
    assert matching, (
        f"Expected a DeprecationWarning naming {name} -> {expected_module}; "
        f"got {[str(w.message) for w in caught]}"
    )


def test_vivarium_engine_artifact_warns_and_resolves() -> None:
    """``from vivarium.engine import Artifact`` is the mid-migration path
    that should now also deprecate, pointing at ``vivarium.artifact``.
    Resolution still works during the deprecation window."""
    import vivarium.artifact

    # The first access caches on the module via Python's import machinery
    # in some access patterns; force a fresh hit through __getattr__.
    vivarium.engine.__dict__.pop("Artifact", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        value = vivarium.engine.Artifact

    assert value is vivarium.artifact.Artifact
    matching = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "from vivarium.engine import Artifact" in str(w.message)
        and "from vivarium.artifact import Artifact" in str(w.message)
    ]
    assert matching, (
        "Expected a DeprecationWarning routing vivarium.engine.Artifact -> "
        f"vivarium.artifact; got {[str(w.message) for w in caught]}"
    )


def test_vivarium_version_passthrough_does_not_warn() -> None:
    """``vivarium.__version__`` is a silent passthrough to
    ``vivarium.engine.__version__``: tooling-facing, no deprecation
    warning. The reader is not the target audience for the migration."""
    vivarium.__dict__.pop("__version__", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        version = vivarium.__version__
    assert version == vivarium.engine.__version__
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_vivarium_unknown_attribute_raises() -> None:
    """Anything outside the deprecated redirects and ``__version__`` should
    raise ``AttributeError`` with a clear message - guards against the shim
    silently swallowing genuine typos."""
    with pytest.raises(AttributeError, match="module 'vivarium' has no attribute"):
        vivarium.not_a_real_thing


def test_vivarium_engine_unknown_attribute_raises() -> None:
    """Engine's ``__getattr__`` only catches ``Artifact``; other unknown
    attribute names should still ``AttributeError`` normally."""
    with pytest.raises(AttributeError, match="module 'vivarium.engine' has no attribute"):
        vivarium.engine.not_a_real_thing


def test_deprecated_redirects_all_resolve() -> None:
    """Every entry in ``_DEPRECATED_REDIRECTS`` must point at a module that
    actually exposes the named attribute. If a name is removed from its
    canonical module but left in the redirect table, the shim raises
    AttributeError when a downstream caller still uses the old import -
    silently broken back-compat. This test fails fast in CI instead."""
    import importlib

    from vivarium import _DEPRECATED_REDIRECTS

    for name, new_module in _DEPRECATED_REDIRECTS.items():
        mod = importlib.import_module(new_module)
        assert hasattr(mod, name), (
            f"{name!r} maps to {new_module!r} in _DEPRECATED_REDIRECTS but "
            f"that module doesn't expose it - the shim will AttributeError "
            f"for downstream users."
        )
