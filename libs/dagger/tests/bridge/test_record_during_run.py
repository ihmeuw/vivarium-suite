"""Tests of recording each bridged step when it finishes, while the Jobmon run is still going.

The builds run through :class:`~vivarium.dagger.harness.FakeJobmon` or a subclass of it,
so they need no Jobmon server. A test that looks at the build's root during the run does
so from the fake's ``run``, in the build's main thread, while the bridge's recording
thread works beside it.
"""

import shlex
import signal
import subprocess
import threading
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any

import pandas as pd
import pytest
from pytask import Mark, PathNode, PTask, Session, TaskOutcome

from tests.bridge import step_targets
from tests.bridge.helpers import (
    LOCKFILE,
    LONG_RECORD_INTERVAL,
    PLAN_ORDER,
    SHORT_RECORD_INTERVAL,
    WAIT_SECONDS,
    A,
    B,
    C,
    D,
    bound_names,
    build_root,
    chain_build,
    journal_holds,
    lockfile_entries,
    make_dir,
    only_state,
    registry,
    remote_failure,
    report_counts,
    report_for,
    squashed,
    stable,
    wait_until,
)
from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry
from vivarium.dagger.bridge import execute
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.dagger.bridge.runner import RESULT_SUFFIX, read_result
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon, Fixture, build_fixture

_SETTLE_SECONDS = 0.3
"""How many seconds a test gives the main thread to get from one statement to the next."""

_FIRST, _SECOND = "first.parquet", "second.parquet"
"""The steps of the chain that :func:`_two_step_chain` returns, in plan order."""

_FLAKY = "fails_on_its_first_attempt"
"""Name of the command step that fails on its first attempt and succeeds on its second."""

_NO_PRODUCT = "exits_zero_without_its_product"
"""Name of the command step that exits 0 without writing its output."""

_BELOW = "below_the_step_without_its_product"
"""Name of the command step that declares the output of :data:`_NO_PRODUCT` as its input."""

_THREAD_ERROR = "the recording thread failed on purpose"
"""Message of the error that a test raises in the recording thread."""

_ANCESTOR_FAILED = "Previous task 'gone.parquet' failed."
"""The reason of a skip_ancestor_failed mark, in the words that pytask gives it."""

_RECORDING_THREAD = "dagger-bridge-recording"
"""The name that the bridge gives its recording thread."""

_CANNOT_START = "can't start new thread"
"""Message of the RuntimeError that Python raises when it cannot start a thread."""

_STATE_ERROR = "the state of the product could not be read, on purpose"
"""Message of the OSError that a test raises from a product node's ``state``."""

_BUILD_SECONDS = 120.0
"""The most seconds that a test gives a bridged build that it runs in a second thread."""

_NO_PASS = f"The recording thread made no further pass within {WAIT_SECONDS} seconds."
"""What a test says when it waited in vain for a pass of the recording thread."""

_NOT_CALLED = f"The recording thread did not call _record_ready in {WAIT_SECONDS} seconds."
"""What a test says when it waited in vain for the recording thread's first pass."""

_SETTLED = (TaskOutcome.FAIL, TaskOutcome.SKIP_PREVIOUS_FAILED)
"""The outcomes with which the bridge settles a step that was not recorded."""

_ENDINGS: Mapping[str, dict[str, Any]] = {
    "finished": {},
    "sigterm": {"interrupt_after": 1},
    "keyboard_interrupt": {"interrupt_after": 1, "interrupt_with": "keyboard_interrupt"},
    "jobmon_error": {"error_after": 1},
}
"""The FakeJobmon options with which a build of a -> b -> c plus d ends in each way."""

_RecordReady = Callable[[Session, Sequence[PTask], Path, set[str]], None]
"""The signature of the bridge's ``_record_ready``."""

_DuringTheRun = Callable[[JobmonRun], None]
"""What a test does in the build's main thread while the bridge waits for the fake's run."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


@pytest.fixture(autouse=True)
def _short_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the recording thread's interval to a few hundredths of a second."""
    monkeypatch.setattr(execute, "_RECORD_INTERVAL_SECONDS", SHORT_RECORD_INTERVAL)


@pytest.fixture(autouse=True)
def _sigterm_kept() -> Iterator[None]:
    """Restore SIGTERM's handler after each test, whatever the test left installed."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def _run_entry(entry: PlanEntry) -> str:
    """Run the entry's command as FakeJobmon does, and return its Jobmon task status."""
    finished = subprocess.run(["/bin/sh", "-c", entry.command], check=False)
    return "D" if finished.returncode == 0 else "F"


def _wait_for_the_journal(root: Path, names: Collection[str]) -> None:
    """Wait until ``root``'s lockfile journal holds ``names``; fail if the limit passes."""
    assert wait_until(lambda: journal_holds(root, names)), (
        f"The lockfile journal did not hold {sorted(names)} within {WAIT_SECONDS} seconds, "
        "while the fake's run was still going."
    )


def _entry_counts(root: Path) -> dict[str, int]:
    """Return how many entries ``root``'s pytask.lock holds for each task id."""
    lockfile = tomllib.loads((root / LOCKFILE).read_text(encoding="utf-8"))
    return dict(Counter(entry["id"] for entry in lockfile.get("task", [])))


def _bridged(tasks: Sequence[Task], root: Path, jobmon: FakeJobmon) -> Session:
    """Build ``tasks`` under ``root`` with jobmon=True through ``jobmon``."""
    with jobmon:
        return build(tasks, root, jobmon=True)


def _stable_fixture(directory: Path) -> Fixture:
    """Return the harness fixture under ``directory`` with stable code identities."""
    fx = build_fixture(directory)
    return replace(fx, tasks=stable(fx.tasks))


def _rewrite(table: Path) -> None:
    """Replace the harness fixture's root-input table with one that holds other numbers."""
    pd.DataFrame({"n": [4, 5, 6]}).to_parquet(table)


def _constant_steps(artifacts: FileRegistry, names: Sequence[str]) -> list[Task]:
    """Return one independent python step per name, each writing a constant frame."""
    return [
        python_step(step_targets.constant, inputs={}, outputs=name, registry=artifacts)
        for name in names
    ]


def _two_step_chain(artifacts: FileRegistry) -> list[Task]:
    """Return a chain of two python steps, ``first.parquet`` then ``second.parquet``."""
    first = python_step(step_targets.constant, inputs={}, outputs=_FIRST, registry=artifacts)
    second = python_step(
        step_targets.stack, inputs={"upstream": _FIRST}, outputs=_SECOND, registry=artifacts
    )
    return [first, second]


def _failing_step(artifacts: FileRegistry, name: str) -> Task:
    """Return a python step called ``name`` whose target raises StepFailure on its node."""
    return python_step(
        step_targets.raise_step_failure_reading, inputs={}, outputs=name, registry=artifacts
    )


def _fails_on_its_first_attempt(directory: Path) -> Task:
    """Return a command step that fails on its first run and succeeds on its second."""
    attempted = shlex.quote(str(directory / "attempted"))
    product = directory / "written_on_the_second_attempt.txt"
    command = (
        f"if [ -e {attempted} ]; then echo written > {shlex.quote(str(product))}; "
        f"else touch {attempted}; exit 1; fi"
    )
    return Task(
        name=_FLAKY,
        run=command,
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=None,
    )


def _chain_whose_first_step_writes_nothing(directory: Path) -> list[Task]:
    """Return two command steps: one exits 0 without its output, the next takes it as input.

    The second step's command does not read that input, so its node reports success.
    """
    never_written = directory / "never_written.txt"
    written = directory / "written.txt"
    first = Task(
        name=_NO_PRODUCT,
        run="true",
        inputs={},
        outputs={"product": file_node(never_written)},
        resources={},
        env=None,
    )
    second = Task(
        name=_BELOW,
        run=f"echo written > {shlex.quote(str(written))}",
        inputs={"source": file_node(never_written)},
        outputs={"written": file_node(written)},
        resources={},
        env=None,
    )
    return [first, second]


@dataclass
class _Scripted(FakeJobmon):
    """Stand in for Jobmon, and call ``before`` and ``after`` around FakeJobmon's own run.

    Both are called in the build's main thread while the bridge's recording thread works
    beside it. What they raise is kept and raised when this object's ``with`` block ends,
    because pytask would report it as a failure of the build. The block also fails if the
    build never called ``run``, so a check made only during the run is never left out
    unseen.
    """

    before: _DuringTheRun | None = None
    """Called with the run before FakeJobmon's own run starts."""
    after: _DuringTheRun | None = None
    """Called with the run after FakeJobmon's own run has returned."""
    raised: list[Exception] = field(default_factory=list, init=False)
    """What the functions called during a run raised, in the order raised."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Restore the adapter's functions, then raise what was kept during the run."""
        super().__exit__(exc_type, exc, tb)
        if exc is not None:
            return
        if self.raised:
            raise self.raised[0]
        assert self.timeouts, "The bridge did not call the fake's run."

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Call ``before``, run as FakeJobmon does, then call ``after``."""
        self._call(self.before, jobmon_run)
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)
        self._call(self.after, jobmon_run)

    def _call(self, function: _DuringTheRun | None, jobmon_run: JobmonRun) -> None:
        """Call ``function`` with the run if there is one, and keep what it raises."""
        if function is None:
            return
        try:
            function(jobmon_run)
        except Exception as error:
            self.raised.append(error)


@dataclass
class _RunsAFailedEntryAgain(_Scripted):
    """Stand in for Jobmon, and run each entry that failed a second time, as Jobmon does.

    ``after_failure`` is called as ``before`` and ``after`` are. An entry that waited for
    a failed entry is not run.
    """

    after_failure: _DuringTheRun | None = None
    """Called with the run after each attempt of an entry that failed."""
    run_again: list[str] = field(default_factory=list, init=False)
    """The names of the entries that were run a second time, in the order run."""

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Run as FakeJobmon does, then run each failed entry once more and set its status."""
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)
        statuses = jobmon_run.task_statuses
        for entry in jobmon_run.plan:
            if statuses.get(entry.signature) != "F":
                continue
            self._call(self.after_failure, jobmon_run)
            self.run_again.append(entry.name)
            statuses[entry.signature] = _run_entry(entry)
            if statuses[entry.signature] == "F":
                self._call(self.after_failure, jobmon_run)
        all_done = all(status == "D" for status in statuses.values())
        jobmon_run.status = "D" if all_done else "E"


class _Passes:
    """Stand in for ``_record_ready`` in the recording thread, and count the thread's passes.

    With ``by_hand``, a pass of the thread records nothing, so only the calls that a test
    makes through :meth:`record_ready` record a step. The thread makes its first pass one
    recording interval after the workflow starts, so what a pass was given is known only
    after :meth:`whole_pass` has returned true.
    """

    def __init__(self, bridge_record_ready: _RecordReady, *, by_hand: bool) -> None:
        self._bridge_record_ready = bridge_record_ready
        self._by_hand = by_hand
        self._given: tuple[Session, Sequence[PTask], Path, set[str]] | None = None
        self.started = 0
        self.finished = 0

    def __call__(
        self, session: Session, submitted: Sequence[PTask], workdir: Path, recorded: set[str]
    ) -> None:
        """Count one pass of the thread, and record in it unless the test records by hand."""
        self._given = (session, submitted, workdir, recorded)
        self.started += 1
        try:
            if not self._by_hand:
                self._bridge_record_ready(session, submitted, workdir, recorded)
        finally:
            self.finished += 1

    def _arguments(self) -> tuple[Session, Sequence[PTask], Path, set[str]]:
        """Return what the thread's latest pass was given."""
        assert self._given is not None, "The recording thread has not called _record_ready."
        return self._given

    @property
    def submitted(self) -> Sequence[PTask]:
        """The submitted tasks that the thread gives ``_record_ready``, in plan order."""
        return self._arguments()[1]

    @property
    def recorded(self) -> set[str]:
        """The set of recorded signatures that the thread gives ``_record_ready``."""
        return self._arguments()[3]

    def whole_pass(self) -> bool:
        """Wait for a pass that starts after this call; return whether it ended."""
        wanted = self.started + 1
        return wait_until(lambda: self.finished >= wanted)

    def signature(self, name: str) -> str:
        """Return the pytask signature of the submitted step called ``name``."""
        [signature] = [task.signature for task in self.submitted if task.name == name]
        return signature

    def result_path(self, name: str) -> Path:
        """Return the result file, in the run directory, of the submitted step ``name``."""
        return self._arguments()[2] / f"{self.signature(name)}{RESULT_SUFFIX}"

    def outcomes_of(self, name: str) -> list[TaskOutcome]:
        """Return the outcome of each report that the session holds now for step ``name``."""
        reports = list(self._arguments()[0].execution_reports)
        return [report.outcome for report in reports if report.task.name == name]

    def record_ready(
        self, submitted: Sequence[PTask] | None = None
    ) -> list[tuple[str, TaskOutcome]]:
        """Call the bridge's ``_record_ready`` once; return the reports that it appended.

        The call gets what the thread's passes get, with ``submitted`` in place of the
        submitted tasks if it is given. Each report is returned as its step's name and its
        outcome, in the order appended.
        """
        session, given, workdir, recorded = self._arguments()
        before = len(session.execution_reports)
        self._bridge_record_ready(
            session, given if submitted is None else submitted, workdir, recorded
        )
        appended = session.execution_reports[before:]
        return [(report.task.name, report.outcome) for report in appended]


@contextmanager
def _watched_passes(*, by_hand: bool = False) -> Iterator[_Passes]:
    """Put a ``_Passes`` in place of the bridge's ``_record_ready`` while the block runs."""
    passes = _Passes(execute._record_ready, by_hand=by_hand)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(execute, "_record_ready", passes)
        yield passes


def _build_recording_by_hand(
    tasks: Sequence[Task], root: Path, scenario: Callable[[_Passes], None]
) -> Session:
    """Build ``tasks`` with jobmon=True, and call ``scenario`` once every entry has run.

    The recording thread records nothing in this build. ``scenario`` is called in the
    build's main thread before the fake's run returns, so the build's session is still
    open, and it calls ``_record_ready`` itself through the object that it is given. What
    it raises is raised here after the build.
    """
    with _watched_passes(by_hand=True) as passes:

        def call_the_scenario(jobmon_run: JobmonRun) -> None:
            """Wait until the thread has been given its arguments, then call the scenario."""
            assert passes.whole_pass(), _NO_PASS
            scenario(passes)

        return _bridged(tasks, root, _Scripted(after=call_the_scenario))


# Recording during the run


def test_an_input_edited_after_its_step_was_recorded_runs_the_step_in_the_next_build(
    tmp_path: Path,
) -> None:
    """When a root input of the first step is edited from the fake's run after that step's journal entry exists and before the run returns, the entry in pytask.lock holds the input's state from before the edit, and the next bridged build with stable code identities binds a plan that contains the first step."""
    unedited = _stable_fixture(make_dir(tmp_path / "unedited"))
    edited_first = _stable_fixture(make_dir(tmp_path / "edited_first"))
    fx = _stable_fixture(make_dir(tmp_path / "edited_during_the_run"))
    first = fx.steps[0]
    build(unedited.tasks, unedited.state)
    _rewrite(edited_first.inputs["raw"])
    build(edited_first.tasks, edited_first.state)
    before_the_edit = lockfile_entries(unedited.state)[first]
    # A step's entry holds the state of its input, so it tells the two inputs apart.
    assert lockfile_entries(edited_first.state)[first] != before_the_edit

    def edit_once_recorded(jobmon_run: JobmonRun) -> None:
        """Edit the first step's root input once the journal holds the step."""
        _wait_for_the_journal(fx.state, [first])
        _rewrite(fx.inputs["raw"])

    _bridged(fx.tasks, fx.state, _Scripted(after=edit_once_recorded))
    recorded_entry = lockfile_entries(fx.state)[first]
    next_build = FakeJobmon()
    _bridged(fx.tasks, fx.state, next_build)

    assert recorded_entry == before_the_edit
    [plan] = bound_names(next_build)
    assert first in plan


def test_every_submitted_step_has_exactly_one_report_and_one_lockfile_entry(
    tmp_path: Path,
) -> None:
    """A bridged build of the chain and the independent step, with a recording interval short enough that steps are recorded during the run, ends with exactly one SUCCESS report and one pytask.lock entry for each submitted step."""
    root = build_root(tmp_path)

    def wait_for_every_step(jobmon_run: JobmonRun) -> None:
        """Wait until the journal holds every step, so each is recorded during the run."""
        _wait_for_the_journal(root, PLAN_ORDER)

    session = chain_build(tmp_path, _Scripted(after=wait_for_every_step))

    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert _entry_counts(root) == dict.fromkeys(PLAN_ORDER, 1)


def test_a_step_recorded_during_the_run_keeps_its_node_output_in_its_report(
    tmp_path: Path,
) -> None:
    """A step whose node writes to standard output and to standard error, recorded by the thread while the run is still going, ends with exactly one report whose sections hold that standard output and that standard error."""
    name = "talks.parquet"
    root = build_root(tmp_path)
    task = python_step(
        step_targets.write_to_streams_and_descriptors,
        inputs={},
        outputs=name,
        registry=registry(tmp_path),
    )

    def wait_for_the_step(jobmon_run: JobmonRun) -> None:
        """Wait until the journal holds the step, so the thread is what recorded it."""
        _wait_for_the_journal(root, [name])

    session = _bridged([task], root, _Scripted(after=wait_for_the_step))

    assert report_counts(session) == {name: 1}
    report = report_for(session, name)
    assert report.outcome == TaskOutcome.SUCCESS
    call = {key: text for when, key, text in report.sections if when == "call"}
    assert step_targets.STDOUT_PRINT in call.get("stdout", "")
    assert step_targets.FD1_OUTPUT in call.get("stdout", "")
    assert step_targets.STDERR_PRINT in call.get("stderr", "")
    assert step_targets.FD2_OUTPUT in call.get("stderr", "")


def test_a_step_whose_unchanged_upstream_was_not_submitted_is_recorded_during_the_run(
    tmp_path: Path,
) -> None:
    """In a bridged build with stable code identities in which only the second step of the harness fixture's two-step chain is stale, so its upstream is not submitted, the second step's journal entry exists while the fake's run is still going."""
    fx = build_fixture(tmp_path)
    first, second = fx.steps
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(stable(fx.tasks), fx.state)) == ran
    assert not journal_holds(fx.state, [second])

    def wait_for_the_second_step(jobmon_run: JobmonRun) -> None:
        """Wait until the journal holds the second step, before the run returns."""
        _wait_for_the_journal(fx.state, [second])

    jobmon = _Scripted(after=wait_for_the_second_step)
    session = _bridged(stable(fx.tasks, changed={second}), fx.state, jobmon)

    assert bound_names(jobmon) == [[second]]
    assert outcomes(session) == {
        first: TaskOutcome.SKIP_UNCHANGED,
        second: TaskOutcome.SUCCESS,
    }


def test_a_result_that_appears_after_the_last_pass_is_recorded_once_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a recording interval longer than the whole run, so the thread makes no pass, every step still ends with exactly one SUCCESS report and one pytask.lock entry."""
    monkeypatch.setattr(execute, "_RECORD_INTERVAL_SECONDS", LONG_RECORD_INTERVAL)
    root = build_root(tmp_path)
    started = time.monotonic()

    with _watched_passes() as passes:
        session = chain_build(tmp_path, FakeJobmon())

    elapsed = time.monotonic() - started
    assert elapsed < LONG_RECORD_INTERVAL, "The build outlasted the recording interval."
    assert passes.started == 0
    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert _entry_counts(root) == dict.fromkeys(PLAN_ORDER, 1)


# A step that fails on its node


def test_a_failure_result_is_not_recorded_or_reported_during_the_run(tmp_path: Path) -> None:
    """While the fake's run is still going after an entry whose result reports a failure, and after at least one more pass of the recording thread, the session has no report for that step and the journal has no entry for it."""
    root = build_root(tmp_path)
    during_the_run: list[tuple[list[TaskOutcome], bool]] = []

    with _watched_passes() as passes:

        def look_after_a_pass(jobmon_run: JobmonRun) -> None:
            """Note b's reports and whether the journal holds b, after one more pass."""
            assert passes.whole_pass(), _NO_PASS
            assert read_result(passes.result_path(B))["success"] is False
            during_the_run.append((passes.outcomes_of(B), journal_holds(root, [B])))

        session = chain_build(tmp_path, _Scripted(after=look_after_a_pass), failing=(B,))

    assert during_the_run == [([], False)]
    assert report_for(session, B).outcome == TaskOutcome.FAIL


def test_a_step_that_succeeds_on_its_second_attempt_has_one_entry_and_one_success_report(
    tmp_path: Path,
) -> None:
    """Through a FakeJobmon subclass that runs a failed entry a second time, a step that fails on its first attempt and succeeds on its second ends with one pytask.lock entry and exactly one report, which is SUCCESS."""
    root = build_root(tmp_path)
    task = _fails_on_its_first_attempt(tmp_path)

    with _watched_passes() as passes:

        def let_the_thread_see_the_failure(jobmon_run: JobmonRun) -> None:
            """Wait for one more pass while the step's result reports its first attempt."""
            assert passes.whole_pass(), _NO_PASS
            assert read_result(passes.result_path(_FLAKY))["success"] is False

        jobmon = _RunsAFailedEntryAgain(after_failure=let_the_thread_see_the_failure)
        session = _bridged([task], root, jobmon)

    assert jobmon.run_again == [_FLAKY]
    assert report_counts(session) == {_FLAKY: 1}
    assert report_for(session, _FLAKY).outcome == TaskOutcome.SUCCESS
    assert _entry_counts(root) == {_FLAKY: 1}


def test_a_step_whose_attempts_all_fail_has_no_entry_and_one_fail_report(
    tmp_path: Path,
) -> None:
    """A step that fails on every attempt has no pytask.lock entry and no journal entry, and exactly one report, which is FAIL with a RemoteTaskFailed that holds the node's error."""
    root = build_root(tmp_path)
    name = "fails.parquet"
    task = _failing_step(registry(tmp_path), name)
    in_the_journal: list[bool] = []

    with _watched_passes() as passes:

        def look_after_a_pass(jobmon_run: JobmonRun) -> None:
            """Note whether the journal holds the step, after one more pass."""
            assert passes.whole_pass(), _NO_PASS
            in_the_journal.append(journal_holds(root, [name]))

        jobmon = _RunsAFailedEntryAgain(after_failure=look_after_a_pass)
        session = _bridged([task], root, jobmon)

    assert jobmon.run_again == [name]
    assert in_the_journal == [False, False]
    assert name not in lockfile_entries(root)
    assert report_counts(session) == {name: 1}
    error = remote_failure(report_for(session, name))
    assert error.exc_type == "tests.bridge.step_targets.StepFailure"
    assert error.exc_message == step_targets.FAILURE_MESSAGE


# A build that is stopped during the run


def test_sigterm_during_the_run_keeps_the_recorded_steps_and_settles_the_rest(
    tmp_path: Path,
) -> None:
    """A bridged build whose fake sends SIGTERM after the first entry was run and recorded returns with the first step as SUCCESS with a pytask.lock entry, every other submitted step with exactly one report that is FAIL or SKIP_PREVIOUS_FAILED, and a state file with status 'interrupted'."""
    root = build_root(tmp_path)

    def run_the_first_entry(jobmon_run: JobmonRun) -> None:
        """Run the first entry and wait until the journal holds its step."""
        first = jobmon_run.plan[0]
        assert _run_entry(first) == "D"
        _wait_for_the_journal(root, [first.name])

    # With interrupt_after=0, FakeJobmon's own run runs no entry and sends SIGTERM at once.
    jobmon = _Scripted(interrupt_after=0, before=run_the_first_entry)

    session = chain_build(tmp_path, jobmon)

    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert report_for(session, A).outcome == TaskOutcome.SUCCESS
    assert set(lockfile_entries(root)) == {A}
    for name in (D, B, C):
        assert report_for(session, name).outcome in _SETTLED, name
    assert only_state(root).status == "interrupted"


def test_a_jobmon_error_during_the_run_keeps_the_recorded_steps_and_settles_the_rest(
    tmp_path: Path,
) -> None:
    """A bridged build whose fake stops with an error after the first entry (error_after=1) returns with the first step as SUCCESS with a pytask.lock entry and every other submitted step with exactly one report that is FAIL or SKIP_PREVIOUS_FAILED."""
    root = build_root(tmp_path)

    def wait_for_the_first_step(jobmon_run: JobmonRun) -> None:
        """Wait until the journal holds the first step, before the run returns its error."""
        _wait_for_the_journal(root, [A])

    jobmon = _Scripted(error_after=1, after=wait_for_the_first_step)

    session = chain_build(tmp_path, jobmon)

    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert report_for(session, A).outcome == TaskOutcome.SUCCESS
    assert set(lockfile_entries(root)) == {A}
    for name in (D, B, C):
        assert report_for(session, name).outcome in _SETTLED, name


@pytest.mark.parametrize(
    "ending", ["finished", "sigterm", "keyboard_interrupt", "jobmon_error"]
)
def test_the_recording_thread_has_ended_when_the_build_returns(
    tmp_path: Path, ending: str
) -> None:
    """After a bridged build that finishes, is interrupted by SIGTERM or by a KeyboardInterrupt, or whose Jobmon run stops with an error, no thread that the build started is alive."""
    before = set(threading.enumerate())
    during: set[threading.Thread] = set()

    def note_the_threads(jobmon_run: JobmonRun) -> None:
        """Note the threads that are alive when the workflow starts to run."""
        during.update(threading.enumerate())

    chain_build(tmp_path, _Scripted(before=note_the_threads, **_ENDINGS[ending]))

    started = during - before
    assert started, "The build had started no thread when the workflow began to run."
    since = (set(threading.enumerate()) | started) - before
    assert [thread.name for thread in since if thread.is_alive()] == []


def test_the_block_does_not_exit_before_the_thread_ends_when_an_interrupt_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _record_ready is replaced by a function that blocks until released, and the main thread gets a KeyboardInterrupt while _recording_during_the_run waits for the thread on exit, the block has not exited while the function is still blocked, and after the function is released the block exits and raises the KeyboardInterrupt."""
    fx = build_fixture(tmp_path)
    built = build(fx.tasks, fx.state)
    run_directory = make_dir(tmp_path / "run")
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    exited_while_blocked: list[bool] = []

    def blocked(
        session: Session, submitted: Sequence[PTask], workdir: Path, recorded: set[str]
    ) -> None:
        """Stay in the recording thread until the test releases it or the limit passes."""
        entered.set()
        release.wait(timeout=WAIT_SECONDS)

    def interrupt(signum: int, frame: FrameType | None) -> None:
        """Raise KeyboardInterrupt in the main thread, unless the block has exited."""
        if not exited.is_set():
            raise KeyboardInterrupt

    def interrupt_then_release() -> None:
        """Interrupt the main thread, note whether the block then exits, and release."""
        # The main thread needs a moment to get from the block's body to its wait.
        if not exited.wait(timeout=_SETTLE_SECONDS):
            signal.pthread_kill(main_thread, signal.SIGINT)
        exited_while_blocked.append(exited.wait(timeout=_SETTLE_SECONDS))
        release.set()

    helper = threading.Thread(target=interrupt_then_release)
    monkeypatch.setattr(execute, "_record_ready", blocked)
    recording = execute._recording_during_the_run(built, built.tasks, run_directory, set())
    # Python raises no KeyboardInterrupt of its own where SIGINT is ignored, as in a
    # process that a shell started in the background.
    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            try:
                with recording:
                    assert entered.wait(timeout=WAIT_SECONDS), _NOT_CALLED
                    helper.start()
            finally:
                released_before_the_exit = release.is_set()
                exited.set()
    finally:
        release.set()
        if helper.is_alive():
            helper.join(timeout=WAIT_SECONDS)
        signal.signal(signal.SIGINT, previous)

    assert exited_while_blocked == [False]
    assert released_before_the_exit


def test_an_error_in_the_recording_thread_is_a_warning_and_every_step_is_still_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With _record_ready replaced by a function that raises, a bridged build still ends with one SUCCESS report and one pytask.lock entry for each step, and it prints a warning that names the error."""
    root = build_root(tmp_path)
    called = threading.Event()

    def broken(
        session: Session, submitted: Sequence[PTask], workdir: Path, recorded: set[str]
    ) -> None:
        """Fail in the recording thread."""
        called.set()
        raise RuntimeError(_THREAD_ERROR)

    def wait_for_the_error(jobmon_run: JobmonRun) -> None:
        """Wait until the thread has made the pass that fails, before the run returns."""
        assert called.wait(timeout=WAIT_SECONDS), _NOT_CALLED

    monkeypatch.setattr(execute, "_record_ready", broken)
    capsys.readouterr()

    session = chain_build(tmp_path, _Scripted(after=wait_for_the_error))

    captured = capsys.readouterr()
    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert _entry_counts(root) == dict.fromkeys(PLAN_ORDER, 1)
    printed = captured.out + captured.err
    assert "RuntimeError" in squashed(printed), printed
    assert squashed(_THREAD_ERROR) in squashed(printed), printed


def test_a_build_whose_recording_thread_cannot_start_records_every_step_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With threading.Thread.start made to raise RuntimeError("can't start new thread") during a bridged build, the build ends within a time limit, every step has one SUCCESS report and one pytask.lock entry, and the printed warning names the error."""
    root = build_root(tmp_path)
    tasks = _two_step_chain(registry(tmp_path))
    start = threading.Thread.start
    refused: list[str] = []
    built: list[Session] = []
    raised: list[BaseException] = []

    def start_unless_recording(thread: threading.Thread) -> None:
        """Start ``thread``, unless it is the bridge's recording thread."""
        if thread.name != _RECORDING_THREAD:
            start(thread)
            return
        refused.append(thread.name)
        raise RuntimeError(_CANNOT_START)

    def build_and_keep() -> None:
        """Build the chain through FakeJobmon, and keep its session or what it raised."""
        try:
            built.append(_bridged(tasks, root, FakeJobmon()))
        except BaseException as error:
            raised.append(error)

    # The build runs in a second thread, so that a build that never ends fails the test.
    builder = threading.Thread(target=build_and_keep, daemon=True)
    monkeypatch.setattr(threading.Thread, "start", start_unless_recording)
    capsys.readouterr()

    builder.start()
    builder.join(timeout=_BUILD_SECONDS)

    captured = capsys.readouterr()
    assert not builder.is_alive(), f"The build did not end within {_BUILD_SECONDS} seconds."
    assert raised == []
    assert refused, f"The build started no thread called {_RECORDING_THREAD!r}."
    [session] = built
    names = [_FIRST, _SECOND]
    assert outcomes(session) == dict.fromkeys(names, TaskOutcome.SUCCESS)
    assert report_counts(session) == dict.fromkeys(names, 1)
    assert _entry_counts(root) == dict.fromkeys(names, 1)
    printed = captured.out + captured.err
    assert "RuntimeError" in squashed(printed), printed
    assert squashed(_CANNOT_START) in squashed(printed), printed


# _record_ready


def test_record_ready_records_a_success_and_adds_it_to_recorded(tmp_path: Path) -> None:
    """_record_ready, given a submitted task whose result file reports success and whose product exists, appends one SUCCESS report for it, writes its journal entry, and adds its signature to the recorded set."""
    name = "only.parquet"
    artifacts = registry(tmp_path)
    root = build_root(tmp_path)

    def scenario(given: _Passes) -> None:
        """Record the one step, whose node succeeded and wrote its product."""
        assert read_result(given.result_path(name))["success"] is True
        assert artifacts.path_for(name).exists()
        assert not journal_holds(root, [name])
        assert given.recorded == set()

        appended = given.record_ready()

        assert appended == [(name, TaskOutcome.SUCCESS)]
        assert journal_holds(root, [name])
        assert given.recorded == {given.signature(name)}

    _build_recording_by_hand(_constant_steps(artifacts, [name]), root, scenario)


def test_record_ready_records_in_plan_order_and_never_twice(tmp_path: Path) -> None:
    """_record_ready, called twice with two independent submitted tasks that both have success results, appends their reports in the order of the submitted list on the first call and appends nothing on the second."""
    names = [_FIRST, _SECOND]
    order: list[str] = []
    calls: list[list[tuple[str, TaskOutcome]]] = []

    def scenario(given: _Passes) -> None:
        """Call _record_ready twice with the two steps in reverse plan order."""
        # Neither step waits for the other, so the reverse of the plan is also an order
        # of the two, and it is not the order of their names or of the build's own list.
        submitted = list(reversed(given.submitted))
        order.extend(task.name for task in submitted)
        calls.append(given.record_ready(submitted))
        calls.append(given.record_ready(submitted))

    tasks = _constant_steps(registry(tmp_path), names)
    session = _build_recording_by_hand(tasks, build_root(tmp_path), scenario)

    first_call, second_call = calls
    assert sorted(order) == sorted(names)
    assert first_call == [(name, TaskOutcome.SUCCESS) for name in order]
    assert second_call == []
    assert report_counts(session) == dict.fromkeys(names, 1)


def test_record_ready_skips_a_result_that_cannot_be_read_and_records_it_later(
    tmp_path: Path,
) -> None:
    """_record_ready leaves a task whose result file holds half of a JSON document with no report and out of the recorded set, and a second call after the whole result is written records it as SUCCESS."""
    name = "only.parquet"

    def scenario(given: _Passes) -> None:
        """Record with half of the step's result in its file, then with the whole of it."""
        result = given.result_path(name)
        assert read_result(result)["success"] is True
        whole = result.read_text(encoding="utf-8")
        result.write_text(whole[: len(whole) // 2], encoding="utf-8")

        assert given.record_ready() == []
        assert given.recorded == set()

        result.write_text(whole, encoding="utf-8")

        assert given.record_ready() == [(name, TaskOutcome.SUCCESS)]
        assert given.recorded == {given.signature(name)}

    tasks = _constant_steps(registry(tmp_path), [name])
    _build_recording_by_hand(tasks, build_root(tmp_path), scenario)


def test_record_ready_leaves_a_failure_result(tmp_path: Path) -> None:
    """_record_ready leaves a task whose result file reports a failure with no report and out of the recorded set."""
    name = "fails.parquet"

    def scenario(given: _Passes) -> None:
        """Call _record_ready for the step whose node failed."""
        assert read_result(given.result_path(name))["success"] is False

        assert given.record_ready() == []
        assert given.recorded == set()

    tasks = [_failing_step(registry(tmp_path), name)]
    _build_recording_by_hand(tasks, build_root(tmp_path), scenario)


def test_record_ready_leaves_a_step_that_pytask_marked_skip_ancestor_failed(
    tmp_path: Path,
) -> None:
    """_record_ready leaves a submitted task that carries pytask's skip_ancestor_failed mark, whose result file reports success and whose product exists, with no report and out of the recorded set."""
    name = "only.parquet"
    artifacts = registry(tmp_path)

    def scenario(given: _Passes) -> None:
        """Mark the one step as pytask marks a step after a failed step, then record."""
        [task] = given.submitted
        assert read_result(given.result_path(name))["success"] is True
        assert artifacts.path_for(name).exists()
        # pytask adds this mark, with a reason, to each step after a failed step.
        task.markers.append(Mark("skip_ancestor_failed", (), {"reason": _ANCESTOR_FAILED}))

        assert given.record_ready() == []
        assert given.recorded == set()

    tasks = _constant_steps(artifacts, [name])
    _build_recording_by_hand(tasks, build_root(tmp_path), scenario)


def test_record_ready_leaves_a_success_whose_product_is_missing_and_records_it_once_present(
    tmp_path: Path,
) -> None:
    """_record_ready leaves a task whose result reports success while its product file does not exist with no report and out of the recorded set, and a second call after the product is written records it as SUCCESS."""
    name = "only.parquet"
    artifacts = registry(tmp_path)

    def scenario(given: _Passes) -> None:
        """Record with the step's product moved away, then with it back in its place."""
        product = artifacts.path_for(name)
        set_aside = product.with_name("set_aside.parquet")
        assert read_result(given.result_path(name))["success"] is True
        product.rename(set_aside)

        assert given.record_ready() == []
        assert given.recorded == set()

        set_aside.rename(product)

        assert given.record_ready() == [(name, TaskOutcome.SUCCESS)]
        assert given.recorded == {given.signature(name)}

    tasks = _constant_steps(artifacts, [name])
    _build_recording_by_hand(tasks, build_root(tmp_path), scenario)


def test_record_ready_leaves_a_step_whose_product_state_raises_oserror_and_records_it_later(
    tmp_path: Path,
) -> None:
    """_record_ready, given a submitted task whose result reports success and one of whose product nodes raises OSError from state(), leaves it with no report and out of the recorded set, and a later call after state() stops raising records it as SUCCESS."""
    name = "only.parquet"
    artifacts = registry(tmp_path)
    product = artifacts.path_for(name)
    state = PathNode.state
    refused: list[str] = []

    def state_unless_the_product(node: PathNode) -> str | None:
        """Return the node's state, or raise OSError if the node is the step's product."""
        if node.path != product:
            return state(node)
        refused.append(str(node.path))
        raise OSError(_STATE_ERROR)

    def scenario(given: _Passes) -> None:
        """Record while the product's state cannot be read, then once it can."""
        assert read_result(given.result_path(name))["success"] is True
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(PathNode, "state", state_unless_the_product)

            assert given.record_ready() == []
            assert given.recorded == set()
            assert refused, "_record_ready did not read the state of the step's product."

        assert given.record_ready() == [(name, TaskOutcome.SUCCESS)]
        assert given.recorded == {given.signature(name)}

    tasks = _constant_steps(artifacts, [name])
    _build_recording_by_hand(tasks, build_root(tmp_path), scenario)


def test_a_success_whose_product_never_appears_is_one_fail_report_at_the_end_of_the_run(
    tmp_path: Path,
) -> None:
    """In a bridged build, a step whose node reports success without writing its product has no report while the run is going and ends with exactly one report, which is FAIL, and no pytask.lock entry."""
    root = build_root(tmp_path)
    task, _ = _chain_whose_first_step_writes_nothing(tmp_path)
    during_the_run: list[list[TaskOutcome]] = []

    with _watched_passes() as passes:

        def look_after_a_pass(jobmon_run: JobmonRun) -> None:
            """Note the step's reports after one more pass, with its result written."""
            assert passes.whole_pass(), _NO_PASS
            assert read_result(passes.result_path(_NO_PRODUCT))["success"] is True
            during_the_run.append(passes.outcomes_of(_NO_PRODUCT))

        session = _bridged([task], root, _Scripted(after=look_after_a_pass))

    assert during_the_run == [[]]
    assert report_counts(session) == {_NO_PRODUCT: 1}
    assert report_for(session, _NO_PRODUCT).outcome == TaskOutcome.FAIL
    assert _NO_PRODUCT not in lockfile_entries(root)


def test_record_ready_does_not_record_a_step_below_a_step_that_is_not_recorded(
    tmp_path: Path,
) -> None:
    """_record_ready, given a two-step chain in which both result files report success and the first step's product is missing, records neither step, and after the first step's product is written one further call records both in plan order."""
    artifacts = registry(tmp_path)

    def scenario(given: _Passes) -> None:
        """Record with the first step's product moved away, then with it back."""
        product = artifacts.path_for(_FIRST)
        set_aside = product.with_name("set_aside.parquet")
        for name in (_FIRST, _SECOND):
            assert read_result(given.result_path(name))["success"] is True
        product.rename(set_aside)

        assert given.record_ready() == []
        assert given.recorded == set()

        set_aside.rename(product)

        assert given.record_ready() == [
            (_FIRST, TaskOutcome.SUCCESS),
            (_SECOND, TaskOutcome.SUCCESS),
        ]
        assert given.recorded == {given.signature(_FIRST), given.signature(_SECOND)}

    _build_recording_by_hand(_two_step_chain(artifacts), build_root(tmp_path), scenario)


def test_a_step_below_a_step_that_failed_at_recording_is_skipped_and_has_no_entry(
    tmp_path: Path,
) -> None:
    """In a bridged build of a two-step chain in which the first step's node reports success without writing its product and the second step's node reports success, the first step ends as FAIL and the second as SKIP_PREVIOUS_FAILED, and neither has a pytask.lock entry."""
    root = build_root(tmp_path)
    tasks = _chain_whose_first_step_writes_nothing(tmp_path)

    with _watched_passes() as passes:

        def let_the_thread_see_both_results(jobmon_run: JobmonRun) -> None:
            """Wait for one more pass, with both results reporting success."""
            assert passes.whole_pass(), _NO_PASS
            for name in (_NO_PRODUCT, _BELOW):
                assert read_result(passes.result_path(name))["success"] is True

        session = _bridged(tasks, root, _Scripted(after=let_the_thread_see_both_results))

    assert outcomes(session) == {
        _NO_PRODUCT: TaskOutcome.FAIL,
        _BELOW: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    assert report_counts(session) == {_NO_PRODUCT: 1, _BELOW: 1}
    entries = lockfile_entries(root)
    assert _NO_PRODUCT not in entries
    assert _BELOW not in entries
