"""Package-level smoke tests."""

import vivarium.risk_distributions


def test_version_resolves_to_installed_distribution():
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.risk_distributions.__version__ != "0.0.0+not-installed"
    Version(vivarium.risk_distributions.__version__)


def test_public_api_reexports():
    """Top-level re-exports stay reachable. A regression that removes one of
    these would only break downstream callers, never local tests.
    """
    from vivarium.risk_distributions import EnsembleDistribution, LogNormal, Normal

    assert EnsembleDistribution is not None
    assert LogNormal is not None
    assert Normal is not None
