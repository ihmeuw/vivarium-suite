"""Pytest plugin providing common fixtures for vivarium projects.

This module is automatically loaded by pytest when vivarium.testing_utils is installed,
via the pytest11 entry point declared in pyproject.toml.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from _pytest.config import Config, argparsing
from _pytest.python import Function
from pytest_mock import MockerFixture
from vivarium.config_tree import ConfigTree

SLOW_TEST_DAY = "Sunday"


def is_on_slurm() -> bool:
    """Returns True if the current environment is a SLURM cluster."""
    return shutil.which("sbatch") is not None


IS_ON_SLURM = is_on_slurm()


def pytest_addoption(parser: argparsing.Parser) -> None:
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")
    parser.addoption(
        "--runweekly", action="store_true", default=False, help="run weekly tests"
    )
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
            if item.get_closest_marker("slow"):
                item.add_marker(skip_slow)

    if not IS_ON_SLURM:
        skip_cluster = pytest.mark.skip(reason="not running on SLURM cluster")
        for item in items:
            if item.get_closest_marker("cluster"):
                item.add_marker(skip_cluster)

    # Weekly tests also require it to be the slow test day (unless overridden)
    if not config.getoption("--runweekly") and not is_slow_test_day():
        skip_weekly = pytest.mark.skip(
            reason="not the designated slow test day for weekly tests"
        )
        for item in items:
            if item.get_closest_marker("weekly"):
                item.add_marker(skip_weekly)


# Default ceiling on parallel xdist workers for ``-n auto``.
DEFAULT_MAX_WORKERS = 4

# Conservative per-worker memory budget (GB).
_GB_PER_WORKER = 1.0


def _usable_cpu_count() -> int:
    """Return the CPUs this process may use: its affinity mask, else the system count."""
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def _available_memory_gb() -> float | None:
    """Return available memory in GB from ``/proc/meminfo``, or None when unreadable (non-Linux)."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _auto_num_workers() -> int:
    """Resolve the worker count: target DEFAULT_MAX_WORKERS, clamped to CPUs/memory, floored at 1."""
    workers = min(DEFAULT_MAX_WORKERS, _usable_cpu_count())
    available_gb = _available_memory_gb()
    if available_gb is not None:
        workers = min(workers, int(available_gb // _GB_PER_WORKER))
    return max(1, workers)


def pytest_xdist_auto_num_workers(config: Config) -> int:
    """Resolve a safe worker count for ``-n auto`` (capped at DEFAULT_MAX_WORKERS, scaled to CPUs/memory)."""
    return _auto_num_workers()


def pytest_report_header(config: Config) -> list[str]:
    """Print the resolved xdist worker plan at the top of the run, when running in parallel."""
    numprocesses = config.getoption("numprocesses", default=None)
    if not numprocesses:
        return []
    memory_gb = _available_memory_gb()
    memory = f"{memory_gb:.1f} GB" if memory_gb is not None else "unknown"
    return [
        f"vivarium xdist auto-workers: {_auto_num_workers()} "
        f"(target {DEFAULT_MAX_WORKERS}, usable CPUs {_usable_cpu_count()}, "
        f"available memory {memory}); requested -n {numprocesses}. The actual spawned "
        f"count is in xdist's own 'N workers [M items]' line below."
    ]


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
        return_value=ConfigTree({"input_data": {"cache_data": False}}),
    )
