import importlib
import sys
import warnings as warnings_module
from types import ModuleType

import pytest
from vivarium_compat._compat import (
    _CompatFinder,
    _CompatLoader,
    _resolving,
    install_compat_finder,
)


@pytest.fixture
def patched_redirects(monkeypatch):
    """Patch _REDIRECTS with a stdlib target and reinstall the finder."""
    monkeypatch.setattr(
        "vivarium_compat._compat._REDIRECTS", {"_test_old_json": "json"}
    )
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _CompatFinder)]
    install_compat_finder()


def test_install_is_idempotent():
    install_compat_finder()
    install_compat_finder()
    assert sum(1 for f in sys.meta_path if isinstance(f, _CompatFinder)) == 1


def test_finder_ignores_unknown_modules():
    assert _CompatFinder().find_spec("some_unknown_module", None) is None


def test_redirect_resolves_to_target(patched_redirects):
    result = importlib.import_module("_test_old_json")
    import json

    assert result is json


def test_deprecation_warning_emitted(patched_redirects):
    with pytest.warns(DeprecationWarning, match="_test_old_json.*json"):
        importlib.import_module("_test_old_json")


def test_no_warning_on_subsequent_import(patched_redirects):
    # First import populates sys.modules; suppress its warning.
    with warnings_module.catch_warnings():
        warnings_module.simplefilter("ignore")
        importlib.import_module("_test_old_json")

    # Second import hits sys.modules cache — find_spec is never called.
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        importlib.import_module("_test_old_json")

    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_prefix_matches_submodule(patched_redirects):
    spec = _CompatFinder().find_spec("_test_old_json.encoder", None)
    assert spec is not None
    assert spec.loader._new_name == "json.encoder"  # type: ignore[union-attr]


def test_prefix_does_not_match_superstring(patched_redirects):
    finder = _CompatFinder()
    assert finder.find_spec("_test_old_json_extra", None) is None
    assert finder.find_spec("_test_old_json2", None) is None


def test_circular_redirect_raises_import_error():
    """Guard prevents infinite recursion when a redirect target re-triggers the old name."""
    loader = _CompatLoader("_test_circular", "_test_circular_target")
    _resolving.add("_test_circular")
    try:
        with pytest.raises(
            ImportError,
            match="Circular redirect detected: '_test_circular' -> '_test_circular_target'",
        ):
            loader.exec_module(ModuleType("_test_circular"))
    finally:
        _resolving.discard("_test_circular")


def test_circular_guard_cleans_up_on_success():
    """_resolving must not retain entries after a successful redirect."""
    loader = _CompatLoader("_test_clean_old", "json")
    loader.exec_module(ModuleType("_test_clean_old"))
    assert "_test_clean_old" not in _resolving


@pytest.fixture(scope="module")
def _polluted_resolving():
    # Runs before the function-scoped autouse clear
    _resolving.add("prior_test_residue")


def test_resolving_cleared_before_each_test(_polluted_resolving):
    # Fails if the setup-time _resolving.clear() in conftest.py is removed.
    assert _resolving == set()


def test_error_when_neither_target_nor_old_name_exists(monkeypatch):
    """ModuleNotFoundError surfaces only when both new target and old name are missing."""
    monkeypatch.setattr(
        "vivarium_compat._compat._REDIRECTS",
        {"_nonexistent_old": "_nonexistent_new_xyz_abc"},
    )
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _CompatFinder)]
    install_compat_finder()

    with pytest.raises(ModuleNotFoundError):
        with pytest.warns(DeprecationWarning):
            importlib.import_module("_nonexistent_old")


def test_falls_back_when_target_missing_but_old_name_exists(monkeypatch):
    """If the redirect target is not installed, fall back to the old name's real module.

    This makes redirect entries safe to ship ahead of their target packages: existing
    code that imports the old name keeps working against the still-on-disk old package,
    with the DeprecationWarning fired to nudge migration.
    """
    monkeypatch.setattr(
        "vivarium_compat._compat._REDIRECTS",
        {"json": "_nonexistent_new_target_xyz"},
    )
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _CompatFinder)]
    install_compat_finder()

    # Drop the cached `json` so the hook actually fires on next import.
    sys.modules.pop("json", None)

    with pytest.warns(DeprecationWarning):
        result = importlib.import_module("json")

    # Fallback resolved to the real json module despite the (missing) redirect target.
    assert hasattr(result, "loads")
    assert hasattr(result, "dumps")
