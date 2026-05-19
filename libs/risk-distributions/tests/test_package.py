"""Package-level smoke tests."""

import vivarium.risk_distributions


def test_version_is_resolvable():
    """``importlib.metadata`` finds the installed distribution and __version__
    is PEP 440 parseable. Guards against a misspelled distribution name in
    ``__init__.py`` silently degrading to the ``"0.0.0+unknown"`` fallback.
    """
    from packaging.version import Version

    assert vivarium.risk_distributions.__version__ != "0.0.0+unknown"
    Version(vivarium.risk_distributions.__version__)


def test_public_api_reexports():
    """Top-level re-exports stay reachable. A regression that removes one of
    these would only break downstream callers, never local tests.
    """
    from vivarium.risk_distributions import EnsembleDistribution, LogNormal, Normal

    assert EnsembleDistribution is not None
    assert LogNormal is not None
    assert Normal is not None
