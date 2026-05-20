"""Smoke tests for the ``vivarium-artifact`` distribution."""

import vivarium.artifact
from vivarium.artifact import artifact as artifact_module
from vivarium.artifact import hdf as hdf_module


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.artifact.__version__ != "0.0.0+not-installed"
    Version(vivarium.artifact.__version__)


def test_public_api_reexports_resolve_to_source_symbols() -> None:
    """Verify package-root re-exports are the same objects as in their source modules.

    A drift in ``__init__.py`` (e.g. shadowing ``Artifact`` with a local stub)
    would pass a bare ``getattr`` check but fail identity here.
    """
    assert vivarium.artifact.Artifact is artifact_module.Artifact
    assert vivarium.artifact.ArtifactException is artifact_module.ArtifactException
    assert vivarium.artifact.EntityKey is hdf_module.EntityKey


def test_submodules_importable() -> None:
    """Verify ``vivarium.artifact.artifact`` and ``vivarium.artifact.hdf``
    resolve, including the cross-module ``from vivarium.artifact import hdf``
    rewrite done during the extract.
    """
    assert artifact_module.hdf is hdf_module
