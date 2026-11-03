"""Smoke tests for the ``vivarium-workflow`` distribution."""
from packaging.version import Version

import vivarium.workflow


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    assert vivarium.workflow.__version__ != "0.0.0+not-installed"
    Version(vivarium.workflow.__version__)
