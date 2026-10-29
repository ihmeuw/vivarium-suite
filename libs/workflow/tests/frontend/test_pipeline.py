"""Tests for the pipeline verbs (``frontend.pipeline``)."""

import dataclasses
import re
import subprocess
import traceback
from pathlib import Path

import pandas as pd
import pytest
from pytask import NodeNotFoundError, TaskOutcome

from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.preview import Reason, StepPreview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend import (
    Pipeline,
    PipelineError,
    StepFailed,
    preview,
    python_step,
    run,
)
from vivarium.workflow.harness import Fixture, build_fixture, stable_code_id

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
        tasks = [
            dataclasses.replace(task, code_id=stable_code_id(f"{task.name}::code"))
            for task in tasks
        ]
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
