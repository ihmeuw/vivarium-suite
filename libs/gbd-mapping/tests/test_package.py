"""Package-level smoke tests."""

import vivarium.gbd_mapping


def test_version_is_resolvable():
    """``importlib.metadata`` finds the installed distribution and __version__
    is PEP 440 parseable. Guards against a misspelled distribution name in
    ``__init__.py`` silently degrading to the ``"0.0.0+unknown"`` fallback.
    """
    from packaging.version import Version

    assert vivarium.gbd_mapping.__version__ != "0.0.0+unknown"
    Version(vivarium.gbd_mapping.__version__)


def test_public_api_reexports():
    """Top-level re-exports stay reachable. A regression that removes one of
    these would only break downstream callers, never local tests.
    """
    from vivarium.gbd_mapping import (
        Cause,
        Covariate,
        Etiology,
        GbdRecord,
        RiskFactor,
        Sequela,
        causes,
        covariates,
        etiologies,
        risk_factors,
        sequelae,
    )

    assert Cause is not None
    assert Covariate is not None
    assert Etiology is not None
    assert GbdRecord is not None
    assert RiskFactor is not None
    assert Sequela is not None
    assert causes is not None
    assert covariates is not None
    assert etiologies is not None
    assert risk_factors is not None
    assert sequelae is not None
