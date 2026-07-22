"""Smoke tests for the ``pytest-vivarium`` distribution."""
from importlib.metadata import entry_points, version

from packaging.version import Version


def test_distribution_is_installed() -> None:
    """Verify the distribution metadata resolves to a real version."""
    Version(version("pytest-vivarium"))


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    import pytest_vivarium

    assert pytest_vivarium.__version__ != "0.0.0+not-installed"
    Version(pytest_vivarium.__version__)


def test_pytest11_entry_point_is_registered() -> None:
    """Verify pytest will auto-load the plugin via its pytest11 entry point.

    The plugin is useless to consumers unless this entry point resolves, so a
    typo in ``pyproject.toml`` should fail loudly here rather than silently
    disabling the markers/fixtures everywhere downstream.
    """
    eps = entry_points(group="pytest11")
    assert "pytest_vivarium" in eps.names
    # Load through the entry point so a broken target (not just a wrong string) fails here.
    assert eps["pytest_vivarium"].load().__name__ == "pytest_vivarium.plugin"
