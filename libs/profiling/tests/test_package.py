"""Package-level smoke tests."""

import vivarium.profiling


def test_version_is_resolvable():
    """``importlib.metadata`` finds the installed distribution and __version__
    is PEP 440 parseable. Guards against a misspelled distribution name in
    ``__init__.py`` silently degrading to the ``"0.0.0+unknown"`` fallback.
    """
    from packaging.version import Version

    assert vivarium.profiling.__version__ != "0.0.0+unknown"
    Version(vivarium.profiling.__version__)
