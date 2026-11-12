"""Tests for ``frontend.pipeline.run`` with ``jobmon=True``, under the fake Jobmon adapter.

These cover what the frontend adds to a bridged build: the compute-resource layers, the
project check, the run lock around ``force``, and the errors for a refused or interrupted
run (MIC-7752).
"""

import os
import re
import signal
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pytask import ExitCode, TaskOutcome

from tests.backend.lock_helpers import account_name, held_lock, lock_is_free
from tests.bridge import step_targets
from tests.bridge.helpers import (
    LOCKFILE,
    WORKFLOW_ID,
    A,
    B,
    C,
    bound_names,
    build_root,
    chain_and_independent,
    make_dir,
    recorded_pids,
    registry,
    stable,
)
from vivarium.dagger.backend.lock import RUN_LOCK_NAME, RunLocked
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.dagger.bridge.execute import RemoteTaskFailed
from vivarium.dagger.bridge.plugin import DEFAULT_MAX_ATTEMPTS
from vivarium.dagger.bridge.state import RUNS_DIR, STATE_FILE
from vivarium.dagger.constants import DEFAULT_RESOURCES
from vivarium.dagger.frontend import (
    Pipeline,
    PipelineError,
    RunInterrupted,
    RunRefused,
    StepFailed,
    python_step,
    run,
)
from vivarium.dagger.harness import FakeJobmon

_CHAIN = (A, B, C)
"""The steps of the chain a -> b -> c, in plan order."""

_PROJECT: Mapping[str, Any] = {"project": "proj_test"}
"""The ``jobmon_resources`` that give every step a project."""

_STEP_RESOURCES = re.compile(r"(?<![\w(])resources=")
"""A step's own ``resources=``, which neither ``Pipeline(resources=`` nor
``jobmon_resources=`` matches."""


@pytest.fixture(autouse=True)
def _node_imports(node_pythonpath: None) -> None:
    """Let every node runner in this module import ``tests.bridge.step_targets``."""


@pytest.fixture(autouse=True)
def _sigterm_kept() -> Iterator[None]:
    """Restore SIGTERM's handler after each test, whatever the test left installed."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


@dataclass
class _InterruptedWhileBinding(FakeJobmon):
    """Stand in for Jobmon, and raise KeyboardInterrupt from bind."""

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
        """Raise KeyboardInterrupt, as Ctrl-C while Jobmon binds does."""
        raise KeyboardInterrupt


def _chain(
    tmp_path: Path,
    *,
    resources: Mapping[str, Any] | None = None,
    own: Mapping[str, dict[str, Any]] | None = None,
    stable_ids: bool = False,
) -> Pipeline:
    """Return the chain a -> b -> c under ``tmp_path``; each step records its process id.

    ``resources`` is the pipeline's, ``own`` gives a step's own resources by step name, and
    ``stable_ids`` gives every step a stable code identity.
    """
    artifacts = registry(tmp_path)
    given: Mapping[str, dict[str, Any]] = {} if own is None else own
    sources: dict[str, dict[str, str]] = {A: {}, B: {"upstream": A}, C: {"upstream": B}}
    tasks = [
        python_step(
            step_targets.record_pid,
            inputs=inputs,
            produces=name,
            registry=artifacts,
            resources=given.get(name),
        )
        for name, inputs in sources.items()
    ]
    if stable_ids:
        tasks = stable(tasks)
    layer: Mapping[str, Any] = {} if resources is None else resources
    return Pipeline(tasks, build_root(tmp_path), artifacts, resources=layer)


def _defaults(jobmon: FakeJobmon) -> dict[str, Any]:
    """Return the default resources of the one workflow that ``jobmon`` bound."""
    [options] = jobmon.options
    return dict(options["default_resources"])


def _state_written(root: Path) -> list[str]:
    """Return which of pytask.lock, run.lock and .pytask exist under ``root``."""
    return [name for name in (LOCKFILE, RUN_LOCK_NAME, ".pytask") if (root / name).exists()]


def _interrupted(pipeline: Pipeline, jobmon: FakeJobmon) -> RunInterrupted:
    """Run ``pipeline`` through ``jobmon``; return the RunInterrupted that it raises."""
    raised: BaseException | None = None
    with jobmon:
        # Caught as BaseException, so that a KeyboardInterrupt fails this test only.
        try:
            run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))
        except BaseException as error:
            raised = error
    assert isinstance(raised, RunInterrupted), repr(raised)
    return raised


def _project_error(pipeline: Pipeline, **build_kwargs: Any) -> PipelineError:
    """Return the PipelineError that ``run(pipeline, jobmon=True, ...)`` raises."""
    with pytest.raises(PipelineError) as info:
        run(pipeline, jobmon=True, **build_kwargs)
    return info.value


# Compute resources, in layers


def test_the_default_resources_reach_the_adapter_when_no_layer_overrides_them(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """run(pipeline, jobmon=True, jobmon_resources={'project': 'proj_test'}) on a pipeline with no resources binds one workflow whose default_resources are DEFAULT_RESOURCES plus that project."""
    run(_chain(tmp_path), jobmon=True, jobmon_resources={"project": "proj_test"})

    assert _defaults(fake_jobmon) == {**DEFAULT_RESOURCES, "project": "proj_test"}


def test_pipeline_resources_override_the_defaults_key_by_key(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A Pipeline with resources={'project': 'proj_a', 'memory': 8} binds default_resources with memory 8, project 'proj_a', and the default queue, runtime and cores."""
    pipeline = _chain(tmp_path, resources={"project": "proj_a", "memory": 8})

    run(pipeline, jobmon=True)

    defaults = _defaults(fake_jobmon)
    assert defaults == {**DEFAULT_RESOURCES, "project": "proj_a", "memory": 8}
    for key in ("queue", "runtime", "cores"):
        assert defaults[key] == DEFAULT_RESOURCES[key]


def test_given_jobmon_resources_override_pipeline_resources_key_by_key(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """jobmon_resources={'project': 'proj_b', 'queue': 'long.q'} given to run() on a Pipeline with resources={'project': 'proj_a', 'memory': 8} binds project 'proj_b', queue 'long.q' and memory 8."""
    pipeline = _chain(tmp_path, resources={"project": "proj_a", "memory": 8})

    run(pipeline, jobmon=True, jobmon_resources={"project": "proj_b", "queue": "long.q"})

    defaults = _defaults(fake_jobmon)
    assert defaults["project"] == "proj_b"
    assert defaults["queue"] == "long.q"
    assert defaults["memory"] == 8


def test_a_steps_own_resources_reach_its_plan_entry_unchanged(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A step built with resources={'memory': 16, 'queue': 'long.q'} has exactly those resources on its plan entry, while the workflow's default_resources keep the layered queue, so the step's own keys win when Jobmon merges them."""
    pipeline = _chain(tmp_path, own={B: {"memory": 16, "queue": "long.q"}})

    run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    [plan] = fake_jobmon.plans
    entries = {entry.name: dict(entry.resources) for entry in plan}
    assert entries[B] == {"memory": 16, "queue": "long.q"}
    assert _defaults(fake_jobmon)["queue"] == DEFAULT_RESOURCES["queue"]


def test_jobmon_max_attempts_passes_through_to_the_adapter_unchanged(tmp_path: Path) -> None:
    """run(..., jobmon=True, jobmon_max_attempts=5) binds with max_attempts 5, and without it with the bridge's DEFAULT_MAX_ATTEMPTS."""
    pipeline = _chain(tmp_path)

    with FakeJobmon() as given:
        run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT), jobmon_max_attempts=5)
    with FakeJobmon() as default:
        run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    assert [options["max_attempts"] for options in given.options] == [5]
    attempts = [options["max_attempts"] for options in default.options]
    assert attempts == [DEFAULT_MAX_ATTEMPTS]


def test_the_callers_jobmon_resources_mapping_is_not_changed(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """The jobmon_resources dict passed to run() and the Pipeline's resources mapping are equal to their originals after the run."""
    given = {"project": "proj_b", "queue": "long.q"}
    resources = {"memory": 8}
    pipeline = _chain(tmp_path, resources=resources)

    run(pipeline, jobmon=True, jobmon_resources=given)

    assert given == {"project": "proj_b", "queue": "long.q"}
    assert resources == {"memory": 8}
    assert pipeline.resources == {"memory": 8}
    assert len(fake_jobmon.plans) == 1


# The project check


def test_a_step_without_a_project_raises_before_anything_is_written(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With no project in any layer, run(pipeline, jobmon=True) raises PipelineError with exit_code None, binds nothing, runs no step body, and leaves the root without pytask.lock, run.lock or a .pytask directory."""
    pipeline = _chain(tmp_path)

    error = _project_error(pipeline)

    assert type(error) is PipelineError, repr(error)
    assert error.exit_code is None
    assert fake_jobmon.plans == []
    assert recorded_pids(registry(tmp_path), _CHAIN) == set()
    assert _state_written(pipeline.root) == []


def test_the_project_error_names_each_step_without_a_project_and_the_three_ways(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """When two of three steps have no project and the third sets one in its own resources, the message names exactly those two steps and mentions -P/--project (or jobmon_resources), Pipeline(resources=...) and the step's resources=."""
    pipeline = _chain(tmp_path, own={C: {"project": "proj_c"}})

    message = str(_project_error(pipeline))

    assert A in message, message
    assert B in message, message
    assert C not in message, message
    assert any(way in message for way in ("-P", "--project", "jobmon_resources")), message
    assert "Pipeline(resources=" in message, message
    assert _STEP_RESOURCES.search(message), message
    assert fake_jobmon.plans == []


def test_a_project_in_only_the_steps_own_resources_is_enough(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A pipeline in which every step sets 'project' in its own resources, with no other layer setting one, runs through the bridge and every step succeeds."""
    own = {name: {"project": f"proj_{name[0]}"} for name in _CHAIN}
    pipeline = _chain(tmp_path, own=own)

    result = run(pipeline, jobmon=True)

    assert result == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert bound_names(fake_jobmon) == [list(_CHAIN)]
    assert "project" not in _defaults(fake_jobmon)


def test_an_empty_project_counts_as_no_project(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A Pipeline with resources={'project': ''} and no other project raises the project PipelineError."""
    pipeline = _chain(tmp_path, resources={"project": ""})

    error = _project_error(pipeline)

    assert type(error) is PipelineError, repr(error)
    assert error.exit_code is None
    message = str(error)
    assert all(name in message for name in _CHAIN), message
    assert "Pipeline(resources=" in message, message
    assert fake_jobmon.plans == []


def test_a_steps_own_empty_project_is_no_project_even_when_another_layer_sets_one(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A step whose own resources set 'project': '' while jobmon_resources sets a project raises the project PipelineError naming only that step, because its own key wins when Jobmon merges them; nothing is bound, no step body runs and nothing is written under the root."""
    pipeline = _chain(tmp_path, own={B: {"project": ""}})

    error = _project_error(pipeline, jobmon_resources=dict(_PROJECT))

    assert type(error) is PipelineError, repr(error)
    assert error.exit_code is None
    message = str(error)
    assert B in message, message
    assert A not in message, message
    assert C not in message, message
    assert fake_jobmon.plans == []
    assert recorded_pids(registry(tmp_path), _CHAIN) == set()
    assert _state_written(pipeline.root) == []


def test_the_project_check_covers_steps_that_would_not_run(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """run(only=...) on a pipeline whose unselected step has no project, while the selected steps have one, still raises the project PipelineError."""
    pipeline = _chain(tmp_path, own={A: {"project": "proj_a"}, B: {"project": "proj_b"}})

    error = _project_error(pipeline, only=B)

    assert type(error) is PipelineError, repr(error)
    message = str(error)
    assert C in message, message
    assert "Pipeline(resources=" in message, message
    assert fake_jobmon.plans == []
    assert recorded_pids(registry(tmp_path), _CHAIN) == set()


def test_a_missing_plugin_and_an_unknown_step_come_before_a_missing_project(
    tmp_path: Path, fake_jobmon: FakeJobmon, request: pytest.FixtureRequest
) -> None:
    """With no project anywhere, run(jobmon=True) without the bridge plugin raises the plugin error, and run(only='nope', jobmon=True) with the plugin raises the unknown-step error, not the project error."""
    pipeline = _chain(tmp_path)

    unknown = _project_error(pipeline, only="nope")
    request.getfixturevalue("without_bridge_plugin")
    missing = _project_error(pipeline)

    assert "No step named 'nope'" in str(unknown), str(unknown)
    assert "its pytask plugin is not registered" in str(missing), str(missing)
    for error in (unknown, missing):
        assert "Pipeline(resources=" not in str(error), str(error)
    assert fake_jobmon.plans == []


def test_a_dry_run_with_jobmon_needs_no_project_and_binds_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """run(pipeline, jobmon=True, dry_run=True) with no project anywhere returns every step as WOULD_BE_EXECUTED and binds no workflow."""
    result = run(_chain(tmp_path), jobmon=True, dry_run=True)

    assert result == dict.fromkeys(_CHAIN, TaskOutcome.WOULD_BE_EXECUTED)
    assert fake_jobmon.plans == []


# The run lock


def test_a_held_run_lock_raises_run_refused_naming_the_lock_file_and_holder(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """While another process holds <root>/run.lock, run(pipeline, jobmon=True) raises RunRefused, a PipelineError with exit_code FAILED chained from RunLocked, whose message names the lock file and the holder; nothing is bound and no step body runs."""
    pipeline = _chain(tmp_path)
    lock = make_dir(pipeline.root) / RUN_LOCK_NAME

    with held_lock(lock) as child:
        with pytest.raises(RunRefused) as info:
            run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    error = info.value
    assert isinstance(error, PipelineError)
    assert error.exit_code == ExitCode.FAILED
    assert isinstance(error.__cause__, RunLocked), repr(error.__cause__)
    message = str(error)
    assert message == str(error.__cause__)
    assert str(lock) in message, message
    assert f"pid {child.pid} on host" in message, message
    assert f"user {account_name()}" in message, message
    assert fake_jobmon.plans == []
    assert recorded_pids(registry(tmp_path), _CHAIN) == set()


def test_force_under_a_held_run_lock_raises_run_refused_and_keeps_pytask_lock(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """While another process holds the run lock, run(pipeline, force=[step], jobmon=True) on a root with a recorded run raises RunRefused and leaves pytask.lock byte-identical."""
    pipeline = _chain(tmp_path, stable_ids=True)
    run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))
    lockfile = pipeline.root / LOCKFILE
    before = lockfile.read_bytes()

    with held_lock(pipeline.root / RUN_LOCK_NAME):
        with pytest.raises(RunRefused):
            run(pipeline, force=[A], jobmon=True, jobmon_resources=dict(_PROJECT))

    assert lockfile.read_bytes() == before
    assert len(fake_jobmon.plans) == 1


def test_force_through_the_bridge_releases_the_run_lock_before_the_build_takes_it(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """run(pipeline, force=[step], jobmon=True) on a free root succeeds, reruns the forced step through the bridge, and leaves the run lock free afterwards."""
    pipeline = _chain(tmp_path, stable_ids=True)
    run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    result = run(pipeline, force=[A], jobmon=True, jobmon_resources=dict(_PROJECT))

    assert result[A] == TaskOutcome.SUCCESS
    plans = bound_names(fake_jobmon)
    assert len(plans) == 2, plans
    assert plans[1][0] == A, plans
    assert os.getpid() not in recorded_pids(registry(tmp_path), [A])
    assert lock_is_free(pipeline.root / RUN_LOCK_NAME)


def test_a_local_force_takes_no_run_lock(tmp_path: Path) -> None:
    """While another process holds the run lock, run(pipeline, force=[step]) without jobmon runs the forced step locally and succeeds."""
    pipeline = _chain(tmp_path, stable_ids=True)
    run(pipeline)

    with held_lock(pipeline.root / RUN_LOCK_NAME):
        result = run(pipeline, force=[A])

    assert result[A] == TaskOutcome.SUCCESS
    assert recorded_pids(registry(tmp_path), [A]) == {os.getpid()}


# An interrupted run


def test_sigterm_while_jobmon_runs_raises_run_interrupted_with_the_workflow_id(
    tmp_path: Path,
) -> None:
    """Under FakeJobmon(workflow_id=4242, interrupt_after=1), run(pipeline, jobmon=True) on a three-step chain raises RunInterrupted whose workflow_id is 4242, whose run_directory is the bridge's run directory under <root>/.pytask/jobmon, whose outcomes give the first step SUCCESS, and whose exit_code is the interrupted build's FAILED."""
    pipeline = _chain(tmp_path)

    error = _interrupted(pipeline, FakeJobmon(workflow_id=WORKFLOW_ID, interrupt_after=1))

    assert error.workflow_id == WORKFLOW_ID
    runs = (pipeline.root / RUNS_DIR).resolve()
    assert error.run_directory.resolve().parent == runs
    assert (error.run_directory / STATE_FILE).is_file()
    assert error.outcomes[A] == TaskOutcome.SUCCESS
    assert error.exit_code == ExitCode.FAILED
    assert str(WORKFLOW_ID) in str(error), str(error)
    assert str(error.run_directory) in str(error), str(error)


def test_a_keyboard_interrupt_from_jobmon_also_raises_run_interrupted(tmp_path: Path) -> None:
    """With interrupt_with='keyboard_interrupt', run(pipeline, jobmon=True) raises RunInterrupted and not KeyboardInterrupt or StepFailed."""
    jobmon = FakeJobmon(
        workflow_id=WORKFLOW_ID, interrupt_after=1, interrupt_with="keyboard_interrupt"
    )

    error = _interrupted(_chain(tmp_path), jobmon)

    assert not isinstance(error, StepFailed)
    assert error.workflow_id == WORKFLOW_ID


def test_an_interrupt_before_the_workflow_is_bound_gives_no_workflow_id(
    tmp_path: Path,
) -> None:
    """When the fake adapter's bind raises KeyboardInterrupt, run(pipeline, jobmon=True) raises RunInterrupted whose workflow_id is None and whose message says the run was interrupted before its workflow was bound."""
    jobmon = _InterruptedWhileBinding(workflow_id=WORKFLOW_ID)

    error = _interrupted(_chain(tmp_path), jobmon)

    assert error.workflow_id is None
    message = str(error)
    assert "before" in message, message
    assert "bound" in message, message
    assert recorded_pids(registry(tmp_path), _CHAIN) == set()


def test_running_again_after_an_interrupt_runs_only_the_steps_not_recorded(
    tmp_path: Path,
) -> None:
    """After a RunInterrupted on a chain of stable steps a -> b -> c interrupted after a, a second run(pipeline, jobmon=True) binds only b and c and returns a SKIP_UNCHANGED for a."""
    pipeline = _chain(tmp_path, stable_ids=True)
    _interrupted(pipeline, FakeJobmon(workflow_id=WORKFLOW_ID, interrupt_after=1))

    with FakeJobmon() as jobmon:
        result = run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    assert bound_names(jobmon) == [[B, C]]
    assert result[A] == TaskOutcome.SKIP_UNCHANGED
    assert result[B] == result[C] == TaskOutcome.SUCCESS


# A step that fails on its node


def test_a_node_failure_raises_step_failed_with_the_node_exception_type_and_message(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A python step whose target raises StepFailure on its node makes run(pipeline, jobmon=True) raise StepFailed whose message is "Step '<name>' failed: tests.bridge.step_targets.StepFailure: the step failed on purpose", and whose failure for the step has the node traceback."""
    artifacts = registry(tmp_path)
    tasks = chain_and_independent(artifacts, failing=(B,))
    pipeline = Pipeline(tasks, build_root(tmp_path), artifacts)

    with pytest.raises(StepFailed) as info:
        run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    failure = f"{step_targets.__name__}.StepFailure: {step_targets.FAILURE_MESSAGE}"
    assert str(info.value) == f"Step {B!r} failed: {failure}"
    error = info.value.failures[B]
    assert isinstance(error, RemoteTaskFailed), repr(error)
    assert error.node_traceback is not None
    assert "raise_step_failure_reading" in error.node_traceback, error.node_traceback
    assert step_targets.FAILURE_MESSAGE in error.node_traceback, error.node_traceback
