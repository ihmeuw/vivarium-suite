from pathlib import Path

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


@pytest.fixture
def test_data_dir():
    data_dir = Path(__file__).resolve().parent / "test_data"
    assert data_dir.exists(), "Test directory structure is broken"
    return data_dir


@pytest.fixture(params=[".yaml", ".yml"])
def test_spec(request, test_data_dir):
    return test_data_dir / f"mock_model_specification{request.param}"
