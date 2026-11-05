"""Tests for interrupting a bridged build (``bridge.execute``).

The builds here run through ``FakeJobmon``, which interrupts them with a real SIGTERM to
the test process or with a ``KeyboardInterrupt``. A test that would end its own process
runs the build in a child Python process instead. Helpers are in ``tests.bridge.helpers``.
"""

import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, Literal

import pytest
from pytask import ExitCode, Session, TaskOutcome

from tests.bridge import step_targets
from tests.bridge.helpers import (
    JOURNAL,
    LOCKFILE,
    PLAN_ORDER,
    WORKFLOW_ID,
    A,
    B,
    C,
    D,
    bound_names,
    build_root,
    chain_build,
    journal_ids,
    lockfile_entries,
    only_state,
    recorded_pids,
    registry,
    remote_failure,
    report_counts,
    report_for,
    squashed,
    stable,
    state_files,
)
from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.dagger.bridge.execute import (
    INTERRUPTED_EXIT_CODE,
    BridgeInterrupted,
    _sigterm_raises,
)
from vivarium.dagger.bridge.state import (
    RUN_STATE_KEY,
    RUNS_DIR,
    STATE_FILE,
    RunState,
    read_run_state,
    write_run_state,
)
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon

_INTERRUPTED_OUTCOMES = {
    A: TaskOutcome.SUCCESS,
    D: TaskOutcome.SUCCESS,
    B: TaskOutcome.FAIL,
    C: TaskOutcome.SKIP_PREVIOUS_FAILED,
}
"""The outcomes of a -> b -> c plus d when the run is interrupted after a and d ran."""

_TWO = ("first.parquet", "second.parquet")
"""The steps of the two independent steps that :func:`_two_independent` returns."""

_EARLIER_ID = 9753102
"""The workflow id of an earlier run, which no other output of a build contains."""

_LATER_ID = 8642097
"""The workflow id of a later run, which no other output of a build contains."""

_CHILD_TIMEOUT = 300
"""The most seconds that a child process or a thread of these tests may take."""

_Handler = Callable[[int, FrameType | None], Any]
"""A signal handler that Python calls."""

_TERM_WHILE_RECORDING = """
import os
import signal
import sys
from pathlib import Path

from pytask import hookimpl

from tests.bridge import step_targets
from tests.bridge.helpers import registry, stable
from vivarium.dagger.backend.build import build
from vivarium.dagger.bridge import plugin
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon


class TermWhileRecording:
    # The hook is firstresult and pytask's live display answers it first with True.
    @hookimpl(tryfirst=True)
    def pytask_execute_task_log_start(self, session, task):
        if not session.config["dry_run"] and task.name == "second.parquet":
            os.kill(os.getpid(), signal.SIGTERM)


bridge_post_parse = plugin.pytask_post_parse


@hookimpl(trylast=True)
def pytask_post_parse(config):
    bridge_post_parse(config)
    config["pm"].register(TermWhileRecording())


plugin.pytask_post_parse = pytask_post_parse
tmp_path = Path(sys.argv[1])
artifacts = registry(tmp_path)
tasks = stable(
    [
        python_step(step_targets.constant, inputs={}, produces=name, registry=artifacts)
        for name in ("first.parquet", "second.parquet")
    ]
)
with FakeJobmon():
    build(tasks, tmp_path / "state", jobmon=True)
print("survived", flush=True)
"""
"""A child build of two independent steps that sends itself SIGTERM while the bridge
records the second step."""

_SIGTERM_AFTER_A_WRAPPED_RUN = """
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tests.bridge.helpers import chain_and_independent, registry
from vivarium.dagger.backend.build import build
from vivarium.dagger.harness import FakeJobmon


@dataclass
class WrappedRun(FakeJobmon):
    def run(self, jobmon_run, *, seconds_until_timeout=None):
        previous = signal.getsignal(signal.SIGTERM)

        def wrapper(signum, frame):
            try:
                if callable(previous):
                    previous(signum, frame)
            except Exception:
                pass

        signal.signal(signal.SIGTERM, wrapper)
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)


tmp_path = Path(sys.argv[1])
with WrappedRun():
    session = build(
        chain_and_independent(registry(tmp_path)), tmp_path / "state", jobmon=True
    )
print("built", int(session.exit_code), flush=True)
os.kill(os.getpid(), signal.SIGTERM)
time.sleep(10)
print("survived", flush=True)
"""
"""A child build whose run is wrapped by a SIGTERM handler that stays installed, and
which sends itself SIGTERM after the build."""

_SIGTERM_AFTER_A_WRAPPED_RUN_UNDER = """
import json
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.bridge.helpers import chain_and_independent, registry
from vivarium.dagger.backend.build import build
from vivarium.dagger.harness import FakeJobmon

calls = []
wrappers = []


def earlier(signum, frame):
    calls.append(int(signum))


@dataclass
class WrappedRun(FakeJobmon):
    def run(self, jobmon_run, *, seconds_until_timeout=None):
        previous = signal.getsignal(signal.SIGTERM)

        def wrapper(signum, frame):
            try:
                if callable(previous):
                    previous(signum, frame)
            except Exception:
                pass

        wrappers.append(wrapper)
        signal.signal(signal.SIGTERM, wrapper)
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)


tmp_path = Path(sys.argv[1])
signal.signal(signal.SIGTERM, earlier if sys.argv[2] == "handler" else signal.SIG_IGN)
with WrappedRun():
    session = build(
        chain_and_independent(registry(tmp_path)), tmp_path / "state", jobmon=True
    )
print("built", int(session.exit_code), flush=True)
print("wrapper kept", signal.getsignal(signal.SIGTERM) is wrappers[0], flush=True)
signal.raise_signal(signal.SIGTERM)
print("calls", json.dumps(calls), flush=True)
print("survived", flush=True)
"""
"""A child build whose run is wrapped by a SIGTERM handler that stays installed, under an
earlier Python handler ("handler") or SIG_IGN ("ignored"), as its second argument names,
and which sends itself SIGTERM after the build."""

_SIGKILL_DURING_THE_RUN = """
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.bridge.helpers import WORKFLOW_ID, chain_and_independent, registry, stable
from vivarium.dagger.backend.build import build
from vivarium.dagger.harness import FakeJobmon


@dataclass
class KilledAfterOne(FakeJobmon):
    def run(self, jobmon_run, *, seconds_until_timeout=None):
        subprocess.run(["/bin/sh", "-c", jobmon_run.plan[0].command], check=False)
        os.kill(os.getpid(), signal.SIGKILL)


tmp_path = Path(sys.argv[1])
tasks = stable(chain_and_independent(registry(tmp_path)))
with KilledAfterOne(workflow_id=WORKFLOW_ID):
    build(tasks, tmp_path / "state", jobmon=True)
print("survived", flush=True)
"""
"""A child build of a -> b -> c plus d with stable code identities whose run sends
itself SIGKILL after it runs the first entry."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


@pytest.fixture(autouse=True)
def _sigterm_kept() -> Iterator[None]:
    """Restore SIGTERM's handler after each test, whatever the test left installed."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def _send_sigterm() -> None:
    """Send SIGTERM to this thread, but only while Python handles or ignores it."""
    handler = signal.getsignal(signal.SIGTERM)
    # Under the default disposition the signal would end the test run itself.
    assert handler is signal.SIG_IGN or callable(handler), handler
    signal.raise_signal(signal.SIGTERM)


def _recording_handler(calls: list[int]) -> _Handler:
    """Return a SIGTERM handler that appends each signal it gets to ``calls``."""

    def handler(signum: int, frame: FrameType | None) -> None:
        """Note the signal."""
        calls.append(signum)

    return handler


@contextmanager
def _sigterm_handler(handler: _Handler) -> Iterator[None]:
    """Install ``handler`` for SIGTERM in the block, and restore the earlier one after."""
    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _interrupted_build(
    tmp_path: Path,
    interrupt_with: Literal["sigterm", "keyboard_interrupt"] = "sigterm",
    workflow_id: int = WORKFLOW_ID,
) -> Session:
    """Build a -> b -> c plus d, interrupted in the way named once a and d have run."""
    jobmon = FakeJobmon(
        interrupt_after=2, interrupt_with=interrupt_with, workflow_id=workflow_id
    )
    return chain_build(tmp_path, jobmon)


def _two_independent(tmp_path: Path) -> list[Task]:
    """Return two independent steps with stable code identities, as the child builds them."""
    artifacts = registry(tmp_path)
    return stable(
        [
            python_step(step_targets.constant, inputs={}, produces=name, registry=artifacts)
            for name in _TWO
        ]
    )


def _run_child(code: str, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a child Python process, with arguments ``tmp_path`` and ``args``.

    The child gets this process's environment, so its own node runners can import
    ``tests.bridge.step_targets``.
    """
    return subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=_CHILD_TIMEOUT,
        check=False,
    )


def _in_a_thread(target: Callable[[], None]) -> list[BaseException]:
    """Run ``target`` in a new thread and wait for it; return what it raised."""
    raised: list[BaseException] = []

    def run() -> None:
        """Call ``target`` and keep anything that it raises."""
        try:
            target()
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=_CHILD_TIMEOUT)
    assert not thread.is_alive()
    return raised


@dataclass
class _SigtermInBind(FakeJobmon):
    """Stand in for Jobmon, and send SIGTERM while binding, before bind returns."""

    def bind(
        self,
        plan: Sequence[PlanEntry],
        *,
        cluster: str,
        default_resources: Mapping[str, Any],
        max_attempts: int,
        resource_scales: Mapping[str, float] | None,
        workflow_name: str,
    ) -> JobmonRun:
        """Bind as FakeJobmon does, then send SIGTERM before returning the run."""
        jobmon_run = super().bind(
            plan,
            cluster=cluster,
            default_resources=default_resources,
            max_attempts=max_attempts,
            resource_scales=resource_scales,
            workflow_name=workflow_name,
        )
        _send_sigterm()
        return jobmon_run


@dataclass
class _SigtermTwice(FakeJobmon):
    """Stand in for Jobmon, and send SIGTERM twice: once, then again while it propagates."""

    second_ignored: bool = field(default=False, init=False)
    """Whether the second SIGTERM raised nothing."""

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Send SIGTERM, and send it again while the first one's error propagates."""
        try:
            _send_sigterm()
        except BridgeInterrupted:
            _send_sigterm()
            self.second_ignored = True
            raise


@dataclass
class _WrappedRun(FakeJobmon):
    """Stand in for Jobmon, and put a SIGTERM handler in front of the bridge's, as Jobmon's
    logging setup does: it calls the earlier handler and ignores an Exception from it."""

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Install the wrapping handler, then run as FakeJobmon does."""
        previous = signal.getsignal(signal.SIGTERM)

        def wrapper(signum: int, frame: FrameType | None) -> None:
            """Call the earlier handler, ignoring an Exception from it."""
            try:
                if callable(previous):
                    previous(signum, frame)
            except Exception:
                pass

        signal.signal(signal.SIGTERM, wrapper)
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)


@dataclass
class _OutputAtBind(FakeJobmon):
    """Stand in for Jobmon, and keep what the build has printed when it binds."""

    capsys: pytest.CaptureFixture[str] | None = None
    """The test's output capture, which this object reads at each bind."""
    printed: list[str] = field(default_factory=list, init=False)
    """What the build printed before each bind."""

    def bind(
        self,
        plan: Sequence[PlanEntry],
        *,
        cluster: str,
        default_resources: Mapping[str, Any],
        max_attempts: int,
        resource_scales: Mapping[str, float] | None,
        workflow_name: str,
    ) -> JobmonRun:
        """Keep the output so far, then bind as FakeJobmon does."""
        if self.capsys is not None:
            captured = self.capsys.readouterr()
            self.printed.append(captured.out + captured.err)
        return super().bind(
            plan,
            cluster=cluster,
            default_resources=default_resources,
            max_attempts=max_attempts,
            resource_scales=resource_scales,
            workflow_name=workflow_name,
        )


# BridgeInterrupted


def test_bridge_interrupted_is_a_system_exit_with_exit_code_143() -> None:
    """BridgeInterrupted is a subclass of SystemExit and not of Exception, and an instance raised by the bridge's SIGTERM handler has code INTERRUPTED_EXIT_CODE, 143."""
    assert issubclass(BridgeInterrupted, SystemExit)
    assert not issubclass(BridgeInterrupted, Exception)
    assert INTERRUPTED_EXIT_CODE == 143

    with pytest.raises(BridgeInterrupted) as info:
        with _sigterm_raises():
            _send_sigterm()

    assert info.value.code == INTERRUPTED_EXIT_CODE


# SIGTERM during the run


def test_sigterm_during_the_run_records_settles_and_writes_the_lockfile(
    tmp_path: Path,
) -> None:
    """End to end: with FakeJobmon(interrupt_after=2, workflow_id=4242), a bridged build of a -> b -> c plus d returns normally; a and d are SUCCESS with pytask.lock entries, b is FAIL, c is SKIP_PREVIOUS_FAILED, each step has one report, pytask.lock.journal is gone, and no product records the test process's pid."""
    root = build_root(tmp_path)

    session = _interrupted_build(tmp_path)

    assert outcomes(session) == _INTERRUPTED_OUTCOMES
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert set(lockfile_entries(root)) == {A, D}
    assert not (root / JOURNAL).exists()
    assert os.getpid() not in recorded_pids(registry(tmp_path), PLAN_ORDER)


def test_sigterm_gives_each_step_without_a_result_a_fail_saying_the_build_was_interrupted(
    tmp_path: Path,
) -> None:
    """After that interrupted build, b's RemoteTaskFailed message names b, says that the build was interrupted (not that it was interrupted before the workflow was bound), and gives task status 'unknown', workflow 4242 and workflow status 'unknown'."""
    session = _interrupted_build(tmp_path)

    error = remote_failure(report_for(session, B))
    message = str(error)
    assert B in message, message
    assert "the build was interrupted" in message.lower(), message
    # The clause for an interrupt before bind says "before the Jobmon workflow was bound".
    assert "bound" not in message, message
    assert message.count("unknown") >= 2, message
    assert str(WORKFLOW_ID) in message, message
    assert (error.exc_type, error.exc_message, error.node_traceback) == (None, None, None)


def test_an_interrupted_build_ends_with_pytasks_failed_exit_code(
    tmp_path: Path,
) -> None:
    """After that interrupted build, which returned without raising, session.exit_code is ExitCode.FAILED; a build interrupted only after every step wrote its result has every step SUCCESS and session.exit_code ExitCode.OK."""
    early = tmp_path / "early"
    late = tmp_path / "late"
    every_step_ran = FakeJobmon(interrupt_after=len(PLAN_ORDER), workflow_id=WORKFLOW_ID)

    interrupted = _interrupted_build(early)
    interrupted_late = chain_build(late, every_step_ran)

    assert outcomes(interrupted) == _INTERRUPTED_OUTCOMES
    assert interrupted.exit_code == ExitCode.FAILED
    assert outcomes(interrupted_late) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert interrupted_late.exit_code == ExitCode.OK
    assert set(lockfile_entries(build_root(late))) == set(PLAN_ORDER)
    state = only_state(build_root(late))
    assert state.status == "interrupted"
    assert state.pending == ()


def test_sigterm_leaves_an_interrupted_state_with_the_workflow_id_and_pending_steps(
    tmp_path: Path,
) -> None:
    """After that interrupted build, the state file and session.config[RUN_STATE_KEY] have status 'interrupted', workflow id 4242, workflow_status None, error None and pending (b, c)."""
    session = _interrupted_build(tmp_path)

    state = only_state(build_root(tmp_path))
    assert state.status == "interrupted"
    assert state.workflow_id == WORKFLOW_ID
    assert state.workflow_status is None
    assert state.error is None
    assert state.pending == (B, C)
    assert session.config[RUN_STATE_KEY] == state


def test_the_sigterm_handler_is_the_bridges_during_the_run_and_restored_after(
    tmp_path: Path,
) -> None:
    """With a sentinel Python handler installed for SIGTERM before the build, FakeJobmon(interrupt_after=1).sigterm_handlers holds a handler other than the sentinel, and after the build signal.getsignal(SIGTERM) is the sentinel again; the same holds for a build that is not interrupted."""
    calls: list[int] = []
    sentinel = _recording_handler(calls)
    interrupted = FakeJobmon(interrupt_after=1)
    finished = FakeJobmon()

    with _sigterm_handler(sentinel):
        chain_build(tmp_path / "interrupted", interrupted)
        after_interrupt = signal.getsignal(signal.SIGTERM)
        chain_build(tmp_path / "finished", finished)
        after_finish = signal.getsignal(signal.SIGTERM)

    for jobmon in (interrupted, finished):
        [during] = jobmon.sigterm_handlers
        assert during is not sentinel
        assert callable(during), during
    assert after_interrupt is sentinel
    assert after_finish is sentinel
    assert calls == []
    assert only_state(tmp_path / "interrupted" / "state").status == "interrupted"


def test_keyboard_interrupt_during_the_run_has_the_same_outcome_as_sigterm(
    tmp_path: Path,
) -> None:
    """With FakeJobmon(interrupt_after=2, interrupt_with='keyboard_interrupt'), a bridged build of a -> b -> c plus d returns normally with the same outcomes, pytask.lock entries and 'interrupted' state as the SIGTERM build."""
    by_signal = tmp_path / "sigterm"
    by_keyboard = tmp_path / "keyboard_interrupt"

    signalled = _interrupted_build(by_signal)
    session = _interrupted_build(by_keyboard, interrupt_with="keyboard_interrupt")

    assert outcomes(session) == outcomes(signalled) == _INTERRUPTED_OUTCOMES
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    for directory in (by_signal, by_keyboard):
        assert set(lockfile_entries(build_root(directory))) == {A, D}
        state = only_state(build_root(directory))
        assert state.status == "interrupted"
        assert state.workflow_id == WORKFLOW_ID
        assert state.pending == (B, C)


def test_the_next_build_after_an_interrupt_runs_exactly_the_pending_steps(
    tmp_path: Path,
) -> None:
    """With stable code identities, after the interrupted build, the next bridged build binds a plan of exactly b and c and reports a and d SKIP_UNCHANGED."""
    chain_build(tmp_path, FakeJobmon(interrupt_after=2), stable_ids=True)
    jobmon = FakeJobmon()

    session = chain_build(tmp_path, jobmon, stable_ids=True)

    assert bound_names(jobmon) == [[B, C]]
    reported = outcomes(session)
    assert reported[A] == TaskOutcome.SKIP_UNCHANGED
    assert reported[D] == TaskOutcome.SKIP_UNCHANGED


# SIGTERM at other moments


def test_sigterm_during_bind_fails_every_step_and_leaves_a_state_without_a_workflow_id(
    tmp_path: Path,
) -> None:
    """With a FakeJobmon subclass whose bind sends SIGTERM to the process before it returns, the build returns normally, every submitted step is FAIL or SKIP_PREVIOUS_FAILED, each FAIL's message says the build was interrupted before the Jobmon workflow was bound, and the state file is 'interrupted' with workflow_id None and every submitted step pending."""
    jobmon = _SigtermInBind(workflow_id=WORKFLOW_ID)

    session = chain_build(tmp_path, jobmon)

    assert bound_names(jobmon) == [list(PLAN_ORDER)]
    assert outcomes(session) == {
        A: TaskOutcome.FAIL,
        D: TaskOutcome.FAIL,
        B: TaskOutcome.SKIP_PREVIOUS_FAILED,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    for name in (A, D):
        message = str(remote_failure(report_for(session, name)))
        assert name in message, message
        assert "interrupted" in message.lower(), message
        assert "bound" in message, message
    state = only_state(build_root(tmp_path))
    assert state.status == "interrupted"
    assert state.workflow_id is None
    assert state.pending == PLAN_ORDER
    assert not any(registry(tmp_path).path_for(name).exists() for name in PLAN_ORDER)


def test_a_second_sigterm_while_the_first_unwinds_is_ignored(tmp_path: Path) -> None:
    """With a FakeJobmon subclass whose run sends SIGTERM and sends a second SIGTERM while the first one's BridgeInterrupted propagates, the build returns normally, each step has exactly one report, and the state file is 'interrupted'."""
    jobmon = _SigtermTwice(workflow_id=WORKFLOW_ID)

    session = chain_build(tmp_path, jobmon)

    assert jobmon.second_ignored
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert only_state(build_root(tmp_path)).status == "interrupted"


def test_sigterm_after_the_run_ends_the_process_and_the_journal_keeps_what_was_recorded(
    tmp_path: Path,
) -> None:
    """In a child Python process, a bridged build of two independent steps whose recording of the second step sends SIGTERM to the process ends the process with return code -15; the root has no pytask.lock, its pytask.lock.journal holds the first step, and the next build reports the first step SKIP_UNCHANGED (stable code identities)."""
    root = build_root(tmp_path)

    child = _run_child(_TERM_WHILE_RECORDING, tmp_path)

    assert "survived" not in child.stdout.splitlines(), child.stdout
    assert child.returncode == -signal.SIGTERM, (child.returncode, child.stderr[-4000:])
    assert not (root / LOCKFILE).exists()
    assert journal_ids(root) == {"first.parquet"}
    with FakeJobmon():
        session = build(_two_independent(tmp_path), root, jobmon=True)
    assert outcomes(session)["first.parquet"] == TaskOutcome.SKIP_UNCHANGED


def test_a_handler_installed_over_the_bridges_still_lets_sigterm_reach_it(
    tmp_path: Path,
) -> None:
    """With a FakeJobmon subclass whose run first installs a SIGTERM handler that calls the previous handler inside 'except Exception: pass', as Jobmon's logging setup does, and then sends SIGTERM, the build is interrupted and settled as without that handler."""
    jobmon = _WrappedRun(interrupt_after=2, workflow_id=WORKFLOW_ID)

    session = chain_build(tmp_path, jobmon)

    assert outcomes(session) == _INTERRUPTED_OUTCOMES
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert set(lockfile_entries(build_root(tmp_path))) == {A, D}
    state = only_state(build_root(tmp_path))
    assert state.status == "interrupted"
    assert state.workflow_id == WORKFLOW_ID
    assert state.pending == (B, C)


def test_after_a_wrapped_run_a_later_sigterm_gets_the_earlier_handling(
    tmp_path: Path,
) -> None:
    """In a child Python process whose run is wrapped by such a handler, which stays installed after the build, a SIGTERM sent after the build ends the process with return code -15."""
    child = _run_child(_SIGTERM_AFTER_A_WRAPPED_RUN, tmp_path)

    printed = child.stdout.splitlines()
    assert "built 0" in printed, (child.stdout, child.stderr[-4000:])
    assert "survived" not in printed, child.stdout
    assert child.returncode == -signal.SIGTERM, (child.returncode, child.stderr[-4000:])


@pytest.mark.parametrize("earlier", ["handler", "ignored"])
def test_after_a_wrapped_run_the_wrapper_stays_and_reaches_the_earlier_handling(
    tmp_path: Path, earlier: str
) -> None:
    """In a child Python process whose run is wrapped by such a handler, under an earlier Python handler or SIG_IGN, the wrapper is still SIGTERM's handler after the build, and a SIGTERM sent then calls the earlier handler once, or is ignored, and the process goes on."""
    child = _run_child(_SIGTERM_AFTER_A_WRAPPED_RUN_UNDER, tmp_path, earlier)

    printed = child.stdout.splitlines()
    assert child.returncode == 0, (child.returncode, child.stderr[-4000:])
    assert "built 0" in printed, (child.stdout, child.stderr[-4000:])
    assert "wrapper kept True" in printed, child.stdout
    calls: list[int] = [int(signal.SIGTERM)] if earlier == "handler" else []
    assert f"calls {json.dumps(calls)}" in printed, child.stdout
    assert "survived" in printed, child.stdout


def test_off_the_main_thread_no_handler_is_installed_and_the_build_settles(
    tmp_path: Path,
) -> None:
    """A bridged build run in a thread other than the main thread, through a FakeJobmon whose run fails one step, raises no ValueError from signal.signal, leaves SIGTERM's handler unchanged during the run and after, and reports every step once."""
    calls: list[int] = []
    sentinel = _recording_handler(calls)
    jobmon = FakeJobmon()
    sessions: list[Session] = []

    with _sigterm_handler(sentinel):
        raised = _in_a_thread(
            lambda: sessions.append(chain_build(tmp_path, jobmon, failing=(B,)))
        )
        after = signal.getsignal(signal.SIGTERM)

    assert raised == []
    assert jobmon.sigterm_handlers == [sentinel]
    assert after is sentinel
    [session] = sessions
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert outcomes(session) == {
        A: TaskOutcome.SUCCESS,
        D: TaskOutcome.SUCCESS,
        B: TaskOutcome.FAIL,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }


def test_sigterm_raises_is_a_no_op_off_the_main_thread() -> None:
    """Entering and leaving _sigterm_raises in a thread other than the main thread leaves signal.getsignal(SIGTERM) unchanged and raises nothing."""
    calls: list[int] = []
    sentinel = _recording_handler(calls)
    seen: list[object] = []

    def enter_and_leave() -> None:
        """Note SIGTERM's handler inside the block."""
        with _sigterm_raises():
            seen.append(signal.getsignal(signal.SIGTERM))

    with _sigterm_handler(sentinel):
        raised = _in_a_thread(enter_and_leave)
        after = signal.getsignal(signal.SIGTERM)

    assert raised == []
    assert seen == [sentinel]
    assert after is sentinel


# SIGKILL


def test_a_sigkilled_build_leaves_a_running_state_and_records_nothing(tmp_path: Path) -> None:
    """In a child Python process, a bridged build through a FakeJobmon subclass whose run sends SIGKILL to the process after one entry ends with return code -9; the state file is 'running' with the fake's workflow id and every submitted step pending, pytask.lock has no entry, and the next bridged build in this process binds the same plan as the killed build."""
    root = build_root(tmp_path)

    child = _run_child(_SIGKILL_DURING_THE_RUN, tmp_path)

    assert child.returncode == -signal.SIGKILL, (child.returncode, child.stderr[-4000:])
    state = only_state(root)
    assert state.status == "running"
    assert state.workflow_id == WORKFLOW_ID
    assert state.pending == PLAN_ORDER
    assert lockfile_entries(root) == {}
    assert journal_ids(root) == set()
    jobmon = FakeJobmon()
    chain_build(tmp_path, jobmon, stable_ids=True)
    assert bound_names(jobmon) == [list(state.pending)]


# The warning about an unfinished run


@pytest.mark.parametrize("earlier", ["interrupted", "running"])
def test_the_next_build_warns_naming_the_workflow_id_of_an_unfinished_run(
    tmp_path: Path, earlier: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the latest state under the root is 'interrupted' (after an interrupted build) or 'running' (written by hand, as a killed build leaves it), the next bridged build prints, before it binds, a warning that names that run's workflow id and run directory and says its jobs may still be running."""
    root = build_root(tmp_path)
    if earlier == "interrupted":
        _interrupted_build(tmp_path, workflow_id=_EARLIER_ID)
        [path] = state_files(root)
        earlier_run = path.parent
    else:
        earlier_run = root / RUNS_DIR / "20261104-120000-abcdef"
        earlier_run.mkdir(parents=True)
        write_run_state(
            RunState(
                workdir=earlier_run,
                status="running",
                workflow_id=_EARLIER_ID,
                workflow_status=None,
                error=None,
                pending=PLAN_ORDER,
            )
        )
    assert read_run_state(earlier_run / STATE_FILE).status == earlier
    jobmon = _OutputAtBind(workflow_id=_LATER_ID, capsys=capsys)
    capsys.readouterr()

    chain_build(tmp_path, jobmon)

    [before_bind] = jobmon.printed
    printed = squashed(before_bind)
    assert str(_EARLIER_ID) in printed, before_bind
    assert squashed(str(earlier_run)) in printed, before_bind
    assert "still" in before_bind.lower(), before_bind


def test_no_warning_after_a_finished_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a bridged build whose state is 'finished' with no error, also one with failed steps, the next bridged build prints no warning about an unfinished run."""
    failed = chain_build(tmp_path, FakeJobmon(workflow_id=_EARLIER_ID), failing=(B,))
    failed_state = failed.config[RUN_STATE_KEY]
    assert isinstance(failed_state, RunState)
    assert failed_state.status == "finished"
    after_failed = _OutputAtBind(workflow_id=_LATER_ID, capsys=capsys)
    capsys.readouterr()
    clean = chain_build(tmp_path, after_failed)
    clean_state = clean.config[RUN_STATE_KEY]
    assert isinstance(clean_state, RunState)
    assert clean_state.status == "finished"
    after_clean = _OutputAtBind(workflow_id=WORKFLOW_ID, capsys=capsys)
    capsys.readouterr()

    chain_build(tmp_path, after_clean)

    checks = [(after_failed, failed_state), (after_clean, clean_state)]
    for jobmon, earlier in checks:
        [before_bind] = jobmon.printed
        printed = squashed(before_bind)
        assert str(earlier.workflow_id) not in printed, before_bind
        assert earlier.workdir.name not in printed, before_bind


def test_no_warning_after_a_build_interrupted_while_binding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a build interrupted while binding, whose state has no workflow id, and no earlier run, the next bridged build prints no warning about an unfinished run."""
    chain_build(tmp_path, _SigtermInBind(workflow_id=_EARLIER_ID))
    [path] = state_files(build_root(tmp_path))
    assert read_run_state(path).workflow_id is None
    jobmon = _OutputAtBind(workflow_id=_LATER_ID, capsys=capsys)
    capsys.readouterr()

    chain_build(tmp_path, jobmon)

    [before_bind] = jobmon.printed
    printed = squashed(before_bind)
    assert path.parent.name not in printed, before_bind
    assert "still be running" not in printed, before_bind


def test_a_build_interrupted_while_binding_does_not_hide_an_earlier_unfinished_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When an earlier run's state is 'running' (written by hand, as a killed build leaves it) and a later build was interrupted while binding, the next bridged build still warns before it binds, naming the earlier run's workflow id and run directory."""
    root = build_root(tmp_path)
    earlier_run = root / RUNS_DIR / "20261104-120000-abcdef"
    earlier_run.mkdir(parents=True)
    write_run_state(
        RunState(
            workdir=earlier_run,
            status="running",
            workflow_id=_EARLIER_ID,
            workflow_status=None,
            error=None,
            pending=PLAN_ORDER,
        )
    )
    os.utime(earlier_run / STATE_FILE, (1_000_000_000, 1_000_000_000))
    chain_build(tmp_path, _SigtermInBind(workflow_id=WORKFLOW_ID))
    statuses = sorted((read_run_state(path).status for path in state_files(root)))
    assert statuses == ["interrupted", "running"]
    jobmon = _OutputAtBind(workflow_id=_LATER_ID, capsys=capsys)
    capsys.readouterr()

    chain_build(tmp_path, jobmon)

    [before_bind] = jobmon.printed
    printed = squashed(before_bind)
    assert str(_EARLIER_ID) in printed, before_bind
    assert squashed(str(earlier_run)) in printed, before_bind
    assert "still" in before_bind.lower(), before_bind


@pytest.mark.parametrize("error_after", [1, len(PLAN_ORDER)], ids=["early", "after_all"])
def test_the_next_build_warns_naming_the_workflow_id_and_error_of_a_run_jobmon_stopped_with_an_error(
    tmp_path: Path, error_after: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the latest state under the root is 'finished' with its error set (as after a Jobmon timeout), the next bridged build prints, before it binds, a warning that names that run's workflow id and run directory, gives the error, and says its jobs may still be running."""
    stopped = chain_build(
        tmp_path, FakeJobmon(error_after=error_after, workflow_id=_EARLIER_ID)
    )
    earlier = stopped.config[RUN_STATE_KEY]
    assert isinstance(earlier, RunState)
    assert earlier.status == "finished"
    assert earlier.error is not None
    [path] = state_files(build_root(tmp_path))
    message = f"FakeJobmon stopped the workflow after {error_after} entries"
    jobmon = _OutputAtBind(workflow_id=_LATER_ID, capsys=capsys)
    capsys.readouterr()

    chain_build(tmp_path, jobmon)

    [before_bind] = jobmon.printed
    printed = squashed(before_bind)
    assert str(_EARLIER_ID) in printed, before_bind
    assert squashed(str(path.parent)) in printed, before_bind
    assert squashed(message) in printed, before_bind
    assert "still" in before_bind.lower(), before_bind


def test_an_unreadable_latest_state_gives_a_warning_naming_the_file_and_the_build_goes_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the latest state file under the root holds text that is not JSON, the next bridged build prints a warning naming that file, then binds and runs its plan as usual."""
    spoiled = build_root(tmp_path) / RUNS_DIR / "20261104-120000-abcdef" / STATE_FILE
    spoiled.parent.mkdir(parents=True)
    spoiled.write_text("{ this is not JSON", encoding="utf-8")
    jobmon = _OutputAtBind(workflow_id=WORKFLOW_ID, capsys=capsys)
    capsys.readouterr()

    session = chain_build(tmp_path, jobmon)

    [before_bind] = jobmon.printed
    assert squashed(str(spoiled)) in squashed(before_bind), before_bind
    assert bound_names(jobmon) == [list(PLAN_ORDER)]
    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
