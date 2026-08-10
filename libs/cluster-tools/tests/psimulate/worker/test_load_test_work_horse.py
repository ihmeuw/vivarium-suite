"""Tests for the load-test work horse."""

import io

import pytest
from pytest_mock import MockerFixture

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate.worker.load_test_work_horse import work_horse

_MODULE = "vivarium.cluster_tools.psimulate.worker.load_test_work_horse"


def test_work_horse_does_not_report_its_own_failure(
    mocker: MockerFixture, captured_logs: io.StringIO
) -> None:
    """A failing load test propagates out of the work horse unreported, leaving
    ``main()`` as the single place a task failure is logged."""
    job_parameters = make_job_parameters(extras={"test_type": "sleep", "num_workers": 2})
    mocker.patch(f"{_MODULE}.ENV_VARIABLES")
    mocker.patch(f"{_MODULE}.sleep_test", side_effect=RuntimeError("load-test-boom"))

    with pytest.raises(RuntimeError, match="load-test-boom"):
        work_horse(job_parameters)

    # Non-empty because the work horse still logs its start and exit; an empty
    # buffer would make the assertion below pass for the wrong reason.
    assert captured_logs.getvalue() != ""
    assert "Unhandled exception in worker" not in captured_logs.getvalue()
