import importlib
import sys
import warnings as warnings_module

import pytest
from vivarium._compat import _CompatFinder, install


@pytest.fixture(autouse=True)
def restore_import_state():
    """Snapshot and restore sys.meta_path and sys.modules after each test."""
    saved_meta_path = sys.meta_path[:]
    saved_modules = set(sys.modules.keys())
    yield
    sys.meta_path[:] = saved_meta_path
    for key in list(sys.modules.keys()):
        if key not in saved_modules:
            del sys.modules[key]


@pytest.fixture
def patched_redirects(monkeypatch):
    """Patch _REDIRECTS with a stdlib target and reinstall the finder."""
    monkeypatch.setattr("vivarium._compat._REDIRECTS", {"_test_old_json": "json"})
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _CompatFinder)]
    install()


def test_install_is_idempotent():
    install()
    install()
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


def test_error_when_target_does_not_exist(monkeypatch):
    monkeypatch.setattr(
        "vivarium._compat._REDIRECTS", {"_nonexistent_old": "_nonexistent_new_xyz_abc"}
    )
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _CompatFinder)]
    install()

    with pytest.raises(ModuleNotFoundError):
        with pytest.warns(DeprecationWarning):
            importlib.import_module("_nonexistent_old")
