"""Tests for the run lock that a bridged build holds (``bridge.execute``).

The builds here run through ``FakeJobmon``, which runs each plan entry's command, and so
the real node runner, in a child process. A refusal is caused by a child process that
holds the lock, and whether the lock is held is checked from a child process, because on
NFS ``flock`` locks do not conflict within one process. Helpers are in
``tests.bridge.helpers`` and, for the child processes that hold or try the lock,
``tests.backend.lock_helpers``.
"""

import errno
import json
import os
import signal
import socket
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _pytask.lockfile import CURRENT_LOCKFILE_VERSION, LockfileState
from pytask import ExitCode, PTask, Session, TaskOutcome, console, hookimpl

from tests.backend.lock_helpers import account_name, held_lock, let_exit, lock_is_free
from tests.bridge.helpers import (
    JOURNAL,
    LOCKFILE,
    PLAN_ORDER,
    A,
    B,
    C,
    D,
    bound_names,
    build_root,
    chain_and_independent,
    chain_build,
    lockfile_entries,
    make_dir,
    only_state,
    recorded_pids,
    registry,
    run_lock_path,
    squashed,
    stable,
    state_files,
)
from vivarium.dagger.backend import lock as lock_module
from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.lock import (
    LOCK_FILE_MODE,
    RUN_LOCK_NAME,
    RUN_REFUSED_KEY,
    RunLocked,
    run_lock,
)
from vivarium.dagger.backend.preview import preview
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge import execute
from vivarium.dagger.bridge import plugin as bridge_plugin
from vivarium.dagger.bridge._jobmon_adapter import (
    JobmonBindError,
    JobmonEnvironmentError,
    JobmonRun,
    PlanEntry,
)
from vivarium.dagger.bridge.execute import _reload_lockfile_state
from vivarium.dagger.bridge.state import RUNS_DIR, RunState, write_run_state
from vivarium.dagger.harness import FakeJobmon

_BUILD_TIMEOUT = 300
"""The most seconds that a child process that runs a whole build may take, which is more
than a child that only holds or tries the lock needs."""

_EARLIER_ID = 9753102
"""The workflow id of an earlier run, which no other output of a build contains."""

_RECORD_A = """
import sys
from pathlib import Path

from tests.bridge.helpers import A, build_root, chain_and_independent, registry, stable
from vivarium.dagger.backend.build import build

tmp_path = Path(sys.argv[1])
tasks = stable(chain_and_independent(registry(tmp_path)))
session = build([task for task in tasks if task.name == A], build_root(tmp_path))
print("exit", int(session.exit_code), flush=True)
"""
"""A child that records step a, with a stable code identity, by a local build of the
root under the directory its first argument names."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


@pytest.fixture
def _sigterm_kept() -> Iterator[None]:
    """Restore SIGTERM's handler after the test, whatever the test left installed."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def _stable_steps(tmp_path: Path, *names: str) -> list[Task]:
    """Return the steps of a -> b -> c plus d named ``names``, with stable code ids."""
    tasks = stable(chain_and_independent(registry(tmp_path)))
    return [task for task in tasks if task.name in names]


def _register_with_each_build(monkeypatch: pytest.MonkeyPatch, extra: object) -> None:
    """Register ``extra`` with each build's plugin manager, after the bridge's own hooks."""
    post_parse = bridge_plugin.pytask_post_parse

    @hookimpl(trylast=True)
    def pytask_post_parse(config: dict[str, Any]) -> None:
        """Register the bridge as usual, then ``extra``."""
        post_parse(config)
        config["pm"].register(extra)

    monkeypatch.setattr(bridge_plugin, "pytask_post_parse", pytask_post_parse)


def _leave_an_interrupted_run(root: Path) -> Path:
    """Leave an 'interrupted' run of workflow ``_EARLIER_ID`` under ``root``; return it."""
    workdir = root / RUNS_DIR / "20261104-120000-abcdef"
    workdir.mkdir(parents=True)
    write_run_state(
        RunState(
            workdir=workdir,
            status="interrupted",
            workflow_id=_EARLIER_ID,
            workflow_status=None,
            error=None,
            pending=PLAN_ORDER,
        )
    )
    return workdir


def _lock_held_at_the_warning(monkeypatch: pytest.MonkeyPatch, lock: Path) -> list[bool]:
    """Return a list that notes if ``lock`` is held at each print about the earlier run."""
    held: list[bool] = []
    original = console.print

    def print_and_check(*objects: Any, **kwargs: Any) -> None:
        """Check the lock if the print names the earlier run, then print."""
        if any(str(_EARLIER_ID) in str(item) for item in objects):
            held.append(not lock_is_free(lock))
        original(*objects, **kwargs)

    monkeypatch.setattr(console, "print", print_and_check)
    return held


def _write_lockfile(root: Path, states: Mapping[str, str]) -> None:
    """Write a pytask.lock in ``root`` with one entry per task id, with the given state."""
    lines = [f'lock-version = "{CURRENT_LOCKFILE_VERSION}"', ""]
    for task_id, state in states.items():
        lines.extend(["[[task]]", f'id = "{task_id}"', f'state = "{state}"', ""])
    (root / LOCKFILE).write_text("\n".join(lines), encoding="utf-8")


def _write_journal(root: Path, states: Mapping[str, str]) -> None:
    """Write a pytask.lock.journal in ``root`` with one entry per task id, in order."""
    entries = [
        {"lock-version": CURRENT_LOCKFILE_VERSION, "id": task_id, "state": state}
        for task_id, state in states.items()
    ]
    text = "".join(f"{json.dumps(entry)}\n" for entry in entries)
    (root / JOURNAL).write_text(text, encoding="utf-8")


def _leave_a_holders_journal(tmp_path: Path) -> Path:
    """Record each step, leave a journal as a holder's run does, and return the root."""
    root = build_root(tmp_path)
    recorded = build(_stable_steps(tmp_path, *PLAN_ORDER), root, jobmon=False)
    assert recorded.exit_code == ExitCode.OK
    # A valid entry that differs from pytask.lock, so pytask finds its state dirty.
    entry = {**lockfile_entries(root)[A], "state": "recorded by the holder"}
    journal = json.dumps({"lock-version": CURRENT_LOCKFILE_VERSION, **entry})
    (root / JOURNAL).write_text(f"{journal}\n", encoding="utf-8")
    return root


def _flock_fails_with_enolck(fd: object, operation: int) -> None:
    """Fail as ``fcntl.flock`` does when the system has no lock to give."""
    raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))


@dataclass
class _ChecksTheLock(FakeJobmon):
    """Stand in for Jobmon, and check from a child whether the run lock is held."""

    lock: Path = Path()
    """The run lock file."""
    held: list[tuple[str, bool]] = field(default_factory=list, init=False)
    """Each moment checked, with whether the lock was held then."""

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
        """Check the lock, then bind as FakeJobmon does."""
        self.held.append(("bind", not lock_is_free(self.lock)))
        return super().bind(
            plan,
            cluster=cluster,
            default_resources=default_resources,
            max_attempts=max_attempts,
            resource_scales=resource_scales,
            workflow_name=workflow_name,
        )

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Check the lock, run as FakeJobmon does, and check the lock again."""
        self.held.append(("run start", not lock_is_free(self.lock)))
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)
        self.held.append(("run end", not lock_is_free(self.lock)))


@dataclass
class _RefusingBind(FakeJobmon):
    """Stand in for the adapter, refusing every bind as it does when no queue is named."""

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
        """Refuse the plan as the adapter does when no queue is named."""
        raise JobmonBindError("ValueError: A queue name must be provided, on purpose.")


@dataclass
class _RefusingEnvironment(FakeJobmon):
    """Stand in for the adapter, failing the environment check."""

    def check_environment(self, cluster: str) -> None:
        """Fail as a missing Jobmon install does."""
        raise JobmonEnvironmentError("Jobmon is not installed, on purpose.")


@dataclass
class _InterruptedCheck(FakeJobmon):
    """Stand in for the adapter, raising an exception that the hook does not catch."""

    error: type[BaseException] = KeyboardInterrupt
    """The exception that the environment check raises."""

    def check_environment(self, cluster: str) -> None:
        """Raise :attr:`error`."""
        raise self.error()


@dataclass
class _CountsChecks(FakeJobmon):
    """Stand in for the adapter, counting environment checks."""

    checks: int = field(default=0, init=False)
    """How many times the environment was checked."""

    def check_environment(self, cluster: str) -> None:
        """Count the check and accept the cluster."""
        self.checks += 1


class _ChecksTheLockWhilePlanning:
    """Check from a child whether the run lock is held when planning reaches a step."""

    def __init__(self, lock: Path) -> None:
        self.lock = lock
        self.held: list[bool] = []

    @hookimpl(tryfirst=True)
    def pytask_execute_task_protocol(self, session: Session, task: PTask) -> None:
        """At the first step that planning reaches, check the lock, and let pytask go on."""
        if session.config["dry_run"] and not self.held:
            self.held.append(not lock_is_free(self.lock))


# Builds that hold the lock


def test_end_to_end_two_bridged_builds_in_a_row_both_succeed(tmp_path: Path) -> None:
    """End to end through FakeJobmon and the real node runner: a bridged build of a -> b -> c plus d ends with ExitCode.OK and every step SUCCESS, a child then finds <root>/run.lock free, and a second bridged build of the same root ends with ExitCode.OK."""
    first = chain_build(tmp_path, FakeJobmon())

    assert first.exit_code == ExitCode.OK
    assert outcomes(first) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert lock_is_free(run_lock_path(tmp_path))

    second = chain_build(tmp_path, FakeJobmon())

    assert second.exit_code == ExitCode.OK


@pytest.mark.usefixtures("umask_022")
def test_a_bridged_build_leaves_run_lock_beside_pytask_lock(tmp_path: Path) -> None:
    """After a bridged build under umask 0o022, <root>/run.lock exists beside <root>/pytask.lock with mode 0o664 and holds a holder line naming this process's pid and host."""
    root = build_root(tmp_path)
    lock = run_lock_path(tmp_path)

    session = chain_build(tmp_path, FakeJobmon())

    assert session.exit_code == ExitCode.OK
    assert lock == root / RUN_LOCK_NAME
    assert lock.name == "run.lock"
    assert (root / LOCKFILE).exists()
    assert stat.S_IMODE(lock.stat().st_mode) == LOCK_FILE_MODE == 0o664
    holder = lock.read_text(encoding="utf-8")
    assert f"pid {os.getpid()} on host {socket.gethostname()}," in holder, holder


def test_the_lock_is_held_from_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When planning reaches its first step (checked from a child at that point, for example through a hook or a wrapped _plan), <root>/run.lock is already held."""
    planning = _ChecksTheLockWhilePlanning(run_lock_path(tmp_path))
    _register_with_each_build(monkeypatch, planning)

    session = chain_build(tmp_path, FakeJobmon())

    assert planning.held == [True]
    assert session.exit_code == ExitCode.OK


def test_the_lock_is_held_while_binding_and_running(tmp_path: Path) -> None:
    """With a FakeJobmon subclass that checks from a child in bind, at the start of run and at the end of run, <root>/run.lock is held each time, and after the build returns a child finds it free."""
    lock = run_lock_path(tmp_path)
    jobmon = _ChecksTheLock(lock=lock)

    session = chain_build(tmp_path, jobmon)

    assert jobmon.held == [("bind", True), ("run start", True), ("run end", True)]
    assert session.exit_code == ExitCode.OK
    assert lock_is_free(lock)


def test_the_lock_is_held_when_each_state_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With write_run_state wrapped to check from a child, <root>/run.lock is held when the 'running' state is written and when the final state is written, after recording and settlement."""
    lock = run_lock_path(tmp_path)
    written: list[tuple[str, bool]] = []
    original = execute.write_run_state

    def write_and_check(state: RunState) -> None:
        """Note whether the lock is held, then write ``state``."""
        written.append((state.status, not lock_is_free(lock)))
        original(state)

    monkeypatch.setattr(execute, "write_run_state", write_and_check)

    # b's runner dies without a result, so the build settles it before the final state.
    session = chain_build(tmp_path, FakeJobmon(), killed=(B,))

    assert written == [("running", True), ("finished", True)]
    assert outcomes(session)[B] == TaskOutcome.FAIL


def test_the_lock_is_held_while_warning_about_an_unfinished_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an 'interrupted' state left under the root, the lock is held when the hook prints its warning about the unfinished run (checked from a child when the warning is printed)."""
    _leave_an_interrupted_run(build_root(tmp_path))
    held = _lock_held_at_the_warning(monkeypatch, run_lock_path(tmp_path))

    chain_build(tmp_path, FakeJobmon())

    assert held == [True]


# A refused build


def test_a_build_is_refused_while_another_process_holds_the_lock(tmp_path: Path) -> None:
    """With stable code identities and one bridged build done, while a child holds run_lock on <root>/run.lock, a bridged build ends with ExitCode.FAILED, has no execution reports (so no SKIP_UNCHANGED from planning), binds no plan, runs no step body, and creates no new run directory or state file."""
    root = build_root(tmp_path)
    artifacts = registry(tmp_path)
    first = chain_build(tmp_path, FakeJobmon(), stable_ids=True)
    assert first.exit_code == ExitCode.OK
    run_directories = sorted((root / RUNS_DIR).iterdir())
    states = state_files(root)
    pids = recorded_pids(artifacts, PLAN_ORDER)
    jobmon = FakeJobmon()

    with held_lock(run_lock_path(tmp_path)):
        session = chain_build(tmp_path, jobmon, stable_ids=True)

    assert session.exit_code == ExitCode.FAILED
    assert session.execution_reports == []
    assert jobmon.plans == []
    assert jobmon.runs == []
    assert recorded_pids(artifacts, PLAN_ORDER) == pids
    assert sorted((root / RUNS_DIR).iterdir()) == run_directories
    assert state_files(root) == states


def test_a_refused_build_leaves_pytask_lock_and_its_journal_byte_identical(
    tmp_path: Path,
) -> None:
    """With a pytask.lock and a pytask.lock.journal in the root, as a holder's unflushed run leaves them, a refused bridged build leaves both files byte-identical, and the journal still exists."""
    root = _leave_a_holders_journal(tmp_path)
    lockfile_before = (root / LOCKFILE).read_bytes()
    journal_before = (root / JOURNAL).read_bytes()

    with held_lock(run_lock_path(tmp_path)):
        session = chain_build(tmp_path, FakeJobmon(), stable_ids=True)

    assert session.exit_code == ExitCode.FAILED
    assert (root / JOURNAL).exists()
    assert (root / LOCKFILE).read_bytes() == lockfile_before
    assert (root / JOURNAL).read_bytes() == journal_before


@pytest.mark.parametrize("failure", ["directory", "ENOLCK"])
def test_a_run_lock_error_leaves_pytask_lock_and_its_journal_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """With pytask.lock and a journal in the root as a holder's unflushed run leaves them, when run_lock raises an OSError and not RunLocked (<root>/run.lock is a directory, or fcntl.flock fails with ENOLCK), a bridged build ends with ExitCode.FAILED, binds nothing, has no RUN_REFUSED_KEY, and leaves both files byte-identical with the journal still there."""
    root = _leave_a_holders_journal(tmp_path)
    lockfile_before = (root / LOCKFILE).read_bytes()
    journal_before = (root / JOURNAL).read_bytes()
    if failure == "directory":
        make_dir(run_lock_path(tmp_path))
    else:
        monkeypatch.setattr(lock_module.fcntl, "flock", _flock_fails_with_enolck)
    jobmon = FakeJobmon()

    session = chain_build(tmp_path, jobmon, stable_ids=True)

    assert session.exit_code == ExitCode.FAILED
    assert RUN_REFUSED_KEY not in session.config
    assert jobmon.plans == []
    assert (root / JOURNAL).exists()
    assert (root / LOCKFILE).read_bytes() == lockfile_before
    assert (root / JOURNAL).read_bytes() == journal_before


def test_a_refused_build_prints_the_lock_file_and_holder_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refused build's output, with ANSI escapes and whitespace removed, names <root>/run.lock, the child's user, 'pid <child pid>' and socket.gethostname(), and contains no 'Traceback'."""
    lock = run_lock_path(tmp_path)
    make_dir(build_root(tmp_path))

    with held_lock(lock) as child:
        capsys.readouterr()
        session = chain_build(tmp_path, FakeJobmon())
        captured = capsys.readouterr()

    output = captured.out + captured.err
    printed = squashed(output)
    assert session.exit_code == ExitCode.FAILED
    # "user <name>", since tmp_path already holds the user's name.
    shown = (
        str(lock),
        f"user {account_name()}",
        f"pid {child.pid}",
        f"host {socket.gethostname()}",
    )
    for text in shown:
        assert squashed(text) in printed, (text, output)
    assert "Traceback" not in output, output


def test_a_refused_build_leaves_run_locked_under_bridge_refused(tmp_path: Path) -> None:
    """After a refused build, session.config[RUN_REFUSED_KEY], which is 'bridge_refused', is a RunLocked whose path is <root>/run.lock and whose holder names the child's pid."""
    lock = run_lock_path(tmp_path)
    make_dir(build_root(tmp_path))

    with held_lock(lock) as child:
        session = chain_build(tmp_path, FakeJobmon())

    assert RUN_REFUSED_KEY == "bridge_refused"
    refused = session.config.get(RUN_REFUSED_KEY)
    assert isinstance(refused, RunLocked), repr(refused)
    assert refused.path == lock
    assert f"pid {child.pid} on host" in refused.holder, refused.holder


def test_a_build_that_is_not_refused_has_no_bridge_refused(tmp_path: Path) -> None:
    """After a bridged build that holds the lock and runs, session.config has no RUN_REFUSED_KEY."""
    session = chain_build(tmp_path, FakeJobmon())

    assert session.exit_code == ExitCode.OK
    assert RUN_REFUSED_KEY not in session.config


def test_a_refused_build_does_not_warn_about_an_unfinished_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With an 'interrupted' state under the root, a refused bridged build prints no warning about an unfinished run, because that check comes after the lock."""
    _leave_an_interrupted_run(build_root(tmp_path))

    with held_lock(run_lock_path(tmp_path)):
        capsys.readouterr()
        session = chain_build(tmp_path, FakeJobmon())
        captured = capsys.readouterr()

    output = captured.out + captured.err
    assert isinstance(session.config.get(RUN_REFUSED_KEY), RunLocked)
    assert str(_EARLIER_ID) not in squashed(output), output
    assert squashed("still be running") not in squashed(output), output


def test_a_refused_build_does_not_check_the_jobmon_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused bridged build through a FakeJobmon subclass that counts check_environment calls makes none, and fails with the refusal and not an environment error."""
    lock = run_lock_path(tmp_path)
    make_dir(build_root(tmp_path))
    jobmon = _CountsChecks()

    with held_lock(lock):
        capsys.readouterr()
        session = chain_build(tmp_path, jobmon)
        captured = capsys.readouterr()

    assert jobmon.checks == 0
    assert session.exit_code == ExitCode.FAILED
    assert isinstance(session.config.get(RUN_REFUSED_KEY), RunLocked)
    output = captured.out + captured.err
    assert squashed(str(lock)) in squashed(output), output


def test_a_build_after_the_holder_exits_runs_normally(tmp_path: Path) -> None:
    """After a refused build, once the child that held the lock has exited, a bridged build of the same root ends with ExitCode.OK and records every step in pytask.lock."""
    root = build_root(tmp_path)
    make_dir(root)
    with held_lock(run_lock_path(tmp_path)) as child:
        refused = chain_build(tmp_path, FakeJobmon())
        let_exit(child)
    assert isinstance(refused.config.get(RUN_REFUSED_KEY), RunLocked)

    session = chain_build(tmp_path, FakeJobmon())

    assert session.exit_code == ExitCode.OK
    assert set(lockfile_entries(root)) == set(PLAN_ORDER)


# The lock is released however the hook ends


def test_the_lock_is_free_after_a_refused_bind(tmp_path: Path) -> None:
    """With a FakeJobmon subclass whose bind raises JobmonBindError, the build ends with ExitCode.FAILED and a child then finds <root>/run.lock free."""
    session = chain_build(tmp_path, _RefusingBind())

    assert session.exit_code == ExitCode.FAILED
    assert lock_is_free(run_lock_path(tmp_path))


@pytest.mark.parametrize("failure", ["error_after", "failing_step"])
def test_the_lock_is_free_after_a_run_that_fails(tmp_path: Path, failure: str) -> None:
    """After a bridged build whose Jobmon run stops with an error (FakeJobmon(error_after=1)) or whose step b fails on its node, the build ends with ExitCode.FAILED and a child finds <root>/run.lock free."""
    if failure == "error_after":
        session = chain_build(tmp_path, FakeJobmon(error_after=1))
    else:
        session = chain_build(tmp_path, FakeJobmon(), failing=(B,))

    assert session.exit_code == ExitCode.FAILED
    assert lock_is_free(run_lock_path(tmp_path))


@pytest.mark.parametrize("failure", ["environment", "jobmon_timeout"])
def test_the_lock_is_free_after_a_failure_before_the_run(
    tmp_path: Path, failure: str
) -> None:
    """After a bridged build that fails before binding, because check_environment raises JobmonEnvironmentError or jobmon_timeout is 0, the build ends with ExitCode.FAILED and a child finds <root>/run.lock free."""
    if failure == "environment":
        session = chain_build(tmp_path, _RefusingEnvironment())
    else:
        session = chain_build(tmp_path, FakeJobmon(), jobmon_timeout=0)

    assert session.exit_code == ExitCode.FAILED
    assert lock_is_free(run_lock_path(tmp_path))


@pytest.mark.usefixtures("_sigterm_kept")
def test_the_lock_is_free_after_a_sigterm_settlement(tmp_path: Path) -> None:
    """After a bridged build that FakeJobmon(interrupt_after=2) interrupts with SIGTERM and the hook settles, the build returns and a child finds <root>/run.lock free."""
    session = chain_build(tmp_path, FakeJobmon(interrupt_after=2))

    assert only_state(build_root(tmp_path)).status == "interrupted"
    assert session.exit_code == ExitCode.FAILED
    assert lock_is_free(run_lock_path(tmp_path))


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_that_escapes_the_hook_releases_the_lock(
    tmp_path: Path, error: type[BaseException]
) -> None:
    """With a FakeJobmon subclass whose check_environment raises KeyboardInterrupt or SystemExit, which the hook does not catch, the exception propagates out of build() and, with this process still alive, a child finds <root>/run.lock free."""
    with pytest.raises(error):
        chain_build(tmp_path, _InterruptedCheck(error=error))

    assert lock_is_free(run_lock_path(tmp_path))


# Reloading pytask's lockfile state


def test_the_hook_plans_from_a_record_written_after_pytask_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When another build records step a in pytask.lock after this build's pytask has read its lockfile state, as late as just after the hook has taken the lock (here from a wrapper of run_lock that runs that build in a child process once the real lock is held), the hook plans from the new record: a is SKIP_UNCHANGED and is not bound (stable code identities)."""
    root = build_root(tmp_path)
    # Without a pytask.lock, pytask would fall back to its database, which the other
    # build also updates, so d is recorded first and the lockfile then decides.
    recorded = build(_stable_steps(tmp_path, D), root, jobmon=False)
    assert set(lockfile_entries(root)) == {D}, recorded.exit_code
    children: list[subprocess.CompletedProcess[str]] = []

    @contextmanager
    def lock_then_record_a(path: Path) -> Iterator[None]:
        """Take the real run lock, then record step a in another, local build."""
        with run_lock(path):
            # A record written only after the lock is taken is seen only by a reload
            # that comes after the lock.
            other_build = subprocess.run(
                [sys.executable, "-c", _RECORD_A, str(tmp_path)],
                capture_output=True,
                text=True,
                cwd=tmp_path,
                timeout=_BUILD_TIMEOUT,
                check=False,
            )
            children.append(other_build)
            yield

    monkeypatch.setattr(execute, "run_lock", lock_then_record_a)
    jobmon = FakeJobmon()

    session = chain_build(tmp_path, jobmon, stable_ids=True)

    [child] = children
    # The child's build prints pytask's own report first, so only its last line is ours.
    assert child.stdout.strip().splitlines()[-1:] == ["exit 0"], child.stderr[-4000:]
    reported = outcomes(session)
    assert reported.get(A) == TaskOutcome.SKIP_UNCHANGED, reported
    [bound] = bound_names(jobmon)
    assert A not in bound
    assert {B, C} <= set(bound)


def test_reload_lockfile_state_reads_the_lockfile_and_journal_as_they_are_now(
    tmp_path: Path,
) -> None:
    """_reload_lockfile_state replaces session.config['lockfile_state'] with a state whose task entries are those of pytask.lock with its journal applied as they are on disk at the call."""
    root = make_dir(tmp_path / "state")
    lockfile = root / LOCKFILE
    _write_lockfile(root, {"gone": "1"})
    earlier = LockfileState.from_path(lockfile, root)
    config = {"root": root, "lockfile_path": lockfile, "lockfile_state": earlier}
    session = Session(config=config)
    _write_lockfile(root, {"kept": "1", "replaced": "1"})
    _write_journal(root, {"replaced": "2", "added": "1"})

    _reload_lockfile_state(session)

    state = session.config["lockfile_state"]
    assert isinstance(state, LockfileState), repr(state)
    assert state is not earlier
    entries = {entry.id: entry.state for entry in state.lockfile.task}
    assert entries == {"kept": "1", "replaced": "2", "added": "1"}


def test_reload_lockfile_state_reads_again_when_a_flush_lands_during_the_read(
    tmp_path: Path,
) -> None:
    """When the previous holder replaces pytask.lock and deletes its journal while _reload_lockfile_state reads them, so the first read sees the old lockfile and no journal, the reload reads again and its state has the journal's entries."""

    class _FlushedWhileRead(LockfileState):
        """Read pytask.lock as a build would just before another build's flush lands."""

        armed = False
        reads = 0

        @classmethod
        def from_path(cls, path: Path, root: Path) -> LockfileState:
            """Once armed, first read the old lockfile alone, then flush."""
            if not cls.armed:
                return super().from_path(path, root)
            cls.reads += 1
            if cls.reads > 1:
                return super().from_path(path, root)
            journal = path.with_suffix(f"{path.suffix}.journal")
            flushed = super().from_path(path, root)
            journal.unlink()
            stale = super().from_path(path, root)
            flushed.flush()
            return stale

    root = make_dir(tmp_path / "state")
    lockfile = root / LOCKFILE
    _write_lockfile(root, {"kept": "1"})
    _write_journal(root, {"added": "1"})
    earlier = _FlushedWhileRead.from_path(lockfile, root)
    session = Session(
        config={"root": root, "lockfile_path": lockfile, "lockfile_state": earlier}
    )
    _FlushedWhileRead.armed = True

    _reload_lockfile_state(session)

    state = session.config["lockfile_state"]
    entries = {entry.id: entry.state for entry in state.lockfile.task}
    assert entries == {"kept": "1", "added": "1"}
    assert _FlushedWhileRead.reads == 2


def test_reload_lockfile_state_keeps_a_none_state(tmp_path: Path) -> None:
    """With session.config['lockfile_state'] set to None, _reload_lockfile_state leaves it None."""
    root = make_dir(tmp_path / "state")
    lockfile = root / LOCKFILE
    _write_lockfile(root, {"kept": "1"})
    config = {"root": root, "lockfile_path": lockfile, "lockfile_state": None}
    session = Session(config=config)

    _reload_lockfile_state(session)

    assert session.config["lockfile_state"] is None


# Local modes take no lock


def test_a_build_without_jobmon_takes_no_lock(tmp_path: Path) -> None:
    """build(jobmon=False) creates no <root>/run.lock, and while a child holds run_lock on that file it still ends with ExitCode.OK."""
    lock = run_lock_path(tmp_path)
    tasks = chain_and_independent(registry(tmp_path))
    root = build_root(tmp_path)

    first = build(tasks, root, jobmon=False)
    created = lock.exists()
    with held_lock(lock):
        second = build(tasks, root, jobmon=False)

    assert first.exit_code == ExitCode.OK
    assert not created
    assert second.exit_code == ExitCode.OK


@pytest.mark.parametrize("mode", ["dry_run", "explain"])
def test_a_dry_run_or_explanation_with_jobmon_takes_no_lock(
    tmp_path: Path, mode: str
) -> None:
    """A build with jobmon=True and dry_run or explain creates no <root>/run.lock, and while a child holds run_lock on that file it is not refused."""
    lock = run_lock_path(tmp_path)
    tasks = chain_and_independent(registry(tmp_path))
    root = build_root(tmp_path)
    jobmon = FakeJobmon()
    flags: dict[str, Any] = {mode: True}

    with jobmon:
        first = build(tasks, root, jobmon=True, **flags)
        created = lock.exists()
        with held_lock(lock):
            second = build(tasks, root, jobmon=True, **flags)

    assert first.exit_code == ExitCode.OK
    assert not created
    assert second.exit_code == ExitCode.OK
    assert RUN_REFUSED_KEY not in second.config
    assert jobmon.plans == []


def test_a_preview_takes_no_lock(tmp_path: Path) -> None:
    """preview() of a root creates no <root>/run.lock, and while a child holds run_lock on that file it still returns a plan."""
    lock = run_lock_path(tmp_path)
    tasks = chain_and_independent(registry(tmp_path))
    root = build_root(tmp_path)

    preview(tasks, root)
    created = lock.exists()
    with held_lock(lock):
        plan = preview(tasks, root)

    assert not created
    assert sorted(step.name for step in plan) == sorted(PLAN_ORDER)
