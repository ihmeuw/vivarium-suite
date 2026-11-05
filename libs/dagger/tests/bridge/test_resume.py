"""Tests for settling a bridged build and resuming it (``bridge.execute``).

The builds here run through ``FakeJobmon``, which runs each plan entry's command, and so
the real node runner, in a child process. Helpers are in ``tests.bridge.helpers``.
"""

import math
import os
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import ExitCode, PTask, Session, TaskOutcome, hookimpl

from tests.bridge import step_targets
from tests.bridge.helpers import (
    CHAIN,
    INDEPENDENT,
    WORKFLOW_ID,
    bound_names,
    chain_and_independent,
    lockfile_entries,
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
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge import plugin as bridge_plugin
from vivarium.dagger.bridge._jobmon_adapter import JobmonBindError, JobmonRun, PlanEntry
from vivarium.dagger.bridge.state import (
    RUN_STATE_KEY,
    RUNS_DIR,
    RunState,
    latest_run_state,
    read_run_state,
)
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon, stable_code_id

A, B, C = CHAIN
"""The steps of the chain a -> b -> c."""

D = INDEPENDENT
"""The step that no other step depends on."""

PLAN_ORDER = (A, D, B, C)
"""The order in which the bridge plans a -> b -> c plus d: by wave, then by name."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


def _root(tmp_path: Path) -> Path:
    """Return the root of the builds under ``tmp_path``."""
    return tmp_path / "state"


def _chain_build(
    tmp_path: Path,
    jobmon: FakeJobmon,
    *,
    failing: Collection[str] = (),
    killed: Collection[str] = (),
    stable_ids: bool = False,
    **options: Any,
) -> Session:
    """Build a -> b -> c plus d under ``tmp_path`` with jobmon=True through ``jobmon``.

    The products go under ``tmp_path / "out"`` and the root is :func:`_root`. A step named
    in ``failing`` raises on its node, and one named in ``killed`` ends its runner without
    a result. ``stable_ids`` gives every step a stable code identity.
    """
    tasks = chain_and_independent(registry(tmp_path), failing=failing, killed=killed)
    if stable_ids:
        tasks = stable(tasks)
    with jobmon:
        return build(tasks, _root(tmp_path), jobmon=True, **options)


def _only_state(root: Path) -> RunState:
    """Return the state of the one run under ``root``."""
    [path] = state_files(root)
    return read_run_state(path)


def _has_word(word: str, text: str) -> bool:
    """Check whether ``word`` stands alone in ``text``, quoted or not."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _gives_status(label: str, code: str, text: str) -> bool:
    """Check whether ``text`` gives ``code`` right after ``label``, quoted or not."""
    pattern = rf"\b(?i:{re.escape(label)})\W*{re.escape(code)}\b"
    return re.search(pattern, text) is not None


def _signature(session: Session, name: str) -> str:
    """Return the pytask signature of the session's step called ``name``."""
    [signature] = [task.signature for task in session.tasks if task.name == name]
    return signature


def _at_version(task: Task, version: str) -> Task:
    """Return ``task`` with a stable code identity at ``version``."""
    return replace(task, code_id=stable_code_id(f"{task.name}::code", version))


def _jobmon_for(stop: str) -> FakeJobmon:
    """Return a FakeJobmon that stops the build in the way ``stop`` names.

    For ``"node_failure"`` and ``"no_result"`` the build's own step b fails or dies.
    """
    if stop == "run_error":
        return FakeJobmon(error_after=1)
    if stop == "sigterm":
        return FakeJobmon(interrupt_after=2)
    if stop == "keyboard_interrupt":
        return FakeJobmon(interrupt_after=2, interrupt_with="keyboard_interrupt")
    return FakeJobmon()


def _stopped_build(tmp_path: Path, stop: str, **options: Any) -> Session:
    """Build a -> b -> c plus d with jobmon=True, stopped in the way ``stop`` names."""
    return _chain_build(
        tmp_path,
        _jobmon_for(stop),
        failing=(B,) if stop == "node_failure" else (),
        killed=(B,) if stop == "no_result" else (),
        **options,
    )


class _ProtocolCalls:
    """Record each call of pytask's execute protocol made while dry_run is False."""

    def __init__(self) -> None:
        self.names: list[str] = []

    @hookimpl(tryfirst=True)
    def pytask_execute_task_protocol(self, session: Session, task: PTask) -> None:
        """Note the step when the call is not part of planning, and let pytask go on."""
        if not session.config["dry_run"]:
            self.names.append(task.name)


def _register_with_each_build(monkeypatch: pytest.MonkeyPatch, extra: object) -> None:
    """Register ``extra`` with each build's plugin manager, after the bridge's own hooks."""
    post_parse = bridge_plugin.pytask_post_parse

    @hookimpl(trylast=True)
    def pytask_post_parse(config: dict[str, Any]) -> None:
        """Register the bridge as usual, then ``extra``."""
        post_parse(config)
        config["pm"].register(extra)

    monkeypatch.setattr(bridge_plugin, "pytask_post_parse", pytask_post_parse)


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
class _ReadsTheStateWhileRunning(FakeJobmon):
    """Stand in for Jobmon, and read the latest run state under a root before running."""

    root: Path = Path()
    """The build's root."""
    seen: list[RunState | None] = field(default_factory=list, init=False)
    """The latest run state under the root at the start of each run."""

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Read the latest run state, then run every entry as FakeJobmon does."""
        self.seen.append(latest_run_state(self.root))
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)


def _missing_input_steps(directory: Path) -> tuple[ArtifactRegistry, list[Task]]:
    """Return x -> y and an unrelated step u under ``directory``, with x's input deleted.

    Step x reads the root input ``raw``, which exists while the steps are wired and is
    deleted afterwards.
    """
    raw = directory / "raw.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"n": [1]}).to_parquet(raw)
    artifacts = ArtifactRegistry(directory / "out", "run1", root_inputs={"raw": raw})
    tasks = [
        python_step(
            step_targets.stack,
            inputs={"upstream": "raw"},
            produces="x.parquet",
            registry=artifacts,
        ),
        python_step(
            step_targets.stack,
            inputs={"upstream": "x.parquet"},
            produces="y.parquet",
            registry=artifacts,
        ),
        python_step(
            step_targets.constant, inputs={}, produces="u.parquet", registry=artifacts
        ),
    ]
    raw.unlink()
    return artifacts, tasks


# A step that fails on its node


def test_a_failed_step_fails_its_descendant_is_skipped_and_an_independent_step_succeeds(
    tmp_path: Path,
) -> None:
    """End to end: with FakeJobmon(workflow_id=4242), a bridged build of a -> b -> c plus d where b raises on its node reports a and d SUCCESS, b FAIL, c SKIP_PREVIOUS_FAILED, one report per step, and exit code FAILED; no product records the test process's pid; pytask.lock holds a and d only; the state file is 'finished' with workflow id 4242, workflow status 'E' and pending (b, c)."""
    session = _chain_build(tmp_path, FakeJobmon(workflow_id=WORKFLOW_ID), failing={B})

    assert outcomes(session) == {
        A: TaskOutcome.SUCCESS,
        D: TaskOutcome.SUCCESS,
        B: TaskOutcome.FAIL,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert session.exit_code == ExitCode.FAILED
    assert os.getpid() not in recorded_pids(registry(tmp_path), PLAN_ORDER)
    assert set(lockfile_entries(_root(tmp_path))) == {A, D}
    state = _only_state(_root(tmp_path))
    assert state.status == "finished"
    assert state.workflow_id == WORKFLOW_ID
    assert state.workflow_status == "E"
    assert state.pending == (B, C)


def test_after_fixing_the_failed_step_the_next_build_runs_it_and_its_descendants(
    tmp_path: Path,
) -> None:
    """With stable code identities, after the build where b failed on its node, a second bridged build with b fixed binds a plan of exactly b and c, reports a and d SKIP_UNCHANGED and b and c SUCCESS, and leaves a state file 'finished' with nothing pending."""
    _chain_build(tmp_path, FakeJobmon(), failing={B}, stable_ids=True)
    jobmon = FakeJobmon()

    session = _chain_build(tmp_path, jobmon, stable_ids=True)

    assert bound_names(jobmon) == [[B, C]]
    assert outcomes(session) == {
        A: TaskOutcome.SKIP_UNCHANGED,
        D: TaskOutcome.SKIP_UNCHANGED,
        B: TaskOutcome.SUCCESS,
        C: TaskOutcome.SUCCESS,
    }
    state = latest_run_state(_root(tmp_path))
    assert state is not None
    assert state.status == "finished"
    assert state.pending == ()


def test_a_failed_step_with_an_older_lockfile_entry_keeps_it_and_is_planned_again(
    tmp_path: Path,
) -> None:
    """With stable code identities, a step that succeeded in one bridged build and fails on its node after its code version changes keeps its older pytask.lock entry, and the next bridged build plans it again."""
    root = _root(tmp_path)
    _chain_build(tmp_path, FakeJobmon(), stable_ids=True)
    first_entry = lockfile_entries(root)[B]
    changed = stable(chain_and_independent(registry(tmp_path), failing={B}))
    changed = [_at_version(task, "2") if task.name == B else task for task in changed]
    with FakeJobmon():
        failed = build(changed, root, jobmon=True)
    assert outcomes(failed)[B] == TaskOutcome.FAIL
    jobmon = FakeJobmon()

    with jobmon:
        build(changed, root, jobmon=True)

    assert lockfile_entries(root)[B] == first_entry
    assert bound_names(jobmon) == [[B, C]]


# A step with no result


def test_a_runner_that_dies_without_a_result_fails_the_build(tmp_path: Path) -> None:
    """End to end through FakeJobmon and the real runner: a step whose target calls os._exit(137), beside an independent step that succeeds, is reported FAIL and the other SUCCESS, session.exit_code is ExitCode.FAILED, and pytask.lock holds only the step that succeeded."""
    artifacts = registry(tmp_path)
    tasks = [
        python_step(
            step_targets.exit_without_a_result,
            inputs={},
            produces="dies.parquet",
            registry=artifacts,
        ),
        python_step(
            step_targets.record_pid, inputs={}, produces="lives.parquet", registry=artifacts
        ),
    ]
    root = _root(tmp_path)

    with FakeJobmon():
        session = build(tasks, root, jobmon=True)

    assert outcomes(session) == {
        "dies.parquet": TaskOutcome.FAIL,
        "lives.parquet": TaskOutcome.SUCCESS,
    }
    assert session.exit_code == ExitCode.FAILED
    assert set(lockfile_entries(root)) == {"lives.parquet"}


def test_a_step_with_no_result_gets_a_fail_naming_its_task_status_and_the_workflow(
    tmp_path: Path,
) -> None:
    """With FakeJobmon(workflow_id=4242), the step whose runner died is reported FAIL with a RemoteTaskFailed whose exc_type, exc_message and node_traceback are None and whose message names the step, says that it was not recorded and will run again, gives task status 'F', workflow 4242 and workflow status 'E', and says that the workflow ended without a result for it, as when its runner was killed on its node."""
    session = _chain_build(tmp_path, FakeJobmon(workflow_id=WORKFLOW_ID), killed={B})

    error = remote_failure(report_for(session, B))
    message = str(error)
    assert (error.exc_type, error.exc_message, error.node_traceback) == (None, None, None)
    assert B in message, message
    assert "not recorded" in message, message
    assert "run again" in message, message
    assert _gives_status("task status", "F", message), message
    assert _has_word(str(WORKFLOW_ID), message), message
    assert _gives_status("workflow status", "E", message), message
    assert "killed on its node" in message or "ended without a result" in message, message


def test_a_synthesized_failure_carries_the_runners_captured_output(tmp_path: Path) -> None:
    """The FAIL of the step whose runner wrote a line to stderr and then died has that line in its RemoteTaskFailed's stderr and in a ('call', 'stderr', ...) report section."""
    session = _chain_build(tmp_path, FakeJobmon(), killed={B})

    report = report_for(session, B)
    error = remote_failure(report)
    assert step_targets.KILLED_STDERR in error.stderr, error.stderr
    call_stderr = [
        text for when, key, text in report.sections if (when, key) == ("call", "stderr")
    ]
    assert any(step_targets.KILLED_STDERR in text for text in call_stderr), report.sections


def test_the_descendant_of_a_step_with_no_result_is_skip_previous_failed(
    tmp_path: Path,
) -> None:
    """In a -> b -> c plus d where b's runner dies without a result, b is FAIL and c, which FakeJobmon withholds with status 'G', is SKIP_PREVIOUS_FAILED and not a second synthesized FAIL; a and d are SUCCESS."""
    jobmon = FakeJobmon()

    session = _chain_build(tmp_path, jobmon, killed={B})

    [jobmon_run] = jobmon.runs
    assert jobmon_run.task_statuses[_signature(session, C)] == "G"
    assert outcomes(session) == {
        A: TaskOutcome.SUCCESS,
        D: TaskOutcome.SUCCESS,
        B: TaskOutcome.FAIL,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)


def test_settlement_reports_in_plan_order(tmp_path: Path) -> None:
    """When FakeJobmon(error_after=1) stops a build of a -> b -> c plus d, whose plan order is a, d, b, c, the reports appended after a's are d FAIL, then b FAIL, then c SKIP_PREVIOUS_FAILED, and c's skip names b as the failed step."""
    session = _chain_build(tmp_path, FakeJobmon(error_after=1))

    reported = [(report.task.name, report.outcome) for report in session.execution_reports]
    after_a = reported[reported.index((A, TaskOutcome.SUCCESS)) + 1 :]
    assert after_a == [
        (D, TaskOutcome.FAIL),
        (B, TaskOutcome.FAIL),
        (C, TaskOutcome.SKIP_PREVIOUS_FAILED),
    ]
    skip = report_for(session, C)
    assert skip.exc_info is not None
    assert B in str(skip.exc_info[1]), skip.exc_info[1]


# Jobmon stopping with an error


def test_when_jobmon_raises_every_step_without_a_result_fails_naming_the_error(
    tmp_path: Path,
) -> None:
    """With FakeJobmon(error_after=1, workflow_id=4242), a bridged build of a -> b -> c plus d records a SUCCESS, reports b and d FAIL with messages that say Jobmon stopped the workflow with an error and name RuntimeError and its message, task status 'unknown' and workflow status 'unknown', and reports c SKIP_PREVIOUS_FAILED; the hook returns without raising and the exit code is FAILED."""
    jobmon = FakeJobmon(error_after=1, workflow_id=WORKFLOW_ID)

    session = _chain_build(tmp_path, jobmon)

    [jobmon_run] = jobmon.runs
    run_error = jobmon_run.error
    assert isinstance(run_error, RuntimeError), repr(run_error)
    assert outcomes(session) == {
        A: TaskOutcome.SUCCESS,
        D: TaskOutcome.FAIL,
        B: TaskOutcome.FAIL,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    for name in (D, B):
        message = str(remote_failure(report_for(session, name)))
        assert name in message, message
        assert "Jobmon" in message and "error" in message, message
        assert "RuntimeError" in message, message
        assert str(run_error) in message, message
        assert message.count("unknown") >= 2, message
    # pytask sets execution_end only in pytask_execute_log_end, which a raising hook skips.
    assert math.isfinite(session.execution_end)
    assert session.exit_code == ExitCode.FAILED


def test_a_run_error_after_a_node_failure_still_shows_the_failure_and_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With FakeJobmon(error_after=2) on two independent steps where the first raises on its node, the build's printed output holds that step's RemoteTaskFailed message and the stderr line it wrote on its node, then pytask's summary of the build, and session.exit_code is ExitCode.FAILED."""
    artifacts = registry(tmp_path)
    tasks = [
        python_step(
            step_targets.raise_step_failure,
            inputs={},
            produces="a_fails.parquet",
            registry=artifacts,
        ),
        python_step(
            step_targets.constant,
            inputs={},
            produces="b_succeeds.parquet",
            registry=artifacts,
        ),
    ]
    capsys.readouterr()

    with FakeJobmon(error_after=2):
        session = build(tasks, _root(tmp_path), jobmon=True)

    captured = capsys.readouterr()
    output = squashed(captured.out + captured.err)
    summary = output.rfind("Summary")
    assert summary >= 0, output
    for shown in (
        "a_fails.parquet",
        step_targets.FAILURE_MESSAGE,
        step_targets.STDERR_BEFORE_FAILURE,
    ):
        position = output.find(squashed(shown))
        assert 0 <= position < summary, (shown, output)
    assert session.exit_code == ExitCode.FAILED


def test_a_run_error_leaves_the_state_finished_with_the_error(tmp_path: Path) -> None:
    """After a build that FakeJobmon(error_after=1, workflow_id=4242) stops, the state file is 'finished' with workflow id 4242, workflow_status None, an error that starts with 'RuntimeError: ' and the names of the steps not recorded as pending."""
    jobmon = FakeJobmon(error_after=1, workflow_id=WORKFLOW_ID)

    _chain_build(tmp_path, jobmon)

    [jobmon_run] = jobmon.runs
    state = _only_state(_root(tmp_path))
    assert state.status == "finished"
    assert state.workflow_id == WORKFLOW_ID
    assert state.workflow_status is None
    assert state.error == f"RuntimeError: {jobmon_run.error}"
    assert state.pending == (D, B, C)


def test_a_run_error_after_every_result_is_written_leaves_the_build_ok(
    tmp_path: Path,
) -> None:
    """When FakeJobmon(error_after=N) stops a build only after all N entries have run and written successful results, every step is SUCCESS with a pytask.lock entry and the exit code is OK, while the state file keeps the error."""
    jobmon = FakeJobmon(error_after=len(PLAN_ORDER))

    session = _chain_build(tmp_path, jobmon)

    [jobmon_run] = jobmon.runs
    assert isinstance(jobmon_run.error, RuntimeError), repr(jobmon_run.error)
    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SUCCESS)
    assert set(lockfile_entries(_root(tmp_path))) == set(PLAN_ORDER)
    assert session.exit_code == ExitCode.OK
    state = _only_state(_root(tmp_path))
    assert state.status == "finished"
    assert state.error == f"RuntimeError: {jobmon_run.error}"
    assert state.pending == ()


# Every step gets exactly one report


@pytest.mark.parametrize(
    "stop", ["node_failure", "no_result", "run_error", "sigterm", "keyboard_interrupt"]
)
def test_settlement_never_calls_pytasks_execute_protocol(tmp_path: Path, stop: str) -> None:
    """With a plugin that records each call of pytask_execute_task_protocol made while dry_run is False, a bridged build of a -> b -> c plus d that stops in the way named records no such call, every step of the plan has exactly one report, and no product records the test process's pid."""
    calls = _ProtocolCalls()
    with pytest.MonkeyPatch.context() as monkeypatch:
        _register_with_each_build(monkeypatch, calls)
        session = _stopped_build(tmp_path, stop)

    assert calls.names == []
    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert os.getpid() not in recorded_pids(registry(tmp_path), PLAN_ORDER)


def test_stop_after_first_failure_still_settles_every_step(tmp_path: Path) -> None:
    """With stop_after_first_failure=True and FakeJobmon(error_after=1), a bridged build of a -> b -> c plus d gives every one of the four steps exactly one report."""
    session = _chain_build(tmp_path, FakeJobmon(error_after=1), stop_after_first_failure=True)

    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)


def test_max_failures_still_settles_every_step(tmp_path: Path) -> None:
    """With max_failures=1, a bridged build of a -> b -> c plus d in which a raises on its node gives every step exactly one report: a FAIL, b and c SKIP_PREVIOUS_FAILED, d SUCCESS."""
    session = _chain_build(tmp_path, FakeJobmon(), failing={A}, max_failures=1)

    assert report_counts(session) == dict.fromkeys(PLAN_ORDER, 1)
    assert outcomes(session) == {
        A: TaskOutcome.FAIL,
        D: TaskOutcome.SUCCESS,
        B: TaskOutcome.SKIP_PREVIOUS_FAILED,
        C: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }


def test_steps_that_fail_the_input_check_are_not_reported_again(tmp_path: Path) -> None:
    """In a bridged build where one stale step fails the bridge's input check (its input is missing) and an unrelated step runs, the failed step and the stale step after it each have exactly one report, and FakeJobmon's plan holds only the unrelated step."""
    artifacts, tasks = _missing_input_steps(tmp_path)
    jobmon = FakeJobmon()

    # With force, planning checks no input, so the bridge's own check finds x's.
    with jobmon:
        session = build(tasks, _root(tmp_path), jobmon=True, force=True)

    assert bound_names(jobmon) == [["u.parquet"]]
    assert report_counts(session) == {"x.parquet": 1, "y.parquet": 1, "u.parquet": 1}
    assert outcomes(session) == {
        "x.parquet": TaskOutcome.FAIL,
        "y.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
        "u.parquet": TaskOutcome.SUCCESS,
    }
    assert artifacts.path_for("u.parquet").exists()


# Run state


def test_the_state_file_is_running_with_the_workflow_id_while_the_workflow_runs(
    tmp_path: Path,
) -> None:
    """A FakeJobmon subclass with workflow_id=4242 that reads latest_run_state(root) inside run sees status 'running', workflow id 4242, workflow_status None, error None and every submitted step pending."""
    root = _root(tmp_path)
    jobmon = _ReadsTheStateWhileRunning(workflow_id=WORKFLOW_ID, root=root)

    _chain_build(tmp_path, jobmon)

    [seen] = jobmon.seen
    assert seen is not None
    assert seen.status == "running"
    assert seen.workflow_id == WORKFLOW_ID
    assert seen.workflow_status is None
    assert seen.error is None
    assert seen.pending == PLAN_ORDER
    [path] = state_files(root)
    assert seen.workdir.resolve() == path.parent.resolve()


def test_the_final_run_state_is_left_in_the_sessions_configuration(tmp_path: Path) -> None:
    """After a bridged build, session.config[RUN_STATE_KEY] equals read_run_state of the run's state file."""
    session = _chain_build(tmp_path, FakeJobmon(workflow_id=WORKFLOW_ID), failing={B})

    [path] = state_files(_root(tmp_path))
    assert session.config[RUN_STATE_KEY] == read_run_state(path)


def test_a_build_that_submits_nothing_writes_no_state_and_leaves_none(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A second bridged build of steps with stable code identities and nothing changed writes no new state file, and its session.config has no RUN_STATE_KEY."""
    tasks = stable(chain_and_independent(registry(tmp_path)))
    root = _root(tmp_path)
    build(tasks, root, jobmon=True)
    first_states = state_files(root)
    assert len(first_states) == 1

    session = build(tasks, root, jobmon=True)

    assert outcomes(session) == dict.fromkeys(PLAN_ORDER, TaskOutcome.SKIP_UNCHANGED)
    assert state_files(root) == first_states
    assert RUN_STATE_KEY not in session.config


def test_a_refused_bind_writes_no_state_file(tmp_path: Path) -> None:
    """A bridged build whose bind FakeJobmon refuses with JobmonBindError fails with pytask.ExecutionError as before, and its run directory holds no state file."""
    root = _root(tmp_path)

    session = _chain_build(tmp_path, _RefusingBind())

    assert session.exit_code == ExitCode.FAILED
    [run_directory] = (root / RUNS_DIR).iterdir()
    assert run_directory.is_dir()
    assert state_files(root) == []


def test_two_builds_in_a_row_write_two_distinct_state_files(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Two bridged builds of the same always-stale steps, run back to back, leave two state files in two run directories, each 'finished'."""
    tasks = chain_and_independent(registry(tmp_path))
    root = _root(tmp_path)

    build(tasks, root, jobmon=True)
    build(tasks, root, jobmon=True)

    paths = state_files(root)
    assert len(paths) == 2
    assert len({path.parent for path in paths}) == 2
    assert [read_run_state(path).status for path in paths] == ["finished", "finished"]


# Resuming


def test_the_next_build_after_a_stop_runs_exactly_the_steps_not_recorded(
    tmp_path: Path,
) -> None:
    """With stable code identities, after a build of a -> b -> c plus d that FakeJobmon(error_after=2) stops, the next bridged build binds a plan of exactly the steps the state file lists as pending, and reports the recorded steps SKIP_UNCHANGED."""
    _chain_build(tmp_path, FakeJobmon(error_after=2), stable_ids=True)
    pending = _only_state(_root(tmp_path)).pending
    assert pending == (B, C)
    jobmon = FakeJobmon()

    session = _chain_build(tmp_path, jobmon, stable_ids=True)

    assert bound_names(jobmon) == [list(pending)]
    reported = outcomes(session)
    assert reported[A] == TaskOutcome.SKIP_UNCHANGED
    assert reported[D] == TaskOutcome.SKIP_UNCHANGED


def test_with_the_default_code_identity_the_next_build_runs_every_step_again(
    tmp_path: Path,
) -> None:
    """With the default always-stale code identity, after a build of a -> b -> c plus d in which b fails on its node, the next bridged build binds a plan of all four steps."""
    _chain_build(tmp_path, FakeJobmon(), failing={B})
    jobmon = FakeJobmon()

    _chain_build(tmp_path, jobmon, failing={B})

    assert bound_names(jobmon) == [list(PLAN_ORDER)]
