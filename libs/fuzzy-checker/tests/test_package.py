"""Smoke tests for the ``vivarium-fuzzy-checker`` distribution."""
import inspect

import vivarium.fuzzy_checker
from packaging.version import Version


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    assert vivarium.fuzzy_checker.__version__ != "0.0.0+not-installed"
    Version(vivarium.fuzzy_checker.__version__)


def test_public_api_exports() -> None:
    """Verify the package exposes its public API as the real objects.

    Guards against ``__init__`` drift exposing a name that is missing or a
    broken stub (which a plain ``hasattr`` check would not catch).
    """
    for name in ("FuzzyChecker", "TestResult", "TargetIntervalConfig"):
        assert inspect.isclass(getattr(vivarium.fuzzy_checker, name))
    # StratValue is a ``str | int | float`` type alias, not a class.
    assert vivarium.fuzzy_checker.StratValue is not None
