"""Test suite scaffolding for the retired ``vivarium-compat`` package."""

from typing import Any

import pytest


def pytest_sessionfinish(session: Any, exitstatus: pytest.ExitCode) -> None:
    """Remap ``NO_TESTS_COLLECTED`` (exit 5) to success.

    The retired ``vivarium-compat`` package intentionally has no test
    files; ``make test-all`` still invokes pytest as part of the
    monorepo's release job, and would otherwise fail this lib.
    """
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK
