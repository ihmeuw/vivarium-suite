"""Pytest plugin providing common fixtures for vivarium projects.

This module is automatically loaded by pytest when vivarium_testing_utils is installed,
via the pytest11 entry point defined in setup.py.
"""

import os
import shutil
from datetime import datetime

import pytest
from _pytest.config import Config, argparsing
from _pytest.python import Function
from layered_config_tree import LayeredConfigTree
from pytest_mock import MockerFixture

SLOW_TEST_DAY = "Sunday"


def is_on_slurm() -> bool:
    """Returns True if the current environment is a SLURM cluster."""
    return shutil.which("sbatch") is not None


IS_ON_SLURM = is_on_slurm()


def pytest_addoption(parser: argparsing.Parser) -> None:
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")
    parser.addoption(
        "--slurm-project",
        type=str,
        default="proj_simscience",
        help="SLURM project for cluster tests (default: proj_simscience)",
    )


def pytest_configure(config: Config) -> None:
    config.addinivalue_line("markers", "slow: mark test as slow to run")
    config.addinivalue_line(
        "markers", "cluster: mark test as requiring a SLURM cluster environment"
    )


def pytest_collection_modifyitems(config: Config, items: list[Function]) -> None:
    if not config.getoption("--runslow"):
        skip_slow = pytest.mark.skip(reason="need --runslow option to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)

    if not IS_ON_SLURM:
        skip_cluster = pytest.mark.skip(reason="not running on SLURM cluster")
        for item in items:
            if "cluster" in item.keywords:
                item.add_marker(skip_cluster)

    # Weekly tests also require it to be the slow test day
    if not is_slow_test_day():
        skip_weekly = pytest.mark.skip(
            reason="not the designated slow test day for weekly tests"
        )
        for item in items:
            if "weekly" in item.keywords:
                item.add_marker(skip_weekly)


def pytest_xdist_auto_num_workers(config: Config) -> int:
    """Automatically determine the number of workers for pytest-xdist.

    - On SLURM: Use CPUs allocated to the job (via SLURM environment variables)
    - Not on SLURM: Return 1 (no parallelization by default)
    - Users can override by explicitly passing -n flag to pytest
    """
    cpus = 1
    if IS_ON_SLURM:
        # Check SLURM environment variables in order of preference
        slurm_cpus = os.environ.get("SLURM_CPUS_PER_TASK") or os.environ.get(
            "SLURM_CPUS_ON_NODE"
        )
        if slurm_cpus:
            cpus = int(slurm_cpus)
        # Fallback: use the number of CPUs actually available to this process
        # (respects cgroup constraints set by SLURM)
        else:
            cpus = len(os.sched_getaffinity(0))

    return cpus


def is_slow_test_day(slow_test_day: str = SLOW_TEST_DAY) -> bool:
    """Determine if today is the day to run slow/weekly tests.

    Parameters
    ----------
    slow_test_day
        The day to run the weekly tests on. Acceptable values are "Monday",
        "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", or "Sunday".
        Default is "Sunday".

    Notes
    -----
    There is some risk that a test will be inadvertently skipped if there is a
    significant delay between when a pipeline is kicked off and when the test
    itself is run.
    """
    return [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ][datetime.today().weekday()] == slow_test_day


@pytest.fixture
def no_gbd_cache(mocker: MockerFixture) -> None:
    """Disable vivarium_gbd_access caching for test isolation.

    This fixture mocks ``vivarium_gbd_access.utilities.get_input_config`` to return
    a configuration with ``cache_data`` set to False, ensuring that tests always
    pull fresh data rather than using cached results.

    Note that this fixture does NOT use ``autouse=True``. If you want it to apply
    to all tests in a module or package, create a wrapper fixture in your conftest.py:

    .. code-block:: python

        import pytest

        @pytest.fixture(autouse=True)
        def no_cache(no_gbd_cache):
            '''Apply no_gbd_cache to all tests in this module.'''
            pass

    """
    mocker.patch(
        "vivarium_gbd_access.utilities.get_input_config",
        return_value=LayeredConfigTree({"input_data": {"cache_data": False}}),
    )
