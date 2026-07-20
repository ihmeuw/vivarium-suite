"""Smoke tests for the ``vivarium-fuzzy-checker`` distribution."""
import inspect
import types

from packaging.version import Version

import vivarium.fuzzy_checker


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


def test_classes_defined_in_expected_modules() -> None:
    """Verify each public class is defined in its dedicated module, not ``__init__``."""
    assert (
        vivarium.fuzzy_checker.FuzzyChecker.__module__
        == "vivarium.fuzzy_checker.fuzzy_checker"
    )
    assert (
        vivarium.fuzzy_checker.TestResult.__module__
        == vivarium.fuzzy_checker.TargetIntervalConfig.__module__
        == "vivarium.fuzzy_checker.data_structures"
    )


def test_init_only_reexports_public_api() -> None:
    """Verify ``__init__`` exposes only the public API (plus version machinery); no
    implementation names leak."""
    module = vivarium.fuzzy_checker
    non_module_public = {
        name
        for name in dir(module)
        if not name.startswith("_")
        and not isinstance(getattr(module, name), types.ModuleType)
    }
    assert non_module_public == set(module.__all__) | {"version", "PackageNotFoundError"}
