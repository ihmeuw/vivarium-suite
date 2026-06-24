"""Package-level smoke tests."""

import vivarium.config_tree


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.config_tree.__version__ != "0.0.0+not-installed"
    Version(vivarium.config_tree.__version__)


def test_public_api_reexports() -> None:
    """Verify each documented re-export is reachable on the package.

    A regression that removes a name from ``__init__.py`` raises
    ``AttributeError`` here with the specific missing name in the message.
    """
    expected = (
        "ConfigNode",
        "ConfigTree",
        "ConfigurationError",
        "ConfigurationKeyError",
        "DuplicatedConfigurationError",
        "ImproperAccessError",
        "MissingLayerError",
        "load_yaml",
    )
    for name in expected:
        getattr(vivarium.config_tree, name)
