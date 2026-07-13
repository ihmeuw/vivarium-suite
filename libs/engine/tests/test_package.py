"""Smoke tests for the ``vivarium-engine`` distribution.

Covers a few things that are easy to silently break:

- ``__version__`` resolves via ``importlib.metadata`` and isn't the
   ``"0.0.0+not-installed"`` fallback - guards against a misspelled
   distribution name in ``__init__.py``.
- Top-level re-exports on ``vivarium.engine`` are the same objects as
   in their source modules (no shadowing).
- ``vivarium.engine.framework.artifact`` is engine's integration layer
   only and does NOT re-export ``Artifact`` / ``ArtifactException`` /
   ``EntityKey`` from vivarium-artifact. Guards against accidental
   restoration of the deprecated re-export.
- Sibling namespace packages (``vivarium.artifact``,
   ``vivarium.config_tree``) coexist with engine's ownership of
   ``vivarium/__init__.py``.
- Unknown-attribute access on ``vivarium`` and ``vivarium.engine``
   raises ``AttributeError`` normally (no ``__getattr__`` shim
   swallowing typos).
"""

from __future__ import annotations

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


def test_vivarium_unknown_attribute_raises() -> None:
    """Anything not exposed as a real submodule of the ``vivarium`` namespace
    should raise ``AttributeError``."""
    with pytest.raises(AttributeError, match="module 'vivarium' has no attribute"):
        vivarium.not_a_real_thing  # type: ignore [attr-defined]


def test_vivarium_engine_unknown_attribute_raises() -> None:
    """Unknown attributes raise the default ``AttributeError`` cleanly."""
    with pytest.raises(AttributeError, match="module 'vivarium.engine' has no attribute"):
        vivarium.engine.not_a_real_thing  # type: ignore [attr-defined]
