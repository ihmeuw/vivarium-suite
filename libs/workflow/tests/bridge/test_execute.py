"""Tests for running a build's stale steps through Jobmon (``bridge.execute``).

Every build here runs through ``FakeJobmon``, which runs each plan entry's command, and so
the real node runner, in a child process.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytask
import pytest
from pluggy import HookRelay
from pytask import (
    ExecutionReport,
    ExitCode,
    Mark,
    PathNode,
    PTask,
    Session,
    TaskOutcome,
    TaskWithoutPath,
)

from tests.bridge import step_targets
from vivarium.workflow.backend.build import build, outcomes, to_pytask_task
from vivarium.workflow.backend.env import resolve_env_bin_path
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import (
    JobmonBindError,
    JobmonEnvironmentError,
    JobmonRun,
    PlanEntry,
)
from vivarium.workflow.bridge.execute import (
    PAYLOAD_SUFFIX,
    RUNS_DIR,
    _new_run_directory,
    _plan,
)
from vivarium.workflow.bridge.payload import payload_for
from vivarium.workflow.bridge.runner import RESULT_SUFFIX, read_result, runner_command
from vivarium.workflow.frontend.python_step import python_step
from vivarium.workflow.harness import FakeJobmon, build_fixture, stable_code_id

_CHAIN: dict[str, list[str]] = {
    "a.parquet": [],
    "b.parquet": ["a.parquet"],
    "c.parquet": ["b.parquet"],
}
"""A chain a -> b -> c, as each step and the steps it reads."""

_DIAMOND: dict[str, list[str]] = {
    "a.parquet": [],
    "b.parquet": ["a.parquet"],
    "c.parquet": ["a.parquet"],
    "d.parquet": ["b.parquet", "c.parquet"],
}
"""A diamond a -> (b, c) -> d, as each step and the steps it reads."""

_AFTER_STALE: dict[str, list[str]] = {
    "a.parquet": [],
    "u.parquet": [],
    "y.parquet": ["a.parquet", "raw"],
    "z.parquet": ["y.parquet"],
    "w.parquet": ["z.parquet"],
}
"""A chain a -> y -> z -> w and an unrelated step u, as each step and what it reads. Step y
also reads the root input ``raw``."""

_REFUSED = "refused_step"
"""Name of the step that the refusal tests give a run that cannot cross to a node."""

_UNLOADABLE = "unloadable_step"
"""Name of the step whose input cannot be loaded on the controller."""

_TOOL_ENV = "tool_env"
"""Name of the conda env that the fake conda lists in the env tests."""

_CHECK_MESSAGE = 'Jobmon is not ready, on purpose: pip install "vivarium-workflow[cluster]"'
"""Message of the error that a stand-in Jobmon environment check raises."""

_QUEUE_MESSAGE = (
    "ValueError: A queue name must be provided in the specified compute resources."
)
"""Message of the JobmonBindError that a stand-in bind raises when no queue is named. It
has the adapter's form, the type and message of Jobmon's error."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
"""An ANSI escape sequence, which pytask's console writes when it styles its output."""

_RUN_DIRECTORY_NAME = re.compile(r"\d{8}-\d{6}-[0-9a-fA-F]{6}")
"""The name of a run directory: a ``YYYYmmdd-HHMMSS`` time stamp and six hex digits."""


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


def _steps(registry: ArtifactRegistry, graph: Mapping[str, Sequence[str]]) -> list[Task]:
    """Return one python step per key of ``graph``, reading the steps that the key lists.

    A step that reads nothing writes a constant frame, and any other step stacks the frames
    it reads. ``graph`` lists each step after the steps it reads.
    """
    tasks: list[Task] = []
    for name, upstreams in graph.items():
        inputs = {f"upstream_{index}": upstream for index, upstream in enumerate(upstreams)}
        if upstreams:
            target: Callable[..., pd.DataFrame] = step_targets.stack
        else:
            target = step_targets.constant
        tasks.append(python_step(target, inputs=inputs, produces=name, registry=registry))
    return tasks


def _stable(tasks: Sequence[Task], changed: Collection[str] = ()) -> list[Task]:
    """Return ``tasks`` with stable code identities, changed for the steps in ``changed``."""
    stable: list[Task] = []
    for task in tasks:
        version = "2" if task.name in changed else "1"
        stable.append(replace(task, code_id=stable_code_id(f"{task.name}::code", version)))
    return stable


def _steps_with_missing_raw(
    directory: Path, graph: Mapping[str, Sequence[str]]
) -> tuple[ArtifactRegistry, list[Task]]:
    """Return a registry under ``directory`` and the steps of ``graph`` wired through it.

    The steps can read the root input ``raw``, the file ``directory / "raw.parquet"``. It
    exists while the steps are wired, as the registry requires, and is deleted afterwards.
    """
    raw = _mkdir(directory) / "raw.parquet"
    pd.DataFrame({"n": [1]}).to_parquet(raw)
    registry = ArtifactRegistry(directory / "out", "run1", root_inputs={"raw": raw})
    tasks = _steps(registry, graph)
    raw.unlink()
    return registry, tasks


def _signatures(session: Session) -> dict[str, str]:
    """Return the pytask signature of each of the session's tasks, by name."""
    return {task.name: task.signature for task in session.tasks}


def _bound_names(jobmon: FakeJobmon) -> list[list[str]]:
    """Return the step names of each plan that ``jobmon`` bound, in plan order."""
    return [[entry.name for entry in plan] for plan in jobmon.plans]


def _entries(jobmon: FakeJobmon) -> dict[str, PlanEntry]:
    """Return the entries of the one plan that ``jobmon`` bound, by step name."""
    [plan] = jobmon.plans
    return {entry.name: entry for entry in plan}


def _run_directories(root: Path) -> list[Path]:
    """Return everything that bridged builds left in ``root / RUNS_DIR``."""
    runs = root / RUNS_DIR
    return sorted(runs.iterdir()) if runs.exists() else []


def _recipe(entry: PlanEntry) -> Path:
    """Return the recipe file that a plan entry's command runs."""
    words = shlex.split(entry.command)
    [recipe] = [Path(word) for word in words if word.endswith(PAYLOAD_SUFFIX)]
    return recipe


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


def _squashed(text: str) -> str:
    """Return ``text`` without ANSI escapes or whitespace, so that wrapped lines join up."""
    return "".join(_ANSI.sub("", text).split())


def _pytask_build(tasks: Sequence[PTask], root: Path, **kwargs: Any) -> Session:
    """Build already-converted pytask tasks under ``root``, as the backend's build does."""
    root.mkdir(parents=True, exist_ok=True)
    config = root / "pytask.toml"
    config.write_text("[tool.pytask.ini_options]\n")
    return pytask.build(tasks=list(tasks), config=config, task_files=(), **kwargs)


def _with_mark(task: Task, mark: str) -> TaskWithoutPath:
    """Return ``task`` as a pytask task that carries the pytask mark named ``mark``."""
    pytask_task = to_pytask_task(task)
    pytask_task.markers.append(Mark(mark, (), {}))
    return pytask_task


def _in_main() -> pd.DataFrame:
    """Stand in for a step function defined in a script or a notebook."""
    return pd.DataFrame({"n": [1]})


setattr(_in_main, "__module__", "__main__")


def _in_main_reading(upstream: Path) -> pd.DataFrame:
    """Stand in for a function in a script or a notebook that reads an upstream frame."""
    return pd.read_parquet(upstream)


setattr(_in_main_reading, "__module__", "__main__")


def _write_by_hand(product: Path) -> None:
    """Stand in for a task run written by hand rather than by python_step."""
    product.write_text("ran")


def _foreign(product: Path) -> None:
    """Stand in for a pytask task function written without vivarium.workflow."""
    product.write_text("ran")


def _refused_step(
    registry: ArtifactRegistry, run: str, name: str = _REFUSED
) -> tuple[Task, Path]:
    """Return a step that cannot run on a node, for the reason ``run`` names, and its product.

    ``run`` is ``"main"`` for a target defined in ``__main__``, ``"lambda"`` for a lambda
    target, and ``"hand_built"`` for a task whose run is a plain function. ``name`` is the
    step's name, which also names its product.
    """
    if run == "hand_built":
        product_key = f"{name}.txt"
        task = Task(
            name=name,
            run=_write_by_hand,
            inputs={},
            outputs={"product": registry.register_output(product_key)},
            resources={},
            env=None,
        )
        return task, registry.path_for(product_key)
    if run == "main":
        target: Callable[[], pd.DataFrame] = _in_main
    else:
        target = lambda: pd.DataFrame({"n": [1]})
    task = python_step(
        target, inputs={}, produces=f"{name}.parquet", registry=registry, name=name
    )
    return task, registry.path_for(f"{name}.parquet")


def _tool_step(name: str, probe_tool: str, directory: Path) -> Task:
    """Return a command step in the tool env that writes what the probe tool says."""
    product = directory / f"{name}.txt"
    return Task(
        name=name,
        run=f"{probe_tool} {name} > {shlex.quote(str(product))}",
        inputs={},
        outputs={"said": file_node(product)},
        resources={},
        env=_TOOL_ENV,
    )


class _LoadFailed(Exception):
    """The error that an unloadable input raises when it is loaded."""


@dataclass
class _UnloadableNode:
    """An input that pytask finds present when it plans, but whose value cannot be loaded."""

    name: str
    """The node's name."""
    attributes: dict[Any, Any] = field(default_factory=dict)
    """pytask node attributes; unused, kept to satisfy the node protocol."""

    @property
    def signature(self) -> str:
        """Return the node's name, which no other node in the test shares."""
        return self.name

    def state(self) -> str | None:
        """Return a fixed state, so that planning finds the input present."""
        return "present"

    def load(self, is_product: bool = False) -> Any:
        """Fail, as loading a corrupt or unreachable input does."""
        raise _LoadFailed(self.name)

    def save(self, value: Any) -> None:
        """Reject saves: this is a dependency, not a product."""
        raise NotImplementedError(f"{self.name} is a dependency and cannot be saved.")


class _PlanningFailed(Exception):
    """The error that the stand-in task protocol raises."""


class _RaisingProtocol:
    """Stand in for pytask's hooks, with a task protocol that raises."""

    def __init__(self, hook: HookRelay) -> None:
        self._hook = hook
        self.dry_run_seen: list[object] = []

    def __getattr__(self, name: str) -> Any:
        """Return pytask's own hook called ``name``."""
        return getattr(self._hook, name)

    def pytask_execute_task_protocol(
        self, *, session: Session, task: PTask
    ) -> ExecutionReport:
        """Record the session's ``dry_run`` flag, then raise."""
        self.dry_run_seen.append(session.config["dry_run"])
        raise _PlanningFailed(task.name)


@dataclass
class _RefusingBind(FakeJobmon):
    """Stand in for the adapter, refusing every bind as it does when no queue is named."""

    refused: list[list[PlanEntry]] = field(default_factory=list, init=False)
    """Every plan whose bind was refused, in the order refused."""

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
        """Record the plan, then refuse it as the adapter does when no queue is named."""
        self.refused.append(list(plan))
        raise JobmonBindError(_QUEUE_MESSAGE)


# End to end


def test_a_jobmon_build_runs_every_stale_step_in_one_plan_and_in_other_processes(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: a two-step python_step chain whose targets save their process id in their products, built with jobmon=True, binds exactly one plan with both steps in topological order; the build exits OK, both products exist, and each recorded pid differs from the test process's."""
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

    assert session.exit_code == ExitCode.OK
    assert _bound_names(fake_jobmon) == [["first.parquet", "second.parquet"]]
    first_pids = pd.read_parquet(registry.path_for("first.parquet"))["pid"].tolist()
    second_pids = pd.read_parquet(registry.path_for("second.parquet"))["pid"].tolist()
    assert len(first_pids) == 1
    assert len(second_pids) == 2
    assert second_pids[0] == first_pids[0]
    assert os.getpid() not in first_pids + second_pids


def test_the_harness_fixture_through_the_bridge_writes_the_products_a_local_build_writes(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: the harness fixture built with jobmon=True writes products equal to those of a local build of an identical fixture."""
    local = build_fixture(_mkdir(tmp_path / "local"))
    ran = dict.fromkeys(local.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(local.tasks, local.state)) == ran
    bridged = build_fixture(_mkdir(tmp_path / "bridged"))

    session = build(bridged.tasks, bridged.state, jobmon=True)

    assert session.exit_code == ExitCode.OK
    assert _bound_names(fake_jobmon) == [list(bridged.steps)]
    for step in local.steps:
        produced = pd.read_parquet(bridged.registry.path_for(step))
        expected = pd.read_parquet(local.registry.path_for(step))
        pd.testing.assert_frame_equal(produced, expected)


# Planning


def test_unchanged_steps_are_reported_skip_unchanged_and_left_out_of_the_plan(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stable code identities and one step's version changed after a first build, the second jobmon build reports the unchanged upstream steps SKIP_UNCHANGED in session.execution_reports, and the plan holds only the changed step and the steps after it."""
    tasks = _steps(_registry(tmp_path), _CHAIN)
    root = tmp_path / "state"
    ran = dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert outcomes(build(_stable(tasks), root)) == ran

    session = build(_stable(tasks, changed={"b.parquet"}), root, jobmon=True)

    assert outcomes(session)["a.parquet"] == TaskOutcome.SKIP_UNCHANGED
    assert _bound_names(fake_jobmon) == [["b.parquet", "c.parquet"]]


def test_nothing_stale_creates_no_run_directory_and_binds_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """When every step is unchanged, a jobmon build reports every step SKIP_UNCHANGED, binds no plan, and creates no directory under <root>/.pytask/jobmon."""
    tasks = _stable(_steps(_registry(tmp_path), _CHAIN))
    root = tmp_path / "state"
    assert outcomes(build(tasks, root)) == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)

    session = build(tasks, root, jobmon=True)

    assert outcomes(session) == dict.fromkeys(_CHAIN, TaskOutcome.SKIP_UNCHANGED)
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []


def test_force_submits_every_step(tmp_path: Path, fake_jobmon: FakeJobmon) -> None:
    """With stable code identities and nothing changed, a jobmon build with force=True binds a plan holding every step."""
    tasks = _stable(_steps(_registry(tmp_path), _CHAIN))
    root = tmp_path / "state"
    assert outcomes(build(tasks, root)) == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)

    build(tasks, root, jobmon=True, force=True)

    assert _bound_names(fake_jobmon) == [list(_CHAIN)]


@pytest.mark.parametrize("selection", ["expression", "marker_expression"])
def test_deselected_steps_report_skip_and_are_not_submitted(
    tmp_path: Path, selection: str, fake_jobmon: FakeJobmon
) -> None:
    """Selecting one step through pytask's own selection (expression= or a marker plus marker_expression=) in a jobmon build reports the deselected steps SKIP and leaves them out of the plan, while the selected step and its ancestors are submitted."""
    registry = _registry(tmp_path)
    graph: dict[str, list[str]] = {
        "alpha.parquet": [],
        "beta.parquet": ["alpha.parquet"],
        "gamma.parquet": ["alpha.parquet"],
    }
    tasks = _steps(registry, graph)
    root = tmp_path / "state"

    if selection == "expression":
        session = build(tasks, root, jobmon=True, expression="beta")
    else:
        alpha, beta, gamma = tasks
        marked = [to_pytask_task(alpha), _with_mark(beta, "selected"), to_pytask_task(gamma)]
        session = _pytask_build(marked, root, jobmon=True, marker_expression="selected")

    assert outcomes(session)["gamma.parquet"] == TaskOutcome.SKIP
    assert _bound_names(fake_jobmon) == [["alpha.parquet", "beta.parquet"]]
    assert not registry.path_for("gamma.parquet").exists()


def test_a_persisted_step_between_two_stale_steps_is_crossed_by_the_dependency(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a chain a -> b -> c with b marked persist and its product present, b reports PERSISTENCE, the plan holds a and c, and c's entry waits for a."""
    tasks = _steps(_registry(tmp_path), _CHAIN)
    root = tmp_path / "state"
    assert outcomes(build(tasks, root)) == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    pytask_tasks = [
        _with_mark(task, "persist") if task.name == "b.parquet" else to_pytask_task(task)
        for task in tasks
    ]

    session = _pytask_build(pytask_tasks, root, jobmon=True)

    assert outcomes(session)["b.parquet"] == TaskOutcome.PERSISTENCE
    entries = _entries(fake_jobmon)
    assert list(entries) == ["a.parquet", "c.parquet"]
    assert entries["c.parquet"].upstreams == (entries["a.parquet"].signature,)


def test_a_missing_input_fails_its_step_at_plan_time_and_unrelated_steps_still_run(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A step whose root input file is missing reports FAIL and its downstream step SKIP_PREVIOUS_FAILED, neither is in the plan, and an unrelated stale step is still submitted and writes its product."""
    raw = tmp_path / "raw.parquet"
    pd.DataFrame({"n": [1]}).to_parquet(raw)
    registry = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"raw": raw})
    graph: dict[str, list[str]] = {
        "x.parquet": ["raw"],
        "y.parquet": ["x.parquet"],
        "z.parquet": [],
    }
    tasks = _steps(registry, graph)
    raw.unlink()

    session = build(tasks, tmp_path / "state", jobmon=True)

    reported = outcomes(session)
    assert reported["x.parquet"] == TaskOutcome.FAIL
    assert reported["y.parquet"] == TaskOutcome.SKIP_PREVIOUS_FAILED
    assert _bound_names(fake_jobmon) == [["z.parquet"]]
    assert registry.path_for("z.parquet").exists()


def test_stop_after_first_failure_ends_planning_at_a_missing_input_and_submits_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stop_after_first_failure, when the first two steps in plan order both read a missing root input, a jobmon build reports only the first of them, as FAIL, and exits FAILED: planning ends at that step, so the second, which would also fail at plan time, gets no report. An unrelated stale step later in plan order is not submitted and its product is not written, no plan is bound, and no run directory exists."""
    raw = tmp_path / "raw.parquet"
    pd.DataFrame({"n": [1]}).to_parquet(raw)
    registry = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"raw": raw})
    graph: dict[str, list[str]] = {
        "a.parquet": ["raw"],
        "b.parquet": ["raw"],
        "z.parquet": [],
    }
    tasks = _steps(registry, graph)
    raw.unlink()
    root = tmp_path / "state"

    session = build(tasks, root, jobmon=True, stop_after_first_failure=True)

    assert session.exit_code == ExitCode.FAILED
    # A stale step gets no report either way; b would get a FAIL report if planning went on.
    assert outcomes(session) == {"a.parquet": TaskOutcome.FAIL}
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []
    assert not registry.path_for("z.parquet").exists()


def test_stop_after_first_failure_submits_none_of_the_stale_steps_planned_before_it(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stop_after_first_failure, when a stale step comes before a step with a missing root input in plan order, a jobmon build reports the failing step FAIL and exits FAILED, gives the step after it no report, binds no plan, creates no run directory, and does not write the earlier stale step's product."""
    raw = tmp_path / "raw.parquet"
    pd.DataFrame({"n": [1]}).to_parquet(raw)
    registry = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"raw": raw})
    graph: dict[str, list[str]] = {
        "a.parquet": [],
        "m.parquet": ["raw"],
        "z.parquet": [],
    }
    tasks = _steps(registry, graph)
    raw.unlink()
    root = tmp_path / "state"

    session = build(tasks, root, jobmon=True, stop_after_first_failure=True)

    assert session.exit_code == ExitCode.FAILED
    reported = outcomes(session)
    assert reported["m.parquet"] == TaskOutcome.FAIL
    assert "z.parquet" not in reported
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []
    assert not registry.path_for("a.parquet").exists()


def test_planning_leaves_dry_run_false_when_the_protocol_raises(tmp_path: Path) -> None:
    """_plan, given a stand-in session whose pytask_execute_task_protocol hook raises, re-raises and leaves session.config['dry_run'] False."""
    fx = build_fixture(tmp_path)
    session = build(fx.tasks, fx.state, dry_run=True)
    hooks = _RaisingProtocol(session.hook)
    session.hook = cast(HookRelay, hooks)
    session.config["dry_run"] = False

    with pytest.raises(_PlanningFailed):
        _plan(session)

    assert hooks.dry_run_seen == [True]
    assert session.config["dry_run"] is False


def test_dry_run_is_false_after_a_jobmon_build(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """After a jobmon build with stale steps, session.config['dry_run'] is False."""
    fx = build_fixture(tmp_path)

    session = build(fx.tasks, fx.state, jobmon=True)

    assert _bound_names(fake_jobmon) == [list(fx.steps)]
    assert session.config["dry_run"] is False


def test_the_plan_is_sorted_by_name_within_each_wave(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Three independent steps created in reverse name order are bound in name order, and a step after them comes last."""
    graph: dict[str, list[str]] = {
        "d.parquet": [],
        "c.parquet": [],
        "b.parquet": [],
        "a.parquet": ["d.parquet", "c.parquet", "b.parquet"],
    }

    build(_steps(_registry(tmp_path), graph), tmp_path / "state", jobmon=True)

    expected = ["b.parquet", "c.parquet", "d.parquet", "a.parquet"]
    assert _bound_names(fake_jobmon) == [expected]


# Dependencies between plan entries


def test_each_entry_of_a_chain_waits_for_the_step_before_it(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a chain a -> b -> c, the entries' upstreams are () for a, (a,) for b and (b,) for c, as signatures."""
    session = build(_steps(_registry(tmp_path), _CHAIN), tmp_path / "state", jobmon=True)

    signature = _signatures(session)
    upstreams = {name: entry.upstreams for name, entry in _entries(fake_jobmon).items()}
    assert upstreams == {
        "a.parquet": (),
        "b.parquet": (signature["a.parquet"],),
        "c.parquet": (signature["b.parquet"],),
    }


def test_the_last_step_of_a_diamond_waits_for_both_branches(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a diamond a -> (b, c) -> d, d's entry waits for exactly b and c, and b and c each wait for a."""
    session = build(_steps(_registry(tmp_path), _DIAMOND), tmp_path / "state", jobmon=True)

    signature = _signatures(session)
    entries = _entries(fake_jobmon)
    branches = sorted([signature["b.parquet"], signature["c.parquet"]])
    assert sorted(entries["d.parquet"].upstreams) == branches
    assert entries["b.parquet"].upstreams == (signature["a.parquet"],)
    assert entries["c.parquet"].upstreams == (signature["a.parquet"],)


def test_an_entry_does_not_wait_for_a_step_that_is_not_stale(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stable code identities, when only c of a -> b -> c changed, c's entry has no upstreams."""
    tasks = _steps(_registry(tmp_path), _CHAIN)
    root = tmp_path / "state"
    ran = dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert outcomes(build(_stable(tasks), root)) == ran

    build(_stable(tasks, changed={"c.parquet"}), root, jobmon=True)

    entries = _entries(fake_jobmon)
    assert list(entries) == ["c.parquet"]
    assert entries["c.parquet"].upstreams == ()


# Checking the inputs of stale steps


def test_a_missing_input_after_a_stale_step_fails_its_step_and_the_others_still_run(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a first build of a -> y -> z -> w and an unrelated step u, where y also reads a root input file that is missing, a jobmon build reports y FAIL with a message naming y and the missing file's full path and both z and w SKIP_PREVIOUS_FAILED, binds one plan holding only a and u, in that order, and writes the products of a and u but not those of y, z and w."""
    registry, tasks = _steps_with_missing_raw(tmp_path, _AFTER_STALE)
    missing = (tmp_path / "raw.parquet").resolve()

    session = build(tasks, tmp_path / "state", jobmon=True)

    message = str(_failure(_report(session, "y.parquet")))
    assert "y.parquet" in message, message
    assert str(missing) in message, message
    assert _report(session, "z.parquet").outcome == TaskOutcome.SKIP_PREVIOUS_FAILED
    assert _report(session, "w.parquet").outcome == TaskOutcome.SKIP_PREVIOUS_FAILED
    assert _bound_names(fake_jobmon) == [["a.parquet", "u.parquet"]]
    assert registry.path_for("a.parquet").exists()
    assert registry.path_for("u.parquet").exists()
    assert not registry.path_for("y.parquet").exists()
    assert not registry.path_for("z.parquet").exists()
    assert not registry.path_for("w.parquet").exists()


def test_a_missing_input_that_a_stale_step_writes_does_not_fail_its_step(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a first build of a chain a -> b -> c, where the files that b and c read do not exist before the build because a and b write them, a jobmon build reports no step FAIL, binds one plan holding all three steps, exits OK and writes every product."""
    registry = _registry(tmp_path)
    tasks = _steps(registry, _CHAIN)
    assert not any(registry.path_for(step).exists() for step in _CHAIN)

    session = build(tasks, tmp_path / "state", jobmon=True)

    reported = outcomes(session)
    assert TaskOutcome.FAIL not in reported.values(), reported
    assert _bound_names(fake_jobmon) == [list(_CHAIN)]
    assert session.exit_code == ExitCode.OK
    assert all(registry.path_for(step).exists() for step in _CHAIN)


def test_a_missing_input_failure_has_no_chained_error_or_traceback(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """The FAIL report of a step after a stale step whose root input file is missing holds an exception with no __cause__, no __context__ and no traceback, and the report's exc_info holds no traceback."""
    _, tasks = _steps_with_missing_raw(tmp_path, _AFTER_STALE)

    session = build(tasks, tmp_path / "state", jobmon=True)

    report = _report(session, "y.parquet")
    error = _failure(report)
    assert report.exc_info is not None and report.exc_info[2] is None
    assert error.__traceback__ is None
    assert error.__cause__ is None
    assert error.__context__ is None


def test_when_every_stale_step_fails_the_input_check_no_run_is_created_or_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With force=True, under which pytask's dry-run checks no step's inputs, a jobmon build of one step whose root input file is missing reports that step FAIL and exits FAILED, never calls the Jobmon environment check, binds no plan and creates no directory under <root>/.pytask/jobmon."""
    registry, tasks = _steps_with_missing_raw(tmp_path, {"x.parquet": ["raw"]})
    root = tmp_path / "state"
    checked: list[str] = []

    def refuse(cluster: str) -> None:
        """Record the check, then fail it."""
        checked.append(cluster)
        raise JobmonEnvironmentError(_CHECK_MESSAGE)

    with FakeJobmon() as jobmon, monkeypatch.context() as patch:
        patch.setattr(_jobmon_adapter, "check_environment", refuse)
        session = build(tasks, root, jobmon=True, force=True)

    assert session.exit_code == ExitCode.FAILED
    assert outcomes(session) == {"x.parquet": TaskOutcome.FAIL}
    assert checked == []
    assert jobmon.plans == []
    assert _run_directories(root) == []
    assert not registry.path_for("x.parquet").exists()


def test_a_missing_input_after_a_stale_step_gets_the_outcomes_of_a_local_build(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """The pipeline a -> y -> z -> w and an unrelated step u, with y's root input file missing, built once locally and once with jobmon=True under separate roots, reports y FAIL and both z and w SKIP_PREVIOUS_FAILED in both builds."""
    local, bridged = tmp_path / "local", tmp_path / "bridged"
    _, local_tasks = _steps_with_missing_raw(local, _AFTER_STALE)
    _, bridged_tasks = _steps_with_missing_raw(bridged, _AFTER_STALE)
    expected = {
        "y.parquet": TaskOutcome.FAIL,
        "z.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
        "w.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    local_outcomes = outcomes(build(local_tasks, local / "state"))
    assert {step: local_outcomes.get(step) for step in expected} == expected

    session = build(bridged_tasks, bridged / "state", jobmon=True)

    bridged_outcomes = outcomes(session)
    assert {step: bridged_outcomes.get(step) for step in expected} == expected


def test_a_refused_step_after_a_failed_input_check_is_skipped_and_the_others_still_run(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """In a first build of a -> y -> z -> w and an unrelated step u, where y also reads a root input file that is missing and a step that cannot run on a node reads y's product and writes its own into a directory that does not exist, a jobmon build reports y FAIL and the refused step once, as SKIP_PREVIOUS_FAILED, binds one plan holding only a and u, in that order, and does not create the refused step's product directory."""
    registry, tasks = _steps_with_missing_raw(tmp_path, _AFTER_STALE)
    refused = python_step(
        _in_main_reading,
        inputs={"upstream": "y.parquet"},
        produces="later/refused.parquet",
        registry=registry,
        name=_REFUSED,
    )
    product = registry.path_for("later/refused.parquet")
    assert not product.parent.exists()

    session = build([*tasks, refused], tmp_path / "state", jobmon=True)

    assert _report(session, "y.parquet").outcome == TaskOutcome.FAIL
    # The refusal check runs only on the steps to submit, so it adds no FAIL report here.
    names = [report.task.name for report in session.execution_reports]
    assert names.count(_REFUSED) == 1, names
    assert _report(session, _REFUSED).outcome == TaskOutcome.SKIP_PREVIOUS_FAILED
    assert _bound_names(fake_jobmon) == [["a.parquet", "u.parquet"]]
    assert registry.path_for("u.parquet").exists()
    assert not product.parent.exists()


def test_stop_after_first_failure_ends_the_input_check_and_submits_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With stop_after_first_failure, in a first build of a -> y -> z -> w and an unrelated step u, where y also reads a root input file that is missing, a jobmon build reports y FAIL and exits FAILED, gives z and w no report, binds no plan, creates no run directory, and writes the products of neither a nor u."""
    registry, tasks = _steps_with_missing_raw(tmp_path, _AFTER_STALE)
    root = tmp_path / "state"

    session = build(tasks, root, jobmon=True, stop_after_first_failure=True)

    assert session.exit_code == ExitCode.FAILED
    reported = outcomes(session)
    assert reported["y.parquet"] == TaskOutcome.FAIL
    # Without the stop, z and w would be reported SKIP_PREVIOUS_FAILED after y's FAIL.
    assert "z.parquet" not in reported, reported
    assert "w.parquet" not in reported, reported
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []
    assert not registry.path_for("a.parquet").exists()
    assert not registry.path_for("u.parquet").exists()


# Refusing steps that cannot run on a node


@pytest.mark.parametrize("run", ["main", "lambda", "hand_built"])
def test_a_step_that_cannot_run_on_a_node_fails_naming_it_and_nothing_is_submitted(
    tmp_path: Path, run: str, fake_jobmon: FakeJobmon
) -> None:
    """A stale step whose python_step target is in __main__ or is a lambda, or whose run is a hand-built callable, reports FAIL with a message naming the step; no plan is bound, no run directory exists, and no step body ran."""
    task, product = _refused_step(_registry(tmp_path), run)
    root = tmp_path / "state"

    session = build([task], root, jobmon=True)

    assert _REFUSED in str(_failure(_report(session, _REFUSED)))
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []
    assert not product.exists()


def test_a_pytask_task_not_built_by_the_backend_is_refused_with_a_plain_message(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A stale pytask task with no TASK_KEY attribute, built with pytask.build and jobmon=True, reports FAIL with a message naming the task and saying it was not built by vivarium.workflow, and no plan is bound."""
    product = tmp_path / "foreign.txt"
    task = TaskWithoutPath(
        name="foreign_task",
        function=_foreign,
        produces={"product": PathNode.from_path(product)},
    )

    session = _pytask_build([task], tmp_path / "state", jobmon=True)

    error = _failure(_report(session, "foreign_task"))
    message = str(error)
    assert "foreign_task" in message
    assert "built" in message and "vivarium.workflow" in message, message
    assert error.__cause__ is None
    assert error.__traceback__ is None
    assert fake_jobmon.plans == []
    assert not product.exists()


def test_a_step_whose_arguments_cannot_be_loaded_is_refused_with_a_plain_message(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A stale step with an input node whose load raises on the controller reports FAIL with a message naming the step and no chained error or traceback, no plan is bound, and no run directory exists."""
    registry = _registry(tmp_path)
    task = python_step(
        step_targets.constant,
        inputs={},
        produces="unloadable.parquet",
        registry=registry,
        name=_UNLOADABLE,
    )
    task = replace(task, inputs={"broken": _UnloadableNode("unloadable_input")})
    root = tmp_path / "state"

    session = build([task], root, jobmon=True)

    report = _report(session, _UNLOADABLE)
    error = _failure(report)
    assert _UNLOADABLE in str(error), str(error)
    assert report.exc_info is not None and report.exc_info[2] is None
    assert error.__traceback__ is None
    assert error.__cause__ is None
    assert error.__context__ is None
    assert fake_jobmon.plans == []
    assert _run_directories(root) == []


def test_a_refusal_shows_its_reason_once_with_no_chained_error_or_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fake_jobmon: FakeJobmon
) -> None:
    """The FAIL report of a refused step holds a ValueError with no __cause__, no __context__ and no traceback, and the build's console output shows the refusal's reason exactly once."""
    task, _ = _refused_step(_registry(tmp_path), "lambda")
    capsys.readouterr()

    session = build([task], tmp_path / "state", jobmon=True)

    captured = capsys.readouterr()
    report = _report(session, _REFUSED)
    error = _failure(report)
    assert isinstance(error, ValueError)
    assert report.exc_info is not None and report.exc_info[2] is None
    assert error.__traceback__ is None
    assert error.__cause__ is None
    assert error.__context__ is None
    # payload_for's reason for a lambda target says this once; a chained error repeats it.
    output = _squashed(captured.out + captured.err)
    assert output.count(_squashed("it is a lambda")) == 1, captured.out


def test_a_refused_step_stops_every_other_stale_step_from_being_submitted(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """When two of three independent stale steps are refused, each for a different reason, the build exits FAILED, each refused step reports FAIL with a message naming it, no plan is bound, and the third step's product is not written."""
    registry = _registry(tmp_path)
    refused, _ = _refused_step(registry, "lambda")
    also_refused, _ = _refused_step(registry, "hand_built", name="second_refused")
    [fine] = _steps(registry, {"fine.parquet": []})

    session = build([fine, refused, also_refused], tmp_path / "state", jobmon=True)

    assert session.exit_code == ExitCode.FAILED
    for name in (_REFUSED, "second_refused"):
        message = str(_failure(_report(session, name)))
        assert name in message, message
    assert fake_jobmon.plans == []
    assert not registry.path_for("fine.parquet").exists()


# The run directory and the recipes


@pytest.mark.usefixtures("fake_jobmon")
def test_each_run_writes_its_recipes_into_a_new_run_directory(tmp_path: Path) -> None:
    """A jobmon build creates exactly one directory under <root>/.pytask/jobmon named <YYYYmmdd-HHMMSS>-<6 hex digits>, holding <signature>.payload.json and <signature>.result.json for each stale step."""
    fx = build_fixture(tmp_path)

    session = build(fx.tasks, fx.state, jobmon=True)

    [run_directory] = _run_directories(fx.state)
    assert run_directory.is_dir()
    assert _RUN_DIRECTORY_NAME.fullmatch(run_directory.name), run_directory.name
    datetime.strptime(run_directory.name[:15], "%Y%m%d-%H%M%S")
    signature = _signatures(session)
    for step in fx.steps:
        assert (run_directory / f"{signature[step]}{PAYLOAD_SUFFIX}").is_file()
        assert (run_directory / f"{signature[step]}{RESULT_SUFFIX}").is_file()


def test_two_builds_use_different_run_directories(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Two jobmon builds of the same pipeline, one right after the other, leave two run directories, and each build's recipes are only in its own."""
    fx = build_fixture(tmp_path)

    build(fx.tasks, fx.state, jobmon=True)
    build(fx.tasks, fx.state, jobmon=True)

    used: list[Path] = []
    for plan in fake_jobmon.plans:
        [directory] = {_recipe(entry).parent for entry in plan}
        recipes = sorted(path.name for path in directory.glob(f"*{PAYLOAD_SUFFIX}"))
        assert recipes == sorted(f"{entry.signature}{PAYLOAD_SUFFIX}" for entry in plan)
        used.append(directory)
    assert len(used) == 2
    assert sorted(used) == _run_directories(fx.state)


def test_run_directories_made_in_the_same_second_are_distinct(tmp_path: Path) -> None:
    """Five run directories made one right after the other under one root, so that at least two share their time stamp, are five distinct directories, each named <YYYYmmdd-HHMMSS>-<6 hex digits>."""
    _mkdir(tmp_path / RUNS_DIR)

    made = [_new_run_directory(tmp_path) for _ in range(5)]

    for directory in made:
        assert directory.is_dir(), directory
        assert _RUN_DIRECTORY_NAME.fullmatch(directory.name), directory.name
    stamps = [directory.name[:15] for directory in made]
    # Five calls with five different time stamps would have to span three seconds or more.
    assert len(set(stamps)) < len(stamps), stamps
    assert len(set(made)) == len(made), made
    assert sorted(made) == _run_directories(tmp_path)


def test_each_recipe_is_the_codec_recipe_for_its_step(
    tmp_path: Path,
    fake_jobmon: FakeJobmon,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """Each <signature>.payload.json holds what payload_for returns for that step, and the entry's signature is the pytask task's signature."""
    fx = build_fixture(tmp_path)

    session = build(fx.tasks, fx.state, jobmon=True)

    signature = _signatures(session)
    [run_directory] = _run_directories(fx.state)
    [plan] = fake_jobmon.plans
    assert [entry.signature for entry in plan] == [signature[step] for step in fx.steps]
    for task in fx.tasks:
        recipe = run_directory / f"{signature[task.name]}{PAYLOAD_SUFFIX}"
        expected = json.loads(json.dumps(payload_for(task, pytask_kwargs(task))))
        assert json.loads(recipe.read_text()) == expected


def test_each_entry_runs_the_runner_with_this_interpreter_and_this_env_first_on_path(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Each plan entry's command sets PATH to resolve_env_bin_path(sys.prefix) followed by the existing PATH, and then runs sys.executable -m vivarium.workflow.bridge.runner with the entry's recipe and result paths."""
    fx = build_fixture(tmp_path)

    build(fx.tasks, fx.state, jobmon=True)

    [run_directory] = _run_directories(fx.state)
    [plan] = fake_jobmon.plans
    expected_path = f"{resolve_env_bin_path(sys.prefix)}:{os.environ['PATH']}"
    for entry in plan:
        recipe = run_directory / f"{entry.signature}{PAYLOAD_SUFFIX}"
        result = run_directory / f"{entry.signature}{RESULT_SUFFIX}"
        runner = runner_command(recipe, result)
        assert entry.command.endswith(runner), entry.command
        # Run what comes before the runner in front of printenv, to see the PATH it sets.
        set_path = entry.command.removesuffix(runner)
        shown = subprocess.run(
            ["/bin/sh", "-c", f"{set_path}printenv PATH"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert shown.stdout == f"{expected_path}\n", entry.command


def test_a_command_step_without_env_finds_this_envs_executables_first_on_the_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_jobmon: FakeJobmon
) -> None:
    """With a directory holding a decoy 'python' put first on the build's PATH, a command step with env None that runs 'python' through the bridge runs this environment's python, not the decoy."""
    decoy = _mkdir(tmp_path / "decoy") / "python"
    decoy.write_text("#!/bin/sh\necho decoy\n")
    decoy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{decoy.parent}{os.pathsep}{os.environ['PATH']}")
    product = tmp_path / "prefix.txt"
    task = Task(
        name="which_python",
        run=f"python -c 'import sys; print(sys.prefix)' > {shlex.quote(str(product))}",
        inputs={},
        outputs={"prefix": file_node(product)},
        resources={},
        env=None,
    )

    build([task], tmp_path / "state", jobmon=True)

    assert _bound_names(fake_jobmon) == [["which_python"]]
    assert Path(product.read_text().strip()).resolve() == Path(sys.prefix).resolve()


def test_entries_carry_each_steps_name_and_resources(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Each plan entry's name is its step's name and its resources equal the step's Task.resources."""
    fx = build_fixture(tmp_path)
    resources: dict[str, dict[str, Any]] = {
        "mid.parquet": {"memory": 2, "runtime": 600},
        "final.parquet": {"cores": 3, "queue": "long.q"},
    }
    tasks = [replace(task, resources=resources[task.name]) for task in fx.tasks]

    build(tasks, fx.state, jobmon=True)

    [plan] = fake_jobmon.plans
    carried = [(entry.name, dict(entry.resources)) for entry in plan]
    assert carried == [(step, resources[step]) for step in fx.steps]


def test_a_command_step_whose_product_directory_is_missing_succeeds_on_the_node(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A command step after a python step, writing its product into a directory that does not exist yet, succeeds through the bridge, because the bridge creates the product's parent directory before it submits."""
    registry = _registry(tmp_path)
    [first] = _steps(registry, {"first.parquet": []})
    source = registry.path_for("first.parquet")
    copy = tmp_path / "later" / "deeper" / "copy.parquet"
    copy_step = Task(
        name="copy_step",
        run=f"cp {shlex.quote(str(source))} {shlex.quote(str(copy))}",
        inputs={"source": registry.resolve_input("first.parquet")},
        outputs={"copy": file_node(copy)},
        resources={},
        env=None,
    )
    assert not copy.parent.exists()

    build([first, copy_step], tmp_path / "state", jobmon=True)

    entries = _entries(fake_jobmon)
    assert list(entries) == ["first.parquet", "copy_step"]
    [run_directory] = _run_directories(tmp_path / "state")
    result = read_result(run_directory / f"{entries['copy_step'].signature}{RESULT_SUFFIX}")
    assert result["success"] is True, result["exc"]
    assert copy.read_bytes() == source.read_bytes()


def test_each_named_env_is_resolved_once_per_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_jobmon: FakeJobmon,
    fake_conda: Callable[..., Path],
    make_tool_dir: Callable[[Path], Path],
    probe_tool: str,
) -> None:
    """With three command steps declaring the same conda env by name, a jobmon build runs the conda lookup as many times as with one such step."""
    monkeypatch.chdir(tmp_path)
    prefix = tmp_path / "conda_envs" / _TOOL_ENV
    make_tool_dir(prefix / "bin")
    calls = fake_conda(prefix)

    def conda_lookups(count: int) -> int:
        """Return how many conda lookups a build of ``count`` tool env steps runs."""
        before = len(calls.read_text().splitlines())
        said = tmp_path / f"said_{count}"
        tasks = [
            _tool_step(f"tool_{count}_{index}", probe_tool, said) for index in range(count)
        ]
        build(tasks, tmp_path / f"state_{count}", jobmon=True)
        return len(calls.read_text().splitlines()) - before

    with_one, with_three = conda_lookups(1), conda_lookups(3)

    assert [len(plan) for plan in fake_jobmon.plans] == [1, 3]
    said = (tmp_path / "said_3" / "tool_3_2.txt").read_text()
    assert said == f"{probe_tool} says tool_3_2\n"
    assert with_one > 0
    assert with_three == with_one


# The Jobmon environment


def test_a_failed_jobmon_environment_check_fails_the_build_with_its_message_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A jobmon build checks the Jobmon environment once, for the build's jobmon_cluster. When the adapter's check_environment raises JobmonEnvironmentError, the build exits FAILED, its output holds the check's message and no traceback, no plan is bound, no run directory is created, and no step body ran."""
    fx = build_fixture(tmp_path)
    checked: list[str] = []

    def refuse(cluster: str) -> None:
        """Record the cluster, then fail as a missing or unsupported Jobmon install does."""
        checked.append(cluster)
        raise JobmonEnvironmentError(_CHECK_MESSAGE)

    with FakeJobmon() as jobmon, monkeypatch.context() as patch:
        patch.setattr(_jobmon_adapter, "check_environment", refuse)
        capsys.readouterr()
        session = build(fx.tasks, fx.state, jobmon=True, jobmon_cluster="multiprocess")
        captured = capsys.readouterr()

    output = captured.out + captured.err
    assert session.exit_code == ExitCode.FAILED
    assert checked == ["multiprocess"]
    assert _squashed(_CHECK_MESSAGE) in _squashed(output), output
    assert "Traceback" not in output, output
    assert jobmon.plans == []
    assert _run_directories(fx.state) == []
    assert not any(fx.registry.path_for(step).exists() for step in fx.steps)


def test_a_refused_bind_fails_the_build_with_a_plain_message_after_writing_the_recipes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the bind of a FakeJobmon subclass raises JobmonBindError with Jobmon's missing-queue message, the build exits FAILED, its output holds a message that names jobmon_cluster and jobmon_resources and quotes the error's message with no traceback, the one run directory holds every stale step's recipe, and no step body ran."""
    fx = build_fixture(tmp_path)
    jobmon = _RefusingBind()

    with jobmon:
        capsys.readouterr()
        session = build(fx.tasks, fx.state, jobmon=True)
        captured = capsys.readouterr()

    output = captured.out + captured.err
    assert session.exit_code == ExitCode.FAILED
    [refused] = jobmon.refused
    assert [entry.name for entry in refused] == list(fx.steps)
    assert _squashed("jobmon_cluster") in _squashed(output), output
    assert _squashed("jobmon_resources") in _squashed(output), output
    assert _squashed(_QUEUE_MESSAGE) in _squashed(output), output
    assert "Traceback" not in output, output
    [run_directory] = _run_directories(fx.state)
    signature = _signatures(session)
    recipes = sorted(path.name for path in run_directory.glob(f"*{PAYLOAD_SUFFIX}"))
    assert recipes == sorted(f"{signature[step]}{PAYLOAD_SUFFIX}" for step in fx.steps)
    assert not any(fx.registry.path_for(step).exists() for step in fx.steps)


def test_a_build_with_nothing_stale_does_not_check_the_jobmon_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every step is unchanged, a jobmon build exits OK even though check_environment would raise."""
    fx = build_fixture(tmp_path)
    tasks = _stable(fx.tasks)
    assert outcomes(build(tasks, fx.state)) == dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    checked: list[str] = []

    def refuse(cluster: str) -> None:
        """Record the check, then fail it."""
        checked.append(cluster)
        raise JobmonEnvironmentError(_CHECK_MESSAGE)

    with FakeJobmon() as jobmon, monkeypatch.context() as patch:
        patch.setattr(_jobmon_adapter, "check_environment", refuse)
        session = build(tasks, fx.state, jobmon=True)

    assert session.exit_code == ExitCode.OK
    assert checked == []
    assert jobmon.plans == []
