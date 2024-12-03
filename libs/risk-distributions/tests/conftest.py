import numpy as np
import pandas as pd
import pytest


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow to run")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def assert_equal(a, b):
    if isinstance(a, (pd.Series, pd.DataFrame)):
        assert a.equals(b)
    elif isinstance(a, np.ndarray):
        assert np.allclose(a, b)
    else:
        assert a == b
