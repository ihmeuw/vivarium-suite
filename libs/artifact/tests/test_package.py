"""Smoke tests for the ``vivarium-artifact`` distribution."""

import vivarium.artifact


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


def test_public_api_reexports() -> None:
    """Verify each documented re-export is reachable on the package.

    A regression that removes a name from ``__init__.py`` raises
    ``AttributeError`` here with the specific missing name in the message.
    """
    expected = (
        "Artifact",
        "ArtifactException",
        "EntityKey",
    )
    for name in expected:
        getattr(vivarium.artifact, name)


def test_submodules_importable() -> None:
    """Verify ``vivarium.artifact.artifact`` and ``vivarium.artifact.hdf``
    resolve, including the cross-module ``from vivarium.artifact import hdf``
    rewrite done during the extract.
    """
    from vivarium.artifact import artifact, hdf

    assert artifact is not None
    assert hdf is not None
