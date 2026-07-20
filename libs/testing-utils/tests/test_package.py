"""Smoke tests for the ``vivarium-testing-utils`` distribution."""
from importlib.metadata import entry_points

from packaging.version import Version

import vivarium.testing_utils


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    assert vivarium.testing_utils.__version__ != "0.0.0+not-installed"
    Version(vivarium.testing_utils.__version__)


def test_pytest_plugin_entry_point_registered() -> None:
    """Verify the pytest11 entry point still resolves to the renamed module.

    The entry-point key is preserved as the underscored ``vivarium_testing_utils``
    for downstream invocation compatibility (``pytest -p no:vivarium_testing_utils``);
    the value-side module path is the new ``vivarium.testing_utils.pytest_plugin``.
    """
    plugins = entry_points(group="pytest11")
    assert "vivarium_testing_utils" in plugins.names
    loaded = plugins["vivarium_testing_utils"].load()
    assert loaded.__name__ == "vivarium.testing_utils.pytest_plugin"
