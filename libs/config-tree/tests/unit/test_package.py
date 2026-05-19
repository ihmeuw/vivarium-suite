"""Package-level smoke tests."""

import vivarium.config_tree


def test_version_is_resolvable():
    """``importlib.metadata`` finds the installed distribution and __version__
    is PEP 440 parseable. Guards against a misspelled distribution name in
    ``__init__.py`` silently degrading to the ``"0.0.0+unknown"`` fallback.
    """
    from packaging.version import Version

    assert vivarium.config_tree.__version__ != "0.0.0+unknown"
    Version(vivarium.config_tree.__version__)


def test_public_api_reexports():
    """Top-level re-exports stay reachable. A regression that removes one of
    these would only break downstream callers, never local tests.
    """
    from vivarium.config_tree import (
        ConfigNode,
        ConfigTree,
        ConfigurationError,
        ConfigurationKeyError,
        DuplicatedConfigurationError,
        ImproperAccessError,
        MissingLayerError,
        load_yaml,
    )

    assert ConfigNode is not None
    assert ConfigTree is not None
    assert ConfigurationError is not None
    assert ConfigurationKeyError is not None
    assert DuplicatedConfigurationError is not None
    assert ImproperAccessError is not None
    assert MissingLayerError is not None
    assert load_yaml is not None
