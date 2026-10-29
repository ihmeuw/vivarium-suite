"""Tests for recording the results of a bridged build (``bridge.execute``).

The builds here run through ``FakeJobmon``, which runs each plan entry's command, and so
the real node runner, in a child process. The bridge then records each step's result in
the build's process.
"""

import io
import json
import os
import re
import socket
import tomllib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import ExecutionReport, ExitCode, NodeNotFoundError, PTask, Session, TaskOutcome
from rich.console import Console
from rich.theme import Theme

from tests.bridge import step_targets
from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.bridge import execute
from vivarium.workflow.bridge._jobmon_adapter import JobmonRun
from vivarium.workflow.bridge.execute import RemoteTaskFailed, _record_available
from vivarium.workflow.bridge.runner import (
    RESULT_SUFFIX,
    RunResult,
    read_result,
    write_result,
)
from vivarium.workflow.frontend.python_step import python_step
from vivarium.workflow.harness import (
    FakeJobmon,
    Fixture,
    Perturbed,
    build_fixture,
    stable_code_id,
)

_LOCKFILE = "pytask.lock"
"""The name of pytask's lockfile in a build's root."""

_JOURNAL = "pytask.lock.journal"
"""The name of the journal in a build's root that pytask appends each lockfile entry to
while the build runs."""

_FAILING = "failing_step"
"""Name of the step whose target raises on its node."""

_EXCEPTION_LINE = f"tests.bridge.step_targets.StepFailure: {step_targets.FAILURE_MESSAGE}"
"""The last line of the node's traceback for the step whose target raises."""

_NO_PRODUCT = "no_product_step"
"""Name of the command step that exits 0 without writing its product."""

_NOT_PRODUCED = "The task did not produce the following files"
"""The start of pytask's message for a step that did not write a declared product."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
"""An ANSI escape sequence, which pytask's console writes when it styles its output."""

_WIDE = 1000
"""A console width at which no line of a rendered report wraps."""

_NODE_STDOUT = "what the node wrote to stdout"
"""The standard output of a result that a test writes in place of the runner's."""

_NODE_STDERR = "what the node wrote to stderr"
"""The standard error of a result that a test writes in place of the runner's."""

_SPOILED = [
    "not_json",
    "json_array",
    "missing_field",
    "success_as_text",
    "stdout_null",
    "directory",
]
"""The ways in which :func:`_spoil` makes a result file unreadable as a node result."""

_Recording = Callable[[Session, list[PTask], Path], list[PTask]]
"""What a test does in place of the bridge's recording: given the build's session, its
stale tasks in plan order and the run directory, return the tasks left without a result."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


def _mkdir(path: Path) -> Path:
    """Create ``path`` and its parents if missing, and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def _registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry whose products go under ``tmp_path / "out"``."""
    return ArtifactRegistry(output_root=tmp_path / "out", run_name="run1")


def _stable(tasks: Sequence[Task]) -> list[Task]:
    """Return ``tasks``, each with a stable code identity."""
    return [replace(task, code_id=stable_code_id(f"{task.name}::code")) for task in tasks]


def _stable_fixture(directory: Path) -> Fixture:
    """Return the harness fixture under ``directory`` with stable code identities."""
    fx = build_fixture(directory)
    return replace(fx, tasks=_stable(fx.tasks))


def _steps_without_inputs(
    registry: ArtifactRegistry, targets: Mapping[str, Callable[..., pd.DataFrame]]
) -> list[Task]:
    """Return one python step per product key in ``targets``, running its target."""
    return [
        python_step(target, inputs={}, produces=produces, registry=registry)
        for produces, target in targets.items()
    ]


def _constant_steps(registry: ArtifactRegistry, names: Sequence[str]) -> list[Task]:
    """Return one independent python step per name, each writing a constant frame."""
    return _steps_without_inputs(registry, dict.fromkeys(names, step_targets.constant))


def _bound_names(jobmon: FakeJobmon) -> list[list[str]]:
    """Return the step names of each plan that ``jobmon`` bound, in plan order."""
    return [[entry.name for entry in plan] for plan in jobmon.plans]


def _report(session: Session, name: str) -> ExecutionReport:
    """Return the session's one report for the step called ``name``."""
    [report] = [report for report in session.execution_reports if report.task.name == name]
    return report


def _failure(report: ExecutionReport) -> BaseException:
    """Return the exception that a FAIL report holds."""
    assert report.outcome == TaskOutcome.FAIL
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert error is not None
    return error


def _remote_failure(report: ExecutionReport) -> RemoteTaskFailed:
    """Return the RemoteTaskFailed that a FAIL report holds."""
    error = _failure(report)
    assert isinstance(error, RemoteTaskFailed), repr(error)
    return error


def _lockfile_entries(root: Path) -> dict[str, dict[str, Any]]:
    """Return the entries of ``root``'s pytask.lock by task id, or none if it is missing."""
    path = root / _LOCKFILE
    if not path.exists():
        return {}
    lockfile = tomllib.loads(path.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in lockfile.get("task", [])}


def _journal_ids(root: Path) -> set[str]:
    """Return the task ids in ``root``'s lockfile journal, or none if there is none."""
    path = root / _JOURNAL
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["id"] for line in lines if line.strip()}


def _rendered(report: ExecutionReport) -> str:
    """Return ``report`` as pytask's console prints it, as plain text."""
    buffer = io.StringIO()
    # pytask's console theme names each outcome's style; plain text needs only the names.
    theme = Theme({outcome.style: "none" for outcome in TaskOutcome})
    Console(file=buffer, width=_WIDE, color_system=None, theme=theme).print(report)
    return buffer.getvalue()


def _squashed(text: str) -> str:
    """Return ``text`` without ANSI escapes or whitespace, so that wrapped lines join up."""
    return "".join(_ANSI.sub("", text).split())


def _temporary_name(result: Path) -> Path:
    """Return the name that the node runner writes ``result`` under before moving it."""
    return result.with_name(f"{result.name}.{socket.gethostname()}.{os.getpid()}.tmp")


def _only_result(root: Path) -> RunResult:
    """Return the one node result in the run directories under ``root``."""
    [path] = (root / execute.RUNS_DIR).glob(f"*/*{RESULT_SUFFIX}")
    return read_result(path)


def _spoil(result: Path, how: str) -> None:
    """Make the node result file ``result`` unreadable in the way that ``how`` names."""
    if how == "directory":
        result.unlink()
        result.mkdir()
        return
    fields: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))
    if how == "not_json":
        text = "{ this is not JSON"
    elif how == "json_array":
        text = "[]"
    elif how == "missing_field":
        del fields["exc_type"]
        text = json.dumps(fields)
    elif how == "success_as_text":
        text = json.dumps({**fields, "success": "true"})
    elif how == "stdout_null":
        text = json.dumps({**fields, "stdout": None})
    else:
        raise ValueError(f"No way to spoil a result is called {how!r}.")
    result.write_text(text, encoding="utf-8")


def _build_a_failing_step(tmp_path: Path) -> ExecutionReport:
    """Build one step whose target raises on its node with jobmon=True; return its report."""
    task = python_step(
        step_targets.raise_step_failure,
        inputs={},
        produces="failing.parquet",
        registry=_registry(tmp_path),
        name=_FAILING,
    )
    session = build([task], tmp_path / "state", jobmon=True)
    return _report(session, _FAILING)


def _build_a_step_without_its_product(tmp_path: Path) -> tuple[Session, Path]:
    """Build a command step that exits 0 without writing its product, with jobmon=True.

    Return the session and the product's path. The build's root is ``tmp_path / "state"``.
    """
    product = tmp_path / "never_written.txt"
    task = Task(
        name=_NO_PRODUCT,
        run="true",
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=None,
    )
    return build([task], tmp_path / "state", jobmon=True), product


def _build_recording_with(
    tasks: Sequence[Task], root: Path, monkeypatch: pytest.MonkeyPatch, recording: _Recording
) -> tuple[Session, list[PTask]]:
    """Build ``tasks`` with jobmon=True, calling ``recording`` where the bridge records.

    The build's hook calls ``recording`` in place of its own call to ``_record_available``,
    after the active FakeJobmon has run every entry, so ``recording`` can change the run
    directory and then call ``_record_available`` with the build's own session. Return the
    session and what ``recording`` returned.
    """
    returned: list[list[PTask]] = []

    def record(session: Session, stale: Sequence[PTask], workdir: Path) -> list[PTask]:
        """Call ``recording`` and keep what it returns."""
        unrecorded = recording(session, list(stale), workdir)
        returned.append(unrecorded)
        return unrecorded

    # pytask stops capturing output when a build ends, and replaying a step's teardown
    # needs that capture, so the results are recorded inside the build.
    monkeypatch.setattr(execute, "_record_available", record)
    session = build(tasks, root, jobmon=True)
    [unrecorded] = returned
    return session, unrecorded


@dataclass
class _LooksAtTheRoot(FakeJobmon):
    """Stand in for Jobmon, and look at the build's lockfile once every entry has run."""

    root: Path = Path()
    """The build's root."""
    seen: list[set[str]] = field(default_factory=list, init=False)
    """The task ids in the root's pytask.lock and its journal at the end of each run."""

    def run(self, jobmon_run: JobmonRun) -> None:
        """Run every entry as FakeJobmon does, then note the task ids the root holds."""
        super().run(jobmon_run)
        self.seen.append(set(_lockfile_entries(self.root)) | _journal_ids(self.root))


# End to end


def test_a_bridged_build_succeeds_and_writes_the_lockfile_entries_of_a_local_build(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: the harness fixture with stable code identities, built with jobmon=True through FakeJobmon and the real node runner, reports SUCCESS for both steps, and its pytask.lock entries equal those that a local build of an identical fixture writes."""
    local = _stable_fixture(_mkdir(tmp_path / "local"))
    ran = dict.fromkeys(local.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(local.tasks, local.state)) == ran
    bridged = _stable_fixture(_mkdir(tmp_path / "bridged"))

    session = build(bridged.tasks, bridged.state, jobmon=True)

    assert outcomes(session) == ran
    assert _bound_names(fake_jobmon) == [list(bridged.steps)]
    entries = _lockfile_entries(bridged.state)
    assert sorted(entries) == sorted(bridged.steps)
    assert entries == _lockfile_entries(local.state)


def test_a_second_bridged_build_reports_skip_unchanged_and_binds_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: after a bridged build of the harness fixture with stable code identities, a second build with jobmon=True reports SKIP_UNCHANGED for both steps and FakeJobmon binds no second plan."""
    fx = _stable_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(fx.tasks, fx.state, jobmon=True)) == ran

    session = build(fx.tasks, fx.state, jobmon=True)

    assert outcomes(session) == dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)
    assert _bound_names(fake_jobmon) == [list(fx.steps)]


@pytest.mark.usefixtures("fake_jobmon")
def test_a_local_build_after_a_bridged_build_reports_skip_unchanged(tmp_path: Path) -> None:
    """After a bridged build of the harness fixture with stable code identities, a local build of the same tasks and root reports SKIP_UNCHANGED for both steps."""
    fx = _stable_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(fx.tasks, fx.state, jobmon=True)) == ran

    session = build(fx.tasks, fx.state)

    assert outcomes(session) == dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)


@pytest.mark.usefixtures("fake_jobmon")
def test_no_step_body_runs_in_the_build_process(tmp_path: Path) -> None:
    """In a bridged two-step chain whose targets record their process id, both steps report SUCCESS and no recorded process id is the test process's own."""
    registry = _registry(tmp_path)
    first = python_step(
        step_targets.record_pid, inputs={}, produces="first.parquet", registry=registry
    )
    second = python_step(
        step_targets.record_pid,
        inputs={"upstream": "first.parquet"},
        produces="second.parquet",
        registry=registry,
    )

    session = build([first, second], tmp_path / "state", jobmon=True)

    ran = dict.fromkeys(["first.parquet", "second.parquet"], TaskOutcome.SUCCESS)
    assert outcomes(session) == ran
    pids = pd.read_parquet(registry.path_for("second.parquet"))["pid"].tolist()
    assert len(pids) == 2
    assert os.getpid() not in pids


@pytest.mark.usefixtures("fake_jobmon")
def test_each_step_has_one_report_although_the_live_table_lists_it_twice(
    tmp_path: Path,
) -> None:
    """A bridged build of the harness fixture leaves exactly one report per step in session.execution_reports, and counting their outcomes gives two SUCCESS and nothing else, although planning already logged each submitted step once in pytask's live table."""
    fx = build_fixture(tmp_path)

    session = build(fx.tasks, fx.state, jobmon=True)

    names = [report.task.name for report in session.execution_reports]
    counts = Counter(report.outcome for report in session.execution_reports)
    assert sorted(names) == sorted(fx.steps)
    assert counts == Counter({TaskOutcome.SUCCESS: 2})


def test_steps_are_recorded_only_after_the_workflow_run_returns(tmp_path: Path) -> None:
    """A FakeJobmon whose run, after running every entry, looks at the build's root finds no pytask.lock entry and no lockfile journal entry for either step, and both steps have pytask.lock entries once the build returns."""
    fx = build_fixture(tmp_path)
    jobmon = _LooksAtTheRoot(root=fx.state)

    with jobmon:
        build(fx.tasks, fx.state, jobmon=True)

    [seen] = jobmon.seen
    assert seen.isdisjoint(fx.steps), seen
    assert sorted(_lockfile_entries(fx.state)) == sorted(fx.steps)


# A step that fails on its node


@pytest.mark.usefixtures("fake_jobmon")
def test_a_step_that_raises_on_its_node_is_a_fail_with_remote_task_failed(
    tmp_path: Path,
) -> None:
    """A bridged step whose target raises StepFailure reports FAIL, its exc_info[1] is a RemoteTaskFailed, and the message names the step and contains the node's exception line 'tests.bridge.step_targets.StepFailure: the step failed on purpose'."""
    report = _build_a_failing_step(tmp_path)

    error = _failure(report)
    assert isinstance(error, RemoteTaskFailed), repr(error)
    assert _FAILING in str(error)
    assert _EXCEPTION_LINE in str(error)


@pytest.mark.usefixtures("fake_jobmon")
def test_remote_task_failed_carries_the_node_exception_type_message_traceback_and_output(
    tmp_path: Path,
) -> None:
    """That step's RemoteTaskFailed has exc_type 'tests.bridge.step_targets.StepFailure', exc_message equal to step_targets.FAILURE_MESSAGE, a node_traceback whose last line is the exception line, a stdout that holds step_targets.STDOUT_BEFORE_FAILURE and a stderr that holds step_targets.STDERR_BEFORE_FAILURE; its exc_type, exc_message, stdout and stderr equal those fields of the step's result file, and its node_traceback equals the file's exc."""
    report = _build_a_failing_step(tmp_path)

    error = _remote_failure(report)
    result = _only_result(tmp_path / "state")
    assert error.exc_type == "tests.bridge.step_targets.StepFailure"
    assert error.exc_message == step_targets.FAILURE_MESSAGE
    assert error.node_traceback is not None
    assert error.node_traceback.strip().splitlines()[-1] == _EXCEPTION_LINE
    assert step_targets.STDOUT_BEFORE_FAILURE in error.stdout
    assert step_targets.STDERR_BEFORE_FAILURE in error.stderr
    assert (error.exc_type, error.exc_message) == (result["exc_type"], result["exc_message"])
    assert error.node_traceback == result["exc"]
    assert (error.stdout, error.stderr) == (result["stdout"], result["stderr"])


@pytest.mark.usefixtures("fake_jobmon")
def test_a_node_failure_report_holds_the_steps_stderr_as_a_call_section(
    tmp_path: Path,
) -> None:
    """That step's report.sections holds ('call', 'stderr', text) with step_targets.STDERR_BEFORE_FAILURE in text, so pytask renders it under 'Captured stderr during call'."""
    report = _build_a_failing_step(tmp_path)

    call_stderr = [
        text for when, key, text in report.sections if (when, key) == ("call", "stderr")
    ]
    assert any(step_targets.STDERR_BEFORE_FAILURE in text for text in call_stderr)
    assert "Captured stderr during call" in _rendered(report)


@pytest.mark.usefixtures("fake_jobmon")
def test_a_node_failure_report_has_no_traceback_from_the_build_process(
    tmp_path: Path,
) -> None:
    """That step's report has exc_info[2] None, and the rendered report shows the node's exception line but no frame from bridge/execute.py, pytask or pluggy."""
    report = _build_a_failing_step(tmp_path)

    assert report.exc_info is not None
    assert report.exc_info[2] is None
    rendered = _rendered(report)
    assert _EXCEPTION_LINE in rendered
    for internal in ("execute.py", "_pytask", "pluggy"):
        assert internal not in rendered, rendered


@pytest.mark.usefixtures("fake_jobmon")
def test_a_failed_result_without_a_node_traceback_is_a_fail_naming_only_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_record_available, given a result with success False and exc, exc_type and exc_message None, reports FAIL with a RemoteTaskFailed whose message names the step and holds no traceback, whose exc_type, exc_message and node_traceback are None, and whose stdout and stderr are the result's."""
    name = "untold.parquet"
    tasks = _constant_steps(_registry(tmp_path), [name])

    def record_failed(session: Session, stale: list[PTask], workdir: Path) -> list[PTask]:
        """Replace the task's result with a failure that has no exception, then record."""
        [task] = stale
        failed = RunResult(
            task=task.name,
            success=False,
            exc=None,
            exc_type=None,
            exc_message=None,
            stdout=_NODE_STDOUT,
            stderr=_NODE_STDERR,
            duration=0.0,
        )
        write_result(failed, workdir / f"{task.signature}{RESULT_SUFFIX}")
        return _record_available(session, stale, workdir)

    root = tmp_path / "state"
    session, unrecorded = _build_recording_with(tasks, root, monkeypatch, record_failed)

    error = _remote_failure(_report(session, name))
    message = str(error)
    assert name in message
    assert "Traceback" not in message, message
    # A missing traceback must not appear in the message as the text "None".
    assert "None" not in message, message
    assert (error.exc_type, error.exc_message, error.node_traceback) == (None, None, None)
    assert (error.stdout, error.stderr) == (_NODE_STDOUT, _NODE_STDERR)
    assert unrecorded == []


def test_a_step_that_failed_on_its_node_has_no_lockfile_entry_and_its_descendant_is_marked(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """After a bridged build of a chain whose first step fails on its node, pytask.lock has no entry for the failed step, and the pytask task of the step after it carries a skip_ancestor_failed marker."""
    registry = _registry(tmp_path)
    fails = python_step(
        step_targets.raise_step_failure,
        inputs={},
        produces="fails.parquet",
        registry=registry,
    )
    after = python_step(
        step_targets.stack,
        inputs={"upstream": "fails.parquet"},
        produces="after.parquet",
        registry=registry,
    )
    root = tmp_path / "state"

    session = build([fails, after], root, jobmon=True)

    assert _bound_names(fake_jobmon) == [["fails.parquet", "after.parquet"]]
    assert outcomes(session)["fails.parquet"] == TaskOutcome.FAIL
    assert "fails.parquet" not in _lockfile_entries(root)
    [after_task] = [task for task in session.tasks if task.name == "after.parquet"]
    assert "skip_ancestor_failed" in [mark.name for mark in after_task.markers]


def test_a_step_that_failed_on_its_node_is_planned_again_by_the_next_build(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stable code identities, after a bridged build in which one step failed on its node and an independent step succeeded, the next bridged build binds a plan holding the failed step and reports the other step SKIP_UNCHANGED."""
    targets = {
        "fails.parquet": step_targets.raise_step_failure,
        "succeeds.parquet": step_targets.constant,
    }
    tasks = _stable(_steps_without_inputs(_registry(tmp_path), targets))
    root = tmp_path / "state"
    assert outcomes(build(tasks, root, jobmon=True)) == {
        "fails.parquet": TaskOutcome.FAIL,
        "succeeds.parquet": TaskOutcome.SUCCESS,
    }

    session = build(tasks, root, jobmon=True)

    assert _bound_names(fake_jobmon)[1:] == [["fails.parquet"]]
    assert outcomes(session)["succeeds.parquet"] == TaskOutcome.SKIP_UNCHANGED


def test_every_result_is_recorded_after_a_failure_with_stop_after_first_failure(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stop_after_first_failure=True, when the first stale step in plan order fails on its node, an independent stale step after it in plan order still reports SUCCESS and gets a pytask.lock entry."""
    targets = {
        "a_fails.parquet": step_targets.raise_step_failure,
        "b_succeeds.parquet": step_targets.constant,
    }
    tasks = _steps_without_inputs(_registry(tmp_path), targets)
    root = tmp_path / "state"

    session = build(tasks, root, jobmon=True, stop_after_first_failure=True)

    assert _bound_names(fake_jobmon) == [list(targets)]
    reported = outcomes(session)
    assert reported["a_fails.parquet"] == TaskOutcome.FAIL
    assert reported["b_succeeds.parquet"] == TaskOutcome.SUCCESS
    assert "b_succeeds.parquet" in _lockfile_entries(root)


def test_remote_task_failed_keeps_its_message_and_each_keyword() -> None:
    """RemoteTaskFailed's str is its message and each keyword is kept as the attribute of the same name; a keyword left out gives None for exc_type, exc_message and node_traceback, and '' for stdout and stderr."""
    node_traceback = "Traceback (most recent call last):\npackage.module.StepError: it failed"
    full = RemoteTaskFailed(
        "Step 'x' failed on its compute node.",
        exc_type="package.module.StepError",
        exc_message="it failed",
        node_traceback=node_traceback,
        stdout="out",
        stderr="err",
    )
    bare = RemoteTaskFailed("Jobmon returned no result.")

    assert str(full) == "Step 'x' failed on its compute node."
    assert full.exc_type == "package.module.StepError"
    assert full.exc_message == "it failed"
    assert full.node_traceback == node_traceback
    assert (full.stdout, full.stderr) == ("out", "err")
    assert str(bare) == "Jobmon returned no result."
    assert (bare.exc_type, bare.exc_message, bare.node_traceback) == (None, None, None)
    assert (bare.stdout, bare.stderr) == ("", "")


# Products checked and hashed


@pytest.mark.usefixtures("fake_jobmon")
def test_a_command_step_that_exits_zero_without_its_product_is_a_fail_naming_the_file(
    tmp_path: Path,
) -> None:
    """A bridged command step whose command exits 0 but writes no declared product reports FAIL with a NodeNotFoundError whose message says 'The task did not produce the following files' and names the file, and it has no pytask.lock entry."""
    session, product = _build_a_step_without_its_product(tmp_path)

    error = _failure(_report(session, _NO_PRODUCT))
    assert isinstance(error, NodeNotFoundError), repr(error)
    message = _squashed(str(error))
    assert _squashed(_NOT_PRODUCED) in message
    assert product.name in message
    assert _NO_PRODUCT not in _lockfile_entries(tmp_path / "state")


@pytest.mark.usefixtures("fake_jobmon")
def test_a_missing_product_report_shows_only_pytasks_message(tmp_path: Path) -> None:
    """The rendered report of that step shows pytask's missing-product message and no frame from bridge/execute.py or pluggy."""
    session, _ = _build_a_step_without_its_product(tmp_path)

    rendered = _rendered(_report(session, _NO_PRODUCT))

    assert _squashed(_NOT_PRODUCED) in _squashed(rendered)
    for internal in ("execute.py", "pluggy"):
        assert internal not in rendered, rendered


@pytest.mark.usefixtures("fake_jobmon")
def test_editing_a_bridged_product_makes_preview_report_its_step_would_be_executed(
    tmp_path: Path,
) -> None:
    """After a bridged build of the harness fixture with stable code identities, appending a byte to the final product makes preview() report the final step WOULD_BE_EXECUTED and its upstream step SKIP_UNCHANGED."""
    fx = _stable_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(fx.tasks, fx.state, jobmon=True)) == ran
    upstream, final = fx.steps

    with Perturbed(fx.registry.path_for(final), b"\0"):
        plan = {step.name: step.outcome for step in preview(fx.tasks, fx.state)}

    assert plan == {
        upstream: TaskOutcome.SKIP_UNCHANGED,
        final: TaskOutcome.WOULD_BE_EXECUTED,
    }


# Captured output


@pytest.mark.usefixtures("fake_jobmon")
def test_a_successful_steps_output_is_kept_in_its_report_sections(tmp_path: Path) -> None:
    """A bridged step whose target writes to standard output and standard error and succeeds reports SUCCESS, and its report.sections holds ('call', 'stdout', ...) and ('call', 'stderr', ...) entries with those lines, as pytask keeps them for a local step without showing them."""
    task = python_step(
        step_targets.write_to_streams_and_descriptors,
        inputs={},
        produces="talks.parquet",
        registry=_registry(tmp_path),
    )

    session = build([task], tmp_path / "state", jobmon=True)

    report = _report(session, "talks.parquet")
    assert report.outcome == TaskOutcome.SUCCESS
    call = {key: text for when, key, text in report.sections if when == "call"}
    assert step_targets.STDOUT_PRINT in call.get("stdout", "")
    assert step_targets.FD1_OUTPUT in call.get("stdout", "")
    assert step_targets.STDERR_PRINT in call.get("stderr", "")
    assert step_targets.FD2_OUTPUT in call.get("stderr", "")


@pytest.mark.usefixtures("fake_jobmon")
def test_a_result_with_empty_output_adds_no_report_sections(tmp_path: Path) -> None:
    """A bridged step whose target writes nothing to standard output or standard error reports SUCCESS with no 'call' entry in report.sections."""
    tasks = _constant_steps(_registry(tmp_path), ["quiet.parquet"])

    session = build(tasks, tmp_path / "state", jobmon=True)

    report = _report(session, "quiet.parquet")
    assert report.outcome == TaskOutcome.SUCCESS
    assert [section for section in report.sections if section[0] == "call"] == []


# Code identities


def test_a_step_with_the_default_code_identity_is_submitted_on_every_bridged_build(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With the default always-stale code identity, a second bridged build of the unchanged harness fixture binds a plan with both steps again and reports both SUCCESS."""
    fx = build_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(fx.tasks, fx.state, jobmon=True)) == ran

    session = build(fx.tasks, fx.state, jobmon=True)

    assert outcomes(session) == ran
    assert _bound_names(fake_jobmon) == [list(fx.steps), list(fx.steps)]


# Recording what the run directory holds


@pytest.mark.usefixtures("fake_jobmon")
@pytest.mark.parametrize("reverse", [False, True], ids=["plan_order", "reverse_order"])
def test_results_are_recorded_in_the_order_of_the_stale_tasks(
    tmp_path: Path, reverse: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_record_available, given two stale tasks whose result files are both in the run directory, appends their reports to session.execution_reports in the order of the stale list, whatever the order of the directory listing."""
    tasks = _constant_steps(_registry(tmp_path), ["first.parquet", "second.parquet"])
    given: list[str] = []

    def record_in_order(session: Session, stale: list[PTask], workdir: Path) -> list[PTask]:
        """Record with the stale tasks in plan order, or in reverse plan order."""
        ordered = stale[::-1] if reverse else stale
        given.extend(task.name for task in ordered)
        return _record_available(session, ordered, workdir)

    root = tmp_path / "state"
    session, _ = _build_recording_with(tasks, root, monkeypatch, record_in_order)

    recorded = [(report.task.name, report.outcome) for report in session.execution_reports]
    assert len(given) == 2
    assert recorded == [(name, TaskOutcome.SUCCESS) for name in given]


@pytest.mark.usefixtures("fake_jobmon")
def test_a_result_still_being_written_is_not_recorded_and_its_task_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_record_available, given a run directory that holds only '<signature>.result.json.<host>.<pid>.tmp' for a stale task, appends no report for it and returns it."""
    tasks = _constant_steps(_registry(tmp_path), ["pending.parquet"])

    def record_pending(session: Session, stale: list[PTask], workdir: Path) -> list[PTask]:
        """Move each result back to the runner's temporary name, then record."""
        for task in stale:
            result = workdir / f"{task.signature}{RESULT_SUFFIX}"
            result.rename(_temporary_name(result))
        return _record_available(session, stale, workdir)

    root = tmp_path / "state"
    session, unrecorded = _build_recording_with(tasks, root, monkeypatch, record_pending)

    assert outcomes(session) == {}
    assert [task.name for task in unrecorded] == ["pending.parquet"]


@pytest.mark.usefixtures("fake_jobmon")
def test_record_available_returns_the_tasks_without_a_result_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_record_available, given a result file for the first of three stale tasks only, records the first and returns the other two in the order of the stale list."""
    tasks = _constant_steps(_registry(tmp_path), ["a.parquet", "b.parquet", "c.parquet"])

    def record_first_only(session: Session, stale: list[PTask], workdir: Path) -> list[PTask]:
        """Record the stale tasks in reverse plan order, with a result for the first only."""
        ordered = stale[::-1]
        for task in ordered[1:]:
            (workdir / f"{task.signature}{RESULT_SUFFIX}").unlink()
        return _record_available(session, ordered, workdir)

    root = tmp_path / "state"
    session, unrecorded = _build_recording_with(tasks, root, monkeypatch, record_first_only)

    assert outcomes(session) == {"c.parquet": TaskOutcome.SUCCESS}
    assert [task.name for task in unrecorded] == ["b.parquet", "a.parquet"]


@pytest.mark.usefixtures("fake_jobmon")
@pytest.mark.parametrize("how", _SPOILED)
def test_an_unreadable_result_file_is_a_fail_naming_the_file(
    tmp_path: Path, how: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_record_available, given for one stale task a result file that cannot be read as a node result (text that is not JSON, a JSON array, an object without exc_type, an object whose success is the string 'true' or whose stdout is null, or a directory at the result file's path) and a valid result for another, returns, reports the first FAIL with a RemoteTaskFailed whose message names the step and the file and whose exc_type, exc_message and node_traceback are None, and still records the second SUCCESS."""
    tasks = _constant_steps(_registry(tmp_path), ["broken.parquet", "fine.parquet"])
    broken: list[str] = []

    def record_broken(session: Session, stale: list[PTask], workdir: Path) -> list[PTask]:
        """Spoil the broken step's result as ``how`` names, then record it first."""
        # A crash on the broken result would leave every result after it unrecorded.
        ordered = sorted(stale, key=lambda task: task.name != "broken.parquet")
        result = workdir / f"{ordered[0].signature}{RESULT_SUFFIX}"
        _spoil(result, how)
        broken.append(result.name)
        return _record_available(session, ordered, workdir)

    root = tmp_path / "state"
    session, unrecorded = _build_recording_with(tasks, root, monkeypatch, record_broken)

    [file_name] = broken
    error = _remote_failure(_report(session, "broken.parquet"))
    assert "broken.parquet" in str(error)
    assert file_name in str(error)
    assert (error.exc_type, error.exc_message, error.node_traceback) == (None, None, None)
    assert _report(session, "fine.parquet").outcome == TaskOutcome.SUCCESS
    assert unrecorded == []


# Jobmon raising during the run


def test_when_the_workflow_run_raises_the_finished_steps_are_recorded_and_the_build_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: with FakeJobmon(error_after=1), a bridged build of a two-step chain records the first step SUCCESS with a pytask.lock entry, gives the second step no report, keeps a RuntimeError as the run's error, exits FAILED, and prints a message that names RuntimeError and says that the steps without a result were not recorded."""
    fx = build_fixture(tmp_path)
    first = fx.steps[0]
    jobmon = FakeJobmon(error_after=1)

    with jobmon:
        capsys.readouterr()
        session = build(fx.tasks, fx.state, jobmon=True)
        captured = capsys.readouterr()

    output = _squashed(captured.out + captured.err)
    [jobmon_run] = jobmon.runs
    assert isinstance(jobmon_run.error, RuntimeError), repr(jobmon_run.error)
    # TODO(MIC-7530): settlement gives the step without a result a report.
    assert outcomes(session) == {first: TaskOutcome.SUCCESS}
    assert first in _lockfile_entries(fx.state)
    assert session.exit_code == ExitCode.FAILED
    assert "RuntimeError" in output, output
    assert _squashed("the steps without a result were not recorded") in output, output
