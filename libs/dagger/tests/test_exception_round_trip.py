"""Tests that dagger's exceptions survive copy, deepcopy and a pickle round trip."""

import copy
import pickle
from collections.abc import Callable
from pathlib import Path

import pytest
from pytask import ExitCode, TaskOutcome

from vivarium.dagger.backend.build import TaskExited
from vivarium.dagger.backend.preview import PlanFailed
from vivarium.dagger.frontend.execution import RunInterrupted, StepExited, StepFailed


def _pickled(error: BaseException) -> BaseException:
    """Return ``error`` after a pickle round trip."""
    restored = pickle.loads(pickle.dumps(error))
    assert isinstance(restored, BaseException)
    return restored


_ROUND_TRIPS = pytest.mark.parametrize(
    "round_trip", [copy.copy, copy.deepcopy, _pickled], ids=["copy", "deepcopy", "pickle"]
)
"""Run a test once for each way an exception is copied or pickled."""

_OUTCOMES = {
    "a": TaskOutcome.SUCCESS,
    "b": TaskOutcome.FAIL,
    "c": TaskOutcome.FAIL,
    "d": TaskOutcome.SKIP_PREVIOUS_FAILED,
}
"""Every step's outcome from a run in which 'b' and 'c' failed and 'd' did not run."""


@_ROUND_TRIPS
def test_step_failed_survives_copy_deepcopy_and_pickle(
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """A StepFailed with two failures, every step's outcome and a description for one step comes back from copy.copy, copy.deepcopy and a pickle round trip as a StepFailed with the same str(), failures keys, outcomes and exit_code."""
    failures: dict[str, BaseException] = {
        "b": ValueError("the step broke"),
        "c": RuntimeError("the command failed"),
    }
    error = StepFailed(
        failures, dict(_OUTCOMES), descriptions={"c": "command exited with status 3"}
    )

    copied = round_trip(error)

    assert type(copied) is StepFailed
    assert str(copied) == str(error)
    assert list(copied.failures) == ["b", "c"]
    stored = [(type(failure), str(failure)) for failure in copied.failures.values()]
    assert stored == [(ValueError, "the step broke"), (RuntimeError, "the command failed")]
    assert copied.outcomes == _OUTCOMES
    assert copied.exit_code == error.exit_code == ExitCode.FAILED


@_ROUND_TRIPS
def test_run_interrupted_survives_copy_deepcopy_and_pickle(
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """A RunInterrupted with a message, a workflow id, a run directory, outcomes and an exit code comes back from copy.copy, copy.deepcopy and a pickle round trip with the same str(), workflow_id, run_directory, outcomes and exit_code."""
    run_directory = Path("/runs/pipeline/run_7")
    error = RunInterrupted(
        "The run was interrupted while Jobmon ran workflow 42.",
        workflow_id=42,
        run_directory=run_directory,
        outcomes=dict(_OUTCOMES),
        exit_code=ExitCode.FAILED,
    )

    copied = round_trip(error)

    assert type(copied) is RunInterrupted
    assert str(copied) == str(error)
    assert copied.workflow_id == 42
    assert copied.run_directory == run_directory
    assert copied.outcomes == _OUTCOMES
    assert copied.exit_code == ExitCode.FAILED


def test_run_interrupted_without_a_workflow_id_survives_a_pickle_round_trip() -> None:
    """A RunInterrupted whose workflow_id is None comes back from a pickle round trip with workflow_id None and the same str()."""
    error = RunInterrupted(
        "The run was interrupted before its workflow was bound.",
        workflow_id=None,
        run_directory=Path("/runs/pipeline/run_8"),
        outcomes={"a": TaskOutcome.SUCCESS},
    )

    copied = _pickled(error)

    assert type(copied) is RunInterrupted
    assert copied.workflow_id is None
    assert str(copied) == str(error)


@_ROUND_TRIPS
def test_step_exited_survives_copy_deepcopy_and_pickle(
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """A StepExited for step 'x' with code 3 comes back from copy.copy, copy.deepcopy and a pickle round trip as a SystemExit and a StepExited with step 'x', code 3 and the same str()."""
    error = StepExited("x", 3)

    copied = round_trip(error)

    assert isinstance(copied, SystemExit)
    assert type(copied) is StepExited
    assert copied.step == "x"
    assert copied.code == 3
    assert str(copied) == str(error)


@_ROUND_TRIPS
def test_task_exited_survives_copy_deepcopy_and_pickle(
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """A TaskExited for task 'x' with code 'stop' comes back from copy.copy, copy.deepcopy and a pickle round trip as a SystemExit and a TaskExited with task 'x', code 'stop' and the same str()."""
    error = TaskExited("x", "stop")

    copied = round_trip(error)

    assert isinstance(copied, SystemExit)
    assert type(copied) is TaskExited
    assert copied.task == "x"
    assert copied.code == "stop"
    assert str(copied) == str(error)


def test_an_exit_with_code_none_keeps_code_none() -> None:
    """A StepExited and a TaskExited whose code is None each come back from a pickle round trip with code None."""
    for error in (StepExited("x", None), TaskExited("x", None)):
        copied = _pickled(error)

        assert type(copied) is type(error)
        assert isinstance(copied, SystemExit)
        assert copied.code is None, type(error).__name__


@_ROUND_TRIPS
def test_plan_failed_survives_copy_deepcopy_and_pickle(
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """A PlanFailed with a message and exit code FAILED comes back from copy.copy, copy.deepcopy and a pickle round trip with the same str() and exit_code."""
    message = "preview could not build the plan: a cycle"
    error = PlanFailed(message, exit_code=ExitCode.FAILED)

    copied = round_trip(error)

    assert type(copied) is PlanFailed
    assert str(copied) == str(error)
    assert copied.exit_code == ExitCode.FAILED
