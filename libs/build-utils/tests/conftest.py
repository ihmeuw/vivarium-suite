"""Pytest configuration for vivarium-build-utils' own test suite.

This deliberately duplicates the ``--runslow`` and ``--runweekly`` handling
from the ``pytest-vivarium`` plugin. vbu cannot depend on pytest-vivarium to
pick up that plugin, because pytest-vivarium depends on vbu; registering the
options here lets the shared Jenkins ``pytest`` invocations
(which pass ``--runslow`` and ``--runweekly``) run against vbu without erroring
on an unrecognized option.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")
    parser.addoption(
        "--runweekly", action="store_true", default=False, help="run weekly tests"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: mark test as slow to run")
    config.addinivalue_line("markers", "weekly: mark test as a weekly test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--runslow"):
        skip_slow = pytest.mark.skip(reason="need --runslow option to run")
        for item in items:
            if item.get_closest_marker("slow"):
                item.add_marker(skip_slow)

    if not config.getoption("--runweekly"):
        skip_weekly = pytest.mark.skip(reason="need --runweekly option to run")
        for item in items:
            if item.get_closest_marker("weekly"):
                item.add_marker(skip_weekly)
