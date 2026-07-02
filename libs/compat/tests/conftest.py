import sys

import pytest
from vivarium_compat._compat import _resolving


@pytest.fixture(autouse=True)
def restore_import_state():
    """Snapshot/reset global import state (sys.meta_path, sys.modules, _resolving) around each test."""
    # Clear at setup too, in case a prior test left a stale entry.
    _resolving.clear()
    saved_meta_path = sys.meta_path[:]
    saved_modules = set(sys.modules.keys())
    yield
    sys.meta_path[:] = saved_meta_path
    for key in list(sys.modules.keys()):
        if key not in saved_modules:
            del sys.modules[key]
    _resolving.clear()
