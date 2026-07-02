"""Test suite scaffolding for the retired ``vivarium-compat`` package.

The package's original tests were deleted in the v1.0.0 retirement commit
(see ``libs/compat/CHANGELOG.rst``). Only this conftest remains so that
``make test-all`` still runs without failing on ``pytest`` exit code 5
(``NO_TESTS_COLLECTED``). Once ``libs/compat/`` itself is removed from
the monorepo in the follow-up PR, this file goes with it.
"""

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
