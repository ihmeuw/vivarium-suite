"""Tests for the pipeline verbs (``frontend.pipeline``)."""

import dataclasses
import re
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import ExitCode, NodeNotFoundError, TaskOutcome

import vivarium.dagger.frontend.pipeline
from tests.frontend.notebooks import broken_env, recorded_errors, write_source_notebook
from vivarium.dagger.backend.build import TaskExited
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.preview import Reason, StepPreview
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.frontend import (
    Pipeline,
    PipelineError,
    StepExited,
    StepFailed,
    preview,
    run,
)
from vivarium.dagger.frontend.notebook_step import notebook_step
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import (
    Fixture,
    build_fixture,
    stable_code_id,
    with_stable_code_id,
)

_SAME_OUTPUT = "There are some tasks which produce the same output"
"""The start of pytask's message when two tasks claim one product."""


def _increment(source: Path) -> pd.DataFrame:
    """Read the source table and add one to its ``n`` column."""
    frame = pd.read_parquet(source)
    return pd.DataFrame({"n": frame["n"] + 1})


def _constant() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _explode() -> pd.DataFrame:
    """Raise a ValueError, as a step body that fails."""
    raise ValueError("the step broke")


def _explode_differently() -> pd.DataFrame:
    """Raise a RuntimeError, as a second step body that fails."""
    raise RuntimeError("another step broke")


def _interrupt() -> pd.DataFrame:
    """Raise KeyboardInterrupt, as a step body stopped by the user."""
    raise KeyboardInterrupt


class _ReportedError(Exception):
    """Stand in for a failure reported from another process, which names the remote error."""

    def __init__(self, exc_type: str, exc_message: str) -> None:
        super().__init__("a step failed in another process")
        self.exc_type = exc_type
        self.exc_message = exc_message


def _pipeline(fixture: Fixture, *extra: Task, stable: bool = False) -> Pipeline:
    """Return the fixture's steps and ``extra`` as a pipeline; ``stable`` fixes code ids."""
    tasks = [*fixture.tasks, *extra]
    if stable:
        tasks = [with_stable_code_id(task) for task in tasks]
    return Pipeline(tasks, fixture.state, fixture.registry)


def _report_step(fixture: Fixture) -> Task:
    """Return a step 'report.parquet' that reads the fixture's 'final.parquet'."""
    return python_step(
        _increment,
        inputs={"source": "final.parquet"},
        produces="report.parquet",
        registry=fixture.registry,
    )


def _side_step(fixture: Fixture) -> Task:
    """Return a step 'side.parquet' that reads the root input and shares no other step."""
    return python_step(
        _increment,
        inputs={"source": "raw"},
        produces="side.parquet",
        registry=fixture.registry,
    )


def _failing_pipeline(tmp_path: Path) -> Pipeline:
    """Return a raising step with two descendants and one independent step as a pipeline."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        python_step(_explode, inputs={}, produces="broken.parquet", registry=registry),
        python_step(
            _increment,
            inputs={"source": "broken.parquet"},
            produces="child.parquet",
            registry=registry,
        ),
        python_step(
            _increment,
            inputs={"source": "child.parquet"},
            produces="grandchild.parquet",
            registry=registry,
        ),
        python_step(_constant, inputs={}, produces="independent.parquet", registry=registry),
    ]
    return Pipeline(tasks, tmp_path / "state", registry)


def _double_claimed(tmp_path: Path) -> Pipeline:
    """Return a pipeline whose two command steps claim one product file."""
    out = tmp_path / "out.txt"
    tasks = [
        Task(
            name=name,
            run=f"touch {out}",
            inputs={},
            outputs={"out": file_node(out)},
            resources={},
            env=None,
        )
        for name in ("first", "second")
    ]
    return Pipeline(tasks, tmp_path / "state")


def _duplicate_names(tmp_path: Path) -> Pipeline:
    """Return a pipeline whose two command steps share the name 'dup'."""
    tasks = [
        Task(name="dup", run="true", inputs={}, outputs={}, resources={}, env=None)
        for _ in range(2)
    ]
    return Pipeline(tasks, tmp_path / "state")


def _errors_from_run_and_preview(pipeline: Pipeline) -> dict[str, PipelineError]:
    """Return the PipelineError run() and preview() raise for ``pipeline``, keyed by verb."""
    with pytest.raises(PipelineError) as ran:
        run(pipeline)
    with pytest.raises(PipelineError) as planned:
        preview(pipeline)
    return {"run": ran.value, "preview": planned.value}


def _outcomes(plan: list[StepPreview]) -> dict[str, TaskOutcome]:
    """Map each planned step's name to its outcome."""
    return {step.name: step.outcome for step in plan}


def _reasons(plan: list[StepPreview]) -> dict[str, list[Reason]]:
    """Map each planned step's name to its reasons."""
    return {step.name: step.reasons for step in plan}


def _nodes(reasons: list[Reason]) -> set[str]:
    """Return the node each reason names."""
    return {reason.node for reason in reasons}


def _assert_no_path(plan: list[StepPreview], tmp_path: Path) -> None:
    """Assert that no reason in ``plan`` names its node by an absolute path."""
    for step in plan:
        for reason in step.reasons:
            assert not Path(reason.node).is_absolute(), reason
            assert str(tmp_path.resolve()) not in reason.node, reason


def _first_frame(error: BaseException) -> traceback.FrameSummary:
    """Return the first frame of ``error``'s traceback."""
    assert error.__traceback__ is not None
    return traceback.extract_tb(error.__traceback__)[0]


def test_run_builds_every_step_and_returns_each_outcome_by_step_name(
    tmp_path: Path,
) -> None:
    """run() with no selection runs every step and returns {step name: TaskOutcome}."""
    fixture = build_fixture(tmp_path)

    assert run(_pipeline(fixture)) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)

    final = pd.read_parquet(fixture.registry.path_for("final.parquet"))
    assert final["n"].tolist() == [12, 14, 16]


def test_run_only_runs_the_named_step_and_its_ancestors_and_skips_the_rest(
    tmp_path: Path,
) -> None:
    """run(only=[s]) runs s and its ancestors; every other step reports SKIP."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, _report_step(fixture), _side_step(fixture))

    assert run(pipeline, only=["final.parquet"]) == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SUCCESS,
        "report.parquet": TaskOutcome.SKIP,
        "side.parquet": TaskOutcome.SKIP,
    }
    assert fixture.registry.path_for("final.parquet").exists()
    assert not fixture.registry.path_for("report.parquet").exists()
    assert not fixture.registry.path_for("side.parquet").exists()


def test_run_only_selects_exactly_so_a_parquet_does_not_run_data_parquet(
    tmp_path: Path,
) -> None:
    """With steps 'a.parquet' and 'data.parquet', only=['a.parquet'] never runs data.parquet."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        python_step(_constant, inputs={}, produces=key, registry=registry)
        for key in ("a.parquet", "data.parquet")
    ]
    pipeline = Pipeline(tasks, tmp_path / "state", registry)

    assert run(pipeline, only=["a.parquet"]) == {
        "a.parquet": TaskOutcome.SUCCESS,
        "data.parquet": TaskOutcome.SKIP,
    }
    assert registry.path_for("a.parquet").exists()
    assert not registry.path_for("data.parquet").exists()


def test_run_only_treats_a_bare_string_as_one_step_name(tmp_path: Path) -> None:
    """only='final.parquet' selects that one step, not its characters."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, _report_step(fixture))

    assert run(pipeline, only="final.parquet") == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SUCCESS,
        "report.parquet": TaskOutcome.SKIP,
    }


def test_run_after_a_run_only_continues_with_the_remaining_steps(tmp_path: Path) -> None:
    """With stable code ids, run(only=[s]) then run() runs only the rest; the prefix is SKIP_UNCHANGED."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, stable=True)

    assert run(pipeline, only=["mid.parquet"]) == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SKIP,
    }
    assert run(pipeline) == {
        "mid.parquet": TaskOutcome.SKIP_UNCHANGED,
        "final.parquet": TaskOutcome.SUCCESS,
    }


def test_run_force_reruns_a_current_step(tmp_path: Path) -> None:
    """With stable code ids, run(force=[s]) re-runs s even though nothing changed."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, stable=True)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SKIP_UNCHANGED)

    assert run(pipeline, force=["final.parquet"]) == {
        "mid.parquet": TaskOutcome.SKIP_UNCHANGED,
        "final.parquet": TaskOutcome.SUCCESS,
    }


def test_run_force_leaves_a_descendant_with_identical_input_bytes_unchanged(
    tmp_path: Path,
) -> None:
    """A forced step that rewrites identical bytes leaves its descendant SKIP_UNCHANGED."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, stable=True)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)

    assert run(pipeline, force=["mid.parquet"]) == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SKIP_UNCHANGED,
    }


def test_run_force_on_a_fresh_root_runs_every_step(tmp_path: Path) -> None:
    """force before any build (no pytask.lock yet) does not fail, and every step runs."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, stable=True)
    assert not (fixture.state / "pytask.lock").exists()

    outcomes = run(pipeline, force=["mid.parquet"])

    assert outcomes == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)


@pytest.mark.parametrize(
    "selection",
    [
        {"only": ["mid.parqet"]},
        {"force": ["mid.parqet"]},
        {"only": ["final.parquet"], "force": ["mid.parqet"]},
        {"force": ["final.parquet", "mid.parqet"]},
    ],
    ids=["only", "force", "force_beside_valid_only", "force_beside_valid_force"],
)
def test_an_unknown_step_raises_before_any_state_is_written(
    tmp_path: Path, selection: dict[str, list[str]]
) -> None:
    """An unknown name in only or force raises PipelineError listing valid names and a did-you-mean; pytask.lock is unchanged."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    lock = fixture.state / "pytask.lock"
    before = lock.read_bytes()

    with pytest.raises(PipelineError) as info:
        run(pipeline, **selection)

    message = str(info.value)
    assert "mid.parqet" in message
    assert all(step in message for step in fixture.steps), message
    assert re.search(r"(?i)did you mean\W+mid\.parquet", message), message
    assert lock.read_bytes() == before


def test_a_failing_step_raises_step_failed_naming_the_step(tmp_path: Path) -> None:
    """A python_step target that raises makes run() raise StepFailed, a PipelineError whose message names the step."""
    with pytest.raises(StepFailed) as info:
        run(_failing_pipeline(tmp_path))

    error = info.value
    assert isinstance(error, PipelineError)
    assert list(error.failures) == ["broken.parquet"]
    message = str(error)
    assert "broken.parquet" in message
    assert "ValueError" in message
    assert "the step broke" in message


def test_step_failed_chains_the_original_exception_with_a_traceback_starting_in_user_code(
    tmp_path: Path,
) -> None:
    """StepFailed.__cause__ is the original exception, and its traceback's first frame is in the user's module."""
    with pytest.raises(StepFailed) as info:
        run(_failing_pipeline(tmp_path))

    cause = info.value.__cause__
    assert isinstance(cause, ValueError)
    assert str(cause) == "the step broke"
    assert cause is info.value.failures["broken.parquet"]
    first = _first_frame(cause)
    assert Path(first.filename).resolve() == Path(__file__).resolve()
    assert first.name == _explode.__name__


def test_step_failed_outcomes_show_the_failure_and_skip_its_descendants(
    tmp_path: Path,
) -> None:
    """StepFailed.outcomes has FAIL for the failed step and SKIP_PREVIOUS_FAILED for its descendants."""
    with pytest.raises(StepFailed) as info:
        run(_failing_pipeline(tmp_path))

    outcomes = info.value.outcomes
    assert set(outcomes) == {
        "broken.parquet",
        "child.parquet",
        "grandchild.parquet",
        "independent.parquet",
    }
    assert outcomes["broken.parquet"] == TaskOutcome.FAIL
    assert outcomes["child.parquet"] == TaskOutcome.SKIP_PREVIOUS_FAILED
    assert outcomes["grandchild.parquet"] == TaskOutcome.SKIP_PREVIOUS_FAILED


def test_an_independent_step_still_completes_when_another_step_fails(
    tmp_path: Path,
) -> None:
    """A step on an independent branch reports SUCCESS in StepFailed.outcomes and writes its product."""
    pipeline = _failing_pipeline(tmp_path)

    with pytest.raises(StepFailed) as info:
        run(pipeline)

    assert info.value.outcomes["independent.parquet"] == TaskOutcome.SUCCESS
    assert pipeline.registry is not None
    frame = pd.read_parquet(pipeline.registry.path_for("independent.parquet"))
    assert frame["n"].tolist() == [1, 2, 3]


def test_two_failing_steps_are_both_reported_and_the_first_is_the_cause(
    tmp_path: Path,
) -> None:
    """Two independent failures both appear in StepFailed.failures; __cause__ is the first in report order."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        python_step(_explode, inputs={}, produces="first.parquet", registry=registry),
        python_step(
            _explode_differently, inputs={}, produces="second.parquet", registry=registry
        ),
    ]

    with pytest.raises(StepFailed) as info:
        run(Pipeline(tasks, tmp_path / "state", registry))

    error = info.value
    assert set(error.failures) == {"first.parquet", "second.parquet"}
    assert isinstance(error.failures["first.parquet"], ValueError)
    assert isinstance(error.failures["second.parquet"], RuntimeError)
    failed_in_order = [
        name for name, outcome in error.outcomes.items() if outcome == TaskOutcome.FAIL
    ]
    assert list(error.failures) == failed_in_order
    assert error.__cause__ is error.failures[failed_in_order[0]]
    assert error.outcomes == {
        "first.parquet": TaskOutcome.FAIL,
        "second.parquet": TaskOutcome.FAIL,
    }
    message = str(error)
    for text in ("first.parquet", "ValueError", "the step broke"):
        assert text in message
    for text in ("second.parquet", "RuntimeError", "another step broke"):
        assert text in message


def test_a_failure_raised_by_pytask_keeps_the_full_traceback(tmp_path: Path) -> None:
    """A failure with no user frame (an input deleted after definition) is still StepFailed, with its full traceback."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    fixture.inputs["raw"].unlink()

    with pytest.raises(StepFailed) as info:
        run(pipeline)

    error = info.value
    assert list(error.failures) == ["mid.parquet"]
    assert error.outcomes == {
        "mid.parquet": TaskOutcome.FAIL,
        "final.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    cause = error.__cause__
    assert isinstance(cause, NodeNotFoundError)
    assert cause is error.failures["mid.parquet"]
    assert cause.__traceback__ is not None
    frames = traceback.extract_tb(cause.__traceback__)
    assert "_pytask" in Path(frames[0].filename).parts
    assert "_pytask" in Path(frames[-1].filename).parts
    # pluggy frames sit only mid-traceback, so one shows the traceback was not cut.
    assert any("pluggy" in Path(frame.filename).parts for frame in frames)


def test_a_failing_command_step_surfaces_its_called_process_error(tmp_path: Path) -> None:
    """A command step that exits nonzero raises StepFailed whose cause is a CalledProcessError naming the command."""
    command = "exit 3"
    task = Task(
        name="shout",
        run=command,
        inputs={},
        outputs={"out": file_node(tmp_path / "out.txt")},
        resources={},
        env=None,
    )

    with pytest.raises(StepFailed) as info:
        run(Pipeline([task], tmp_path / "state"))

    assert "shout" in str(info.value)
    cause = info.value.__cause__
    assert isinstance(cause, subprocess.CalledProcessError)
    assert cause.cmd == command
    assert cause.returncode == 3
    assert command in str(cause)


def test_step_failed_message_reads_exc_type_and_exc_message_when_present() -> None:
    """An error carrying exc_type and exc_message attributes is described by them in StepFailed's message."""
    failures: dict[str, BaseException] = {
        "remote.parquet": _ReportedError("ZeroDivisionError", "worker divided by zero"),
        "local.parquet": ValueError("a local failure"),
    }
    outcomes = {"remote.parquet": TaskOutcome.FAIL, "local.parquet": TaskOutcome.FAIL}

    error = StepFailed(failures, outcomes)

    assert isinstance(error, PipelineError)
    assert error.failures == failures
    assert error.outcomes == outcomes
    message = str(error)
    for text in ("remote.parquet", "ZeroDivisionError", "worker divided by zero"):
        assert text in message
    for text in ("local.parquet", "ValueError", "a local failure"):
        assert text in message
    assert _ReportedError.__name__ not in message
    assert "a step failed in another process" not in message


def test_run_raises_pipeline_error_when_pytask_cannot_plan(tmp_path: Path) -> None:
    """Two tasks producing one node make run() raise PipelineError carrying pytask's message."""
    with pytest.raises(PipelineError) as info:
        run(_double_claimed(tmp_path))

    assert not isinstance(info.value, StepFailed)
    assert _SAME_OUTPUT in str(info.value)


def test_run_raises_pipeline_error_when_the_backend_rejects_the_tasks(
    tmp_path: Path,
) -> None:
    """Two steps sharing a name make run() raise PipelineError chained from the backend's ValueError."""
    with pytest.raises(PipelineError) as info:
        run(_duplicate_names(tmp_path))

    cause = info.value.__cause__
    assert isinstance(cause, ValueError)
    assert "dup" in str(cause)


def test_run_passes_build_keyword_arguments_to_the_backend_build(tmp_path: Path) -> None:
    """A keyword argument given to run() reaches the backend build and pytask."""
    fixture = build_fixture(tmp_path)

    outcomes = run(_pipeline(fixture), dry_run=True)

    assert outcomes == dict.fromkeys(fixture.steps, TaskOutcome.WOULD_BE_EXECUTED)
    assert not any(fixture.registry.path_for(step).exists() for step in fixture.steps)


def test_preview_names_reasons_by_artifact_key_and_step_label(tmp_path: Path) -> None:
    """A registry-wired preview names reasons as artifact, code of, command of, or step labels, with no absolute path."""
    fixture = build_fixture(tmp_path)
    registry = fixture.registry
    copied = registry.register_output("raw_copy.parquet")
    copy = Task(
        name="copy",
        run=f"cp {fixture.inputs['raw']} {registry.path_for('raw_copy.parquet')}",
        inputs={"src": registry.resolve_input("raw")},
        outputs={"dst": copied},
        resources={},
        env=None,
    )
    pipeline = _pipeline(fixture, copy)

    first = preview(pipeline)

    reasons = _reasons(first)
    expected = {"artifact 'raw'", "artifact 'mid.parquet'", "code of 'mid.parquet'"}
    assert expected <= _nodes(reasons["mid.parquet"])
    assert reasons["final.parquet"] == [Reason("step 'mid.parquet'", "cascade")]
    expected = {"artifact 'raw'", "command of 'copy'", "artifact 'raw_copy.parquet'"}
    assert expected <= _nodes(reasons["copy"])
    _assert_no_path(first, tmp_path)

    run(pipeline)
    pd.DataFrame({"n": [4, 5, 6]}).to_parquet(fixture.inputs["raw"])
    edited = preview(pipeline)

    assert Reason("artifact 'raw'", "changed") in _reasons(edited)["mid.parquet"]
    _assert_no_path(edited, tmp_path)


def test_preview_without_a_registry_keeps_file_paths(tmp_path: Path) -> None:
    """With registry=None, preview does not fail and file reasons keep pytask's path names."""
    fixture = build_fixture(tmp_path)

    plan = preview(Pipeline(fixture.tasks, fixture.state))

    raw = fixture.inputs["raw"].resolve().as_posix()
    mid = fixture.registry.path_for("mid.parquet").as_posix()
    assert {raw, mid} <= _nodes(_reasons(plan)["mid.parquet"])
    every = {reason.node for step in plan for reason in step.reasons}
    assert not any(node.startswith("artifact ") for node in every)


def test_preview_only_reports_unselected_steps_as_skip(tmp_path: Path) -> None:
    """preview(only=[s]) plans s and its ancestors; every other step is SKIP with no reasons."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, _report_step(fixture), _side_step(fixture))

    plan = preview(pipeline, only=["final.parquet"])

    assert _outcomes(plan) == {
        "mid.parquet": TaskOutcome.WOULD_BE_EXECUTED,
        "final.parquet": TaskOutcome.WOULD_BE_EXECUTED,
        "report.parquet": TaskOutcome.SKIP,
        "side.parquet": TaskOutcome.SKIP,
    }
    reasons = _reasons(plan)
    assert reasons["mid.parquet"] and reasons["final.parquet"]
    assert reasons["report.parquet"] == []
    assert reasons["side.parquet"] == []


def test_preview_raises_pipeline_error_when_pytask_cannot_plan(tmp_path: Path) -> None:
    """Two tasks producing one node make preview() raise PipelineError carrying pytask's message."""
    with pytest.raises(PipelineError) as info:
        preview(_double_claimed(tmp_path))

    assert _SAME_OUTPUT in str(info.value)


def test_preview_raises_pipeline_error_when_the_backend_rejects_the_tasks(
    tmp_path: Path,
) -> None:
    """Two steps sharing a name make preview() raise PipelineError chained from the backend's ValueError."""
    with pytest.raises(PipelineError) as info:
        preview(_duplicate_names(tmp_path))

    cause = info.value.__cause__
    assert isinstance(cause, ValueError)
    assert "dup" in str(cause)


def test_preview_raises_pipeline_error_for_an_unknown_step(tmp_path: Path) -> None:
    """An unknown name in preview's only raises PipelineError listing the valid step names."""
    fixture = build_fixture(tmp_path)

    with pytest.raises(PipelineError) as info:
        preview(_pipeline(fixture), only=["mid.parqet"])

    message = str(info.value)
    assert "mid.parqet" in message
    assert all(step in message for step in fixture.steps), message


def test_pipeline_is_frozen_and_its_registry_defaults_to_none(tmp_path: Path) -> None:
    """Pipeline's fields cannot be reassigned, and registry defaults to None."""
    fixture = build_fixture(tmp_path)
    pipeline = Pipeline(fixture.tasks, fixture.state)

    assert pipeline.registry is None
    replacements: dict[str, object] = {
        "tasks": [],
        "root": tmp_path,
        "registry": fixture.registry,
    }
    for field, value in replacements.items():
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(pipeline, field, value)


def test_pipeline_stores_tasks_as_a_tuple_that_a_change_to_the_passed_list_does_not_reach(
    tmp_path: Path,
) -> None:
    """Pipeline(tasks_list, root).tasks is a tuple equal to tuple(tasks_list), and appending to or clearing tasks_list afterwards leaves it unchanged."""
    fixture = build_fixture(tmp_path)
    tasks_list = list(fixture.tasks)
    expected = tuple(tasks_list)

    pipeline = Pipeline(tasks_list, fixture.state)

    assert isinstance(pipeline.tasks, tuple)
    assert pipeline.tasks == expected
    tasks_list.append(_report_step(fixture))
    assert pipeline.tasks == expected
    tasks_list.clear()
    assert pipeline.tasks == expected


def test_an_operator_previews_runs_up_to_a_step_continues_and_forces_a_step(
    tmp_path: Path,
) -> None:
    """End to end: preview, run(only=) up to a step, run() to continue, then force a step, each with the expected outcomes."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, _report_step(fixture), stable=True)
    steps = ("mid.parquet", "final.parquet", "report.parquet")
    planned = dict.fromkeys(steps, TaskOutcome.WOULD_BE_EXECUTED)

    assert _outcomes(preview(pipeline)) == planned

    assert run(pipeline, only="final.parquet") == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SUCCESS,
        "report.parquet": TaskOutcome.SKIP,
    }
    assert _outcomes(preview(pipeline)) == {
        "mid.parquet": TaskOutcome.SKIP_UNCHANGED,
        "final.parquet": TaskOutcome.SKIP_UNCHANGED,
        "report.parquet": TaskOutcome.WOULD_BE_EXECUTED,
    }

    assert run(pipeline) == {
        "mid.parquet": TaskOutcome.SKIP_UNCHANGED,
        "final.parquet": TaskOutcome.SKIP_UNCHANGED,
        "report.parquet": TaskOutcome.SUCCESS,
    }
    report = pd.read_parquet(fixture.registry.path_for("report.parquet"))
    assert report["n"].tolist() == [13, 15, 17]

    assert run(pipeline, force="mid.parquet") == {
        "mid.parquet": TaskOutcome.SUCCESS,
        "final.parquet": TaskOutcome.SKIP_UNCHANGED,
        "report.parquet": TaskOutcome.SKIP_UNCHANGED,
    }


def test_run_force_adds_forced_steps_to_the_only_selection(tmp_path: Path) -> None:
    """With stable code ids, run(only=[a], force=[b]) for a b outside a's ancestry also runs b."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, _side_step(fixture), stable=True)
    steps = (*fixture.steps, "side.parquet")
    assert run(pipeline) == dict.fromkeys(steps, TaskOutcome.SUCCESS)

    assert run(pipeline, only="mid.parquet", force="final.parquet") == {
        "mid.parquet": TaskOutcome.SKIP_UNCHANGED,
        "final.parquet": TaskOutcome.SUCCESS,
        "side.parquet": TaskOutcome.SKIP,
    }


def test_run_force_with_an_unreadable_lockfile_raises_pipeline_error(
    tmp_path: Path,
) -> None:
    """With an invalid pytask.lock, run(force=[s]) raises PipelineError chained from pytask's error."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture)
    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    (fixture.state / "pytask.lock").write_text("this is not toml [\n")

    with pytest.raises(PipelineError) as info:
        run(pipeline, force="mid.parquet")

    assert not isinstance(info.value, StepFailed)
    cause = info.value.__cause__
    assert isinstance(cause, Exception)
    assert not isinstance(cause, PipelineError)


def test_an_interrupt_raised_in_a_step_propagates_as_itself(tmp_path: Path) -> None:
    """A python_step target that raises KeyboardInterrupt makes run() raise KeyboardInterrupt, not StepFailed."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = python_step(_interrupt, inputs={}, produces="stop.parquet", registry=registry)

    with pytest.raises(KeyboardInterrupt):
        run(Pipeline([task], tmp_path / "state", registry))


def test_pipeline_rejects_a_root_that_is_not_a_path(tmp_path: Path) -> None:
    """Pipeline(tasks, str(tmp_path / 'state')) raises TypeError at construction, naming the type it got."""
    tasks = build_fixture(tmp_path).tasks
    root = str(tmp_path / "state")

    with pytest.raises(TypeError, match=r"\bstr\b"):
        Pipeline(tasks, root)  # type: ignore[arg-type]


def test_pipeline_rejects_a_relative_root(tmp_path: Path) -> None:
    """Pipeline(tasks, Path('state')) raises ValueError at construction, saying the root must be absolute."""
    tasks = build_fixture(tmp_path).tasks

    with pytest.raises(ValueError, match="(?i)absolute"):
        Pipeline(tasks, Path("state"))


def test_pipeline_error_carries_pytasks_exit_code_when_pytask_stops(tmp_path: Path) -> None:
    """With a malformed pytask.toml under the root, run and preview raise PipelineError whose exit_code is ExitCode.CONFIGURATION_FAILED."""
    fixture = build_fixture(tmp_path)
    fixture.state.mkdir()
    (fixture.state / "pytask.toml").write_text("[[[ not toml\n")

    errors = _errors_from_run_and_preview(_pipeline(fixture))

    for verb, error in errors.items():
        assert not isinstance(error, StepFailed), verb
        assert error.exit_code == ExitCode.CONFIGURATION_FAILED, verb


def test_pipeline_error_exit_code_names_a_planning_failure(tmp_path: Path) -> None:
    """When two hand-built tasks claim one product, run and preview raise PipelineError whose exit_code is ExitCode.DAG_FAILED."""
    errors = _errors_from_run_and_preview(_double_claimed(tmp_path))

    for verb, error in errors.items():
        assert not isinstance(error, StepFailed), verb
        assert error.exit_code == ExitCode.DAG_FAILED, verb


def test_pipeline_error_exit_code_is_none_before_pytask_runs(tmp_path: Path) -> None:
    """An unknown step and a backend rejection (two steps sharing a name) raise PipelineError whose exit_code is None."""
    pipeline = _pipeline(build_fixture(tmp_path))
    calls: dict[str, Callable[[], object]] = {
        "run, unknown step": lambda: run(pipeline, only="mid.parqet"),
        "run, unknown forced step": lambda: run(pipeline, force="mid.parqet"),
        "preview, unknown step": lambda: preview(pipeline, only="mid.parqet"),
        "run, shared name": lambda: run(_duplicate_names(tmp_path)),
        "preview, shared name": lambda: preview(_duplicate_names(tmp_path)),
    }

    for case, call in calls.items():
        with pytest.raises(PipelineError) as info:
            call()
        assert info.value.exit_code is None, case


def test_step_failed_exit_code_is_failed(tmp_path: Path) -> None:
    """A failing step raises StepFailed whose exit_code is ExitCode.FAILED."""
    with pytest.raises(StepFailed) as info:
        run(_failing_pipeline(tmp_path))

    assert info.value.exit_code == ExitCode.FAILED


# A failed step's message, in step terms (MIC-7785)

_RAISES_KEY_ERROR = "{}['no_such_column']"
"""A notebook body that raises KeyError('no_such_column')."""

_SUMMARY_KEY = "tables/summary.csv"

_EXECUTED_KEY = "executed/summarize.ipynb"

_REPORT = "its output is in pytask's report for the step."
"""The end of the description of a failed shell command."""


def _notebook_pipeline(
    tmp_path: Path, body: str = _RAISES_KEY_ERROR, env: str = sys.prefix
) -> Pipeline:
    """Return a pipeline of the notebook step 'summarize', which runs ``body``."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = write_source_notebook(
        tmp_path / "notebooks" / "summarize.ipynb", "summary_path = 'unset'", body
    )
    task = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=registry,
        env=env,
        produces={"summary_path": _SUMMARY_KEY},
        name="summarize",
    )
    return Pipeline([task], tmp_path / "state", registry)


def _executed(pipeline: Pipeline) -> Path:
    """Return the path of the notebook step's executed notebook."""
    assert pipeline.registry is not None
    return pipeline.registry.path_for(_EXECUTED_KEY)


def _key_error_line(pipeline: Pipeline) -> str:
    """Return the StepFailed line for the notebook step that raised KeyError."""
    return (
        "Step 'summarize' failed: notebook summarize.ipynb raised KeyError: "
        "'no_such_column' in cell In [3]. The executed notebook, with the error, is at "
        f"{_executed(pipeline)}."
    )


def _fallback_line(pipeline: Pipeline) -> str:
    """Return the StepFailed line for the notebook step whose command exited 1 early."""
    return (
        "Step 'summarize' failed: notebook summarize.ipynb left no error in an executed "
        f"notebook at {_executed(pipeline)}, and the command exited with status 1; "
        f"{_REPORT}"
    )


def _step_failed(pipeline: Pipeline) -> StepFailed:
    """Run ``pipeline`` and return the StepFailed it raises."""
    with pytest.raises(StepFailed) as info:
        run(pipeline)
    return info.value


def _command_step(tmp_path: Path, name: str, command: str) -> Task:
    """Return a hand-built command step ``name`` that runs ``command``."""
    return Task(
        name=name,
        run=command,
        inputs={},
        outputs={"out": file_node(tmp_path / f"{name}.txt")},
        resources={},
        env=None,
    )


def _run_own_command() -> pd.DataFrame:
    """Raise CalledProcessError, as a step body whose own command failed."""
    raise subprocess.CalledProcessError(2, ["my-tool", "--input", "data.csv"])


def _missing_column() -> pd.DataFrame:
    """Raise KeyError, as a step body that reads a column that is not there."""
    raise KeyError("no_such_column")


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebook_step_message_names_the_step_notebook_cell_error_and_executed_path(
    tmp_path: Path,
) -> None:
    """A notebook step named 'summarize' whose body cell raises KeyError('no_such_column') makes run() raise StepFailed whose message line is "Step 'summarize' failed: notebook summarize.ipynb raised KeyError: 'no_such_column' in cell In [3]. The executed notebook, with the error, is at <path>.", and the executed notebook at that path holds the error."""
    pipeline = _notebook_pipeline(tmp_path)

    error = _step_failed(pipeline)

    assert str(error) == _key_error_line(pipeline)
    assert _executed(pipeline).is_absolute()
    assert ("KeyError", "'no_such_column'") in recorded_errors(_executed(pipeline))


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebook_step_message_does_not_contain_the_generated_command(
    tmp_path: Path,
) -> None:
    """The StepFailed message for a failing notebook step contains neither 'papermill', 'ipykernel', 'mkdir' nor 'CalledProcessError'."""
    message = str(_step_failed(_notebook_pipeline(tmp_path)))

    for text in ("papermill", "ipykernel", "mkdir", "CalledProcessError"):
        assert text not in message, message


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebook_step_keeps_its_called_process_error_as_failure_and_cause(
    tmp_path: Path,
) -> None:
    """For a failing notebook step, StepFailed.failures holds the step's CalledProcessError, which is also the cause, and its cmd is the step's command."""
    pipeline = _notebook_pipeline(tmp_path)

    error = _step_failed(pipeline)

    [task] = pipeline.tasks
    failure = error.failures["summarize"]
    assert isinstance(failure, subprocess.CalledProcessError)
    assert error.__cause__ is failure
    assert failure.cmd == task.run
    assert failure.returncode != 0


def test_a_notebook_step_that_fails_before_papermill_runs_gives_the_exit_status(
    tmp_path: Path,
) -> None:
    """A notebook step whose env's python exits 1 before papermill writes anything makes StepFailed's line name the step and the notebook, say it left no error in an executed notebook at its path, and give 'the command exited with status 1', without the generated command."""
    env = broken_env(tmp_path / "broken_env")
    pipeline = _notebook_pipeline(tmp_path, env=str(env))

    error = _step_failed(pipeline)

    message = str(error)
    assert message == _fallback_line(pipeline)
    assert not _executed(pipeline).exists()
    assert "papermill" not in message
    assert isinstance(error.failures["summarize"], subprocess.CalledProcessError)


@pytest.mark.usefixtures("jupyter_dirs")
def test_an_error_left_by_an_earlier_run_is_not_reported_as_this_runs(tmp_path: Path) -> None:
    """After a run in which the notebook raised KeyError, a second run whose env fails before papermill starts reports the exit-status fallback, not the earlier KeyError."""
    first = _notebook_pipeline(tmp_path)
    assert str(_step_failed(first)) == _key_error_line(first)
    assert recorded_errors(_executed(first))

    env = broken_env(tmp_path / "broken_env")
    second = _notebook_pipeline(tmp_path, env=str(env))
    message = str(_step_failed(second))

    assert _executed(second) == _executed(first)
    assert message == _fallback_line(second)
    assert "KeyError" not in message


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_notebook_step_that_does_not_write_a_product_keeps_the_generic_description(
    tmp_path: Path,
) -> None:
    """A notebook that finishes without writing a declared product makes StepFailed's line give pytask's NodeNotFoundError type and message, as before."""
    pipeline = _notebook_pipeline(tmp_path, body="pass")

    error = _step_failed(pipeline)

    failure = error.failures["summarize"]
    assert isinstance(failure, NodeNotFoundError)
    assert str(error) == f"Step 'summarize' failed: NodeNotFoundError: {failure}"
    assert recorded_errors(_executed(pipeline)) == []


def test_a_failing_command_step_message_gives_the_exit_status_and_not_the_command(
    tmp_path: Path,
) -> None:
    """A hand-built command step 'shout' running 'exit 3' makes StepFailed's message exactly "Step 'shout' failed: the command exited with status 3; its output is in pytask's report for the step.", while its cause is still the CalledProcessError naming the command."""
    task = _command_step(tmp_path, "shout", "exit 3")

    error = _step_failed(Pipeline([task], tmp_path / "state"))

    assert str(error) == f"Step 'shout' failed: the command exited with status 3; {_REPORT}"
    cause = error.__cause__
    assert isinstance(cause, subprocess.CalledProcessError)
    assert cause is error.failures["shout"]
    assert cause.cmd == "exit 3"


def test_a_command_step_stopped_by_a_signal_is_described_by_the_signal(
    tmp_path: Path,
) -> None:
    """A command step whose shell is killed with SIGKILL makes StepFailed's line say 'the command was stopped by signal SIGKILL'."""
    task = _command_step(tmp_path, "killed", "kill -9 $$")

    error = _step_failed(Pipeline([task], tmp_path / "state"))

    assert str(error) == (
        f"Step 'killed' failed: the command was stopped by signal SIGKILL; {_REPORT}"
    )


def test_a_python_step_that_raises_called_process_error_keeps_its_type_and_message(
    tmp_path: Path,
) -> None:
    """A python_step whose own code raises CalledProcessError is described as 'CalledProcessError: <its message>', with its command, since that command is the user's own."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = python_step(
        _run_own_command, inputs={}, produces="tool.parquet", registry=registry
    )

    error = _step_failed(Pipeline([task], tmp_path / "state", registry))

    failure = error.failures["tool.parquet"]
    assert isinstance(failure, subprocess.CalledProcessError)
    assert str(error) == f"Step 'tool.parquet' failed: CalledProcessError: {failure}"
    assert "my-tool" in str(error)
    assert "the command exited with status" not in str(error)


def test_a_python_step_failure_is_still_described_by_type_and_message(tmp_path: Path) -> None:
    """A python_step that raises KeyError('no_such_column') is still described as "KeyError: 'no_such_column'"."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = python_step(_missing_column, inputs={}, produces="read.parquet", registry=registry)

    error = _step_failed(Pipeline([task], tmp_path / "state", registry))

    assert str(error) == "Step 'read.parquet' failed: KeyError: 'no_such_column'"


def test_step_failed_uses_the_given_descriptions_and_defaults_the_rest() -> None:
    """StepFailed(failures, outcomes, descriptions={'a': 'text'}) gives "Step 'a' failed: text" and describes a step missing from descriptions by its error's type and message, or by exc_type and exc_message."""
    failures: dict[str, BaseException] = {
        "a": ValueError("a broke"),
        "b": ValueError("b broke"),
        "remote": _ReportedError("ZeroDivisionError", "worker divided by zero"),
    }
    outcomes = dict.fromkeys(failures, TaskOutcome.FAIL)

    error = StepFailed(failures, outcomes, descriptions={"a": "text"})

    assert str(error).splitlines() == [
        "Step 'a' failed: text",
        "Step 'b' failed: ValueError: b broke",
        "Step 'remote' failed: ZeroDivisionError: worker divided by zero",
    ]
    assert error.failures == failures
    assert error.outcomes == outcomes
    assert error.exit_code == ExitCode.FAILED


def test_several_failed_steps_each_get_their_own_description(tmp_path: Path) -> None:
    """A run in which a command step and a python step both fail gives one line per step, each described its own way, in pytask's report order."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        _command_step(tmp_path, "shout", "exit 3"),
        python_step(_explode, inputs={}, produces="broken.parquet", registry=registry),
    ]
    expected = {
        "shout": f"Step 'shout' failed: the command exited with status 3; {_REPORT}",
        "broken.parquet": "Step 'broken.parquet' failed: ValueError: the step broke",
    }

    error = _step_failed(Pipeline(tasks, tmp_path / "state", registry))

    assert set(error.failures) == set(expected)
    failed_in_order = [
        name for name, outcome in error.outcomes.items() if outcome == TaskOutcome.FAIL
    ]
    assert list(error.failures) == failed_in_order
    assert str(error).splitlines() == [expected[step] for step in failed_in_order]


# Two steps with one name (MIC-7785)


def _named(tmp_path: Path, *names: str) -> Pipeline:
    """Return a pipeline of command steps with ``names``, each writing its own file."""
    tasks = [
        Task(
            name=name,
            run="true",
            inputs={},
            outputs={"out": file_node(tmp_path / f"out_{index}.txt")},
            resources={},
            env=None,
        )
        for index, name in enumerate(names)
    ]
    return Pipeline(tasks, tmp_path / "state")


def _shared(name: str, count: str = "Two") -> str:
    """Return the line for ``count`` steps that share ``name``."""
    return (
        f"{count} steps are named {name!r}; give each a different name= to tell them "
        "apart."
    )


def test_two_steps_with_one_name_make_run_raise_a_pipeline_error_naming_it(
    tmp_path: Path,
) -> None:
    """Two steps named 'dup' make run() raise PipelineError, not StepFailed, whose message is exactly "Two steps are named 'dup'; give each a different name= to tell them apart.", with exit_code None, chained from the backend's ValueError, and nothing written under the root."""
    with pytest.raises(PipelineError) as info:
        run(_named(tmp_path, "dup", "dup"))

    error = info.value
    assert not isinstance(error, StepFailed)
    assert str(error) == _shared("dup")
    assert error.exit_code is None
    cause = error.__cause__
    assert isinstance(cause, ValueError)
    assert "Task names must be unique" in str(cause)
    assert not (tmp_path / "state").exists()


def test_two_steps_with_one_name_make_preview_raise_the_same_pipeline_error(
    tmp_path: Path,
) -> None:
    """preview() on a pipeline with two steps named 'dup' raises the same PipelineError message as run()."""
    errors = _errors_from_run_and_preview(_named(tmp_path, "dup", "dup"))

    assert str(errors["preview"]) == str(errors["run"]) == _shared("dup")
    assert errors["preview"].exit_code is None
    assert isinstance(errors["preview"].__cause__, ValueError)


def test_three_steps_with_one_name_are_counted_with_a_digit(tmp_path: Path) -> None:
    """Three steps named 'dup' are reported as "3 steps are named 'dup'; give each a different name= ..."."""
    with pytest.raises(PipelineError) as info:
        run(_named(tmp_path, "dup", "dup", "dup"))

    assert str(info.value) == _shared("dup", count="3")


def test_each_shared_name_gets_its_own_line_in_sorted_order(tmp_path: Path) -> None:
    """Two pairs of steps sharing the names 'b' and 'a' give two lines, the one for 'a' first, and the message has no Python list."""
    with pytest.raises(PipelineError) as info:
        run(_named(tmp_path, "b", "b", "a", "a", "c"))

    message = str(info.value)
    assert message.splitlines() == [_shared("a"), _shared("b")]
    assert "[" not in message


def test_a_shared_name_is_reported_before_an_unknown_step(tmp_path: Path) -> None:
    """run() with only= naming an unknown step on a pipeline with a shared name reports the shared name."""
    with pytest.raises(PipelineError) as info:
        run(_named(tmp_path, "dup", "dup"), only="no_such_step")

    assert str(info.value) == _shared("dup")


def test_a_shared_name_is_reported_before_force_touches_the_lockfile(tmp_path: Path) -> None:
    """run() with force= on a pipeline with a shared name raises before pytask.lock under the root is read or changed."""
    lock = tmp_path / "state" / "pytask.lock"
    lock.parent.mkdir()
    lock.write_text("this is not toml [\n")
    before = lock.read_bytes()

    with pytest.raises(PipelineError) as info:
        run(_named(tmp_path, "dup", "dup"), force="dup")

    assert str(info.value) == _shared("dup")
    assert isinstance(info.value.__cause__, ValueError)
    assert lock.read_bytes() == before


# A step that exits (MIC-7785)


def _leave() -> pd.DataFrame:
    """End the process with code 0, as a step body that calls sys.exit does."""
    sys.exit(0)


def _leave_with_a_message() -> pd.DataFrame:
    """End the process with the code 'bye'."""
    sys.exit("bye")


def _exiting(tmp_path: Path, target: Callable[[], pd.DataFrame]) -> Pipeline:
    """Return a pipeline of one python step 'leave' that runs ``target``."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = python_step(
        target, inputs={}, produces="leave.parquet", registry=registry, name="leave"
    )
    return Pipeline([task], tmp_path / "state", registry)


def test_a_step_that_calls_sys_exit_makes_run_raise_step_exited_naming_it(
    tmp_path: Path,
) -> None:
    """A step 'leave' that calls sys.exit(0) makes run() raise StepExited, which is a SystemExit whose step is 'leave' and whose code is 0, chained from the backend's TaskExited, which is chained from the step's own SystemExit."""
    with pytest.raises(StepExited) as info:
        run(_exiting(tmp_path, _leave))

    exited = info.value
    assert isinstance(exited, SystemExit)
    assert exited.step == "leave"
    assert exited.code == 0
    cause = exited.__cause__
    assert isinstance(cause, TaskExited)
    assert cause.task == "leave"
    original = cause.__cause__
    assert type(original) is SystemExit
    assert original.code == 0


def test_step_exited_keeps_a_non_integer_exit_code(tmp_path: Path) -> None:
    """A step that calls sys.exit('bye') makes run() raise StepExited whose code is 'bye'."""
    with pytest.raises(StepExited) as info:
        run(_exiting(tmp_path, _leave_with_a_message))

    assert info.value.step == "leave"
    assert info.value.code == "bye"


def test_step_exited_is_caught_by_an_except_system_exit_clause(tmp_path: Path) -> None:
    """Code that calls run() inside 'except SystemExit' catches a step's exit, as it did before StepExited existed."""
    pipeline = _exiting(tmp_path, _leave)
    caught: SystemExit | None = None

    try:
        run(pipeline)
    except SystemExit as error:
        caught = error

    assert caught is not None
    assert caught.code == 0


def _write_first(first: Path) -> None:
    """Write the first step's product."""
    first.write_text("first")


def _read_and_leave(first: Path, leave: Path) -> None:
    """Read the first step's product, then end the process with code 3."""
    first.read_text()
    sys.exit(3)


def _read_leave(leave: Path, after: Path) -> None:
    """Read the exiting step's product and write the last product."""
    after.write_text(leave.read_text())


def test_a_middle_step_that_exits_stops_the_build_after_its_upstream_step(
    tmp_path: Path,
) -> None:
    """In a chain first -> leave -> after where 'leave' calls sys.exit(3), run() raises StepExited for 'leave' with code 3; 'first' wrote its product and 'after' did not."""
    out = tmp_path / "out"
    first, leave, after = out / "first.txt", out / "leave.txt", out / "after.txt"

    def _step(name: str, run: Callable[..., None], inputs: dict[str, Path]) -> Task:
        return Task(
            name=name,
            run=run,
            inputs={key: file_node(path) for key, path in inputs.items()},
            outputs={name: file_node(out / f"{name}.txt")},
            resources={},
            env=None,
            code_id=stable_code_id(f"{name}::code"),
        )

    out.mkdir()
    pipeline = Pipeline(
        [
            _step("first", _write_first, {}),
            _step("leave", _read_and_leave, {"first": first}),
            _step("after", _read_leave, {"leave": leave}),
        ],
        tmp_path / "state",
    )

    with pytest.raises(StepExited) as info:
        run(pipeline)

    assert info.value.step == "leave"
    assert info.value.code == 3
    assert first.read_text() == "first"
    assert not after.exists()


# The frontend's exports (MIC-7785)


def test_step_exited_is_exported_from_the_frontend() -> None:
    """vivarium.dagger.frontend.StepExited is the pipeline module's StepExited, and 'StepExited' is in vivarium.dagger.frontend.__all__."""
    frontend = vivarium.dagger.frontend

    assert frontend.StepExited is vivarium.dagger.frontend.pipeline.StepExited
    assert "StepExited" in frontend.__all__


@pytest.mark.usefixtures("without_bridge_plugin")
def test_run_with_jobmon_but_no_bridge_plugin_raises_before_force_touches_the_lockfile(
    tmp_path: Path,
) -> None:
    """run(force=..., jobmon=True) without the bridge's plugin raises PipelineError, chained from
    the backend's ValueError, and leaves pytask.lock byte-identical."""
    fixture = build_fixture(tmp_path)
    pipeline = _pipeline(fixture, stable=True)
    run(pipeline)
    lockfile = fixture.state / "pytask.lock"
    before = lockfile.read_bytes()

    with pytest.raises(PipelineError, match="its pytask plugin is not registered") as excinfo:
        run(pipeline, force=["mid.parquet"], jobmon=True)

    assert isinstance(excinfo.value.__cause__, ValueError)
    assert excinfo.value.exit_code is None
    assert lockfile.read_bytes() == before


# Pipeline resources and the bridge's errors (MIC-7752)


def test_pipeline_resources_default_to_an_empty_mapping(tmp_path: Path) -> None:
    """Pipeline(tasks, root) has resources equal to {}, and two pipelines made that way do not share one mapping object."""
    first = Pipeline([], tmp_path / "first")
    second = Pipeline([], tmp_path / "second")

    assert first.resources == {}
    assert second.resources == {}
    assert first.resources is not second.resources


def test_pipeline_keeps_the_resources_it_is_given(tmp_path: Path) -> None:
    """Pipeline(tasks, root, registry, resources={'project': 'proj_a'}) has resources equal to {'project': 'proj_a'}."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")

    pipeline = Pipeline([], tmp_path / "state", registry, resources={"project": "proj_a"})

    assert pipeline.resources == {"project": "proj_a"}
    assert pipeline.registry is registry


def test_pipeline_rejects_resources_that_are_not_a_mapping(tmp_path: Path) -> None:
    """Pipeline(tasks, root, resources=[('project', 'proj_a')]) raises TypeError naming the type it got."""
    pairs: Any = [("project", "proj_a")]

    with pytest.raises(TypeError, match=r"\blist\b"):
        Pipeline([], tmp_path / "state", resources=pairs)


def test_run_without_jobmon_ignores_pipeline_resources_and_needs_no_project(
    tmp_path: Path,
) -> None:
    """run(pipeline) on a Pipeline with resources={'memory': 4} and no project anywhere runs every step locally and returns SUCCESS for each."""
    fixture = build_fixture(tmp_path)
    pipeline = Pipeline(
        fixture.tasks, fixture.state, fixture.registry, resources={"memory": 4}
    )

    assert run(pipeline) == dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    assert fixture.registry.path_for("final.parquet").exists()


def test_run_refused_and_run_interrupted_are_exported_pipeline_errors() -> None:
    """RunRefused and RunInterrupted are subclasses of PipelineError, are importable from vivarium.dagger.frontend, and are in its __all__."""
    frontend = vivarium.dagger.frontend
    module = vivarium.dagger.frontend.pipeline

    assert frontend.RunRefused is module.RunRefused
    assert frontend.RunInterrupted is module.RunInterrupted
    assert issubclass(frontend.RunRefused, PipelineError)
    assert issubclass(frontend.RunInterrupted, PipelineError)
    assert {"RunRefused", "RunInterrupted"} <= set(frontend.__all__)


def test_run_interrupted_keeps_its_workflow_id_run_directory_outcomes_and_exit_code(
    tmp_path: Path,
) -> None:
    """RunInterrupted('msg', workflow_id=7, run_directory=path, outcomes={...}, exit_code=ExitCode.FAILED) has those attributes and str() equal to 'msg'."""
    run_directory = tmp_path / "run"
    reported = {"a": TaskOutcome.SUCCESS, "b": TaskOutcome.FAIL}

    error = vivarium.dagger.frontend.RunInterrupted(
        "msg",
        workflow_id=7,
        run_directory=run_directory,
        outcomes=reported,
        exit_code=ExitCode.FAILED,
    )

    assert error.workflow_id == 7
    assert error.run_directory == run_directory
    assert error.outcomes == reported
    assert error.exit_code == ExitCode.FAILED
    assert str(error) == "msg"
