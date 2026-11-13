"""Tests for a Workflow's pipeline run through the Jobmon bridge (MIC-7805).

Each test runs its steps through ``FakeJobmon`` and the real node runner, so every step
runs in a child process; the step functions come from ``tests.bridge.step_targets``.
"""

import os
import sys
import traceback
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result
from pytask import TaskOutcome

from tests.backend.lock_helpers import held_lock
from tests.bridge import step_targets
from tests.bridge.helpers import (
    CHAIN,
    LOCKFILE,
    A,
    B,
    C,
    bound_names,
    make_dir,
    recorded_pids,
)
from vivarium.dagger.backend.lock import RUN_LOCK_NAME
from vivarium.dagger.cli import dagger
from vivarium.dagger.constants import DEFAULT_RESOURCES
from vivarium.dagger.frontend import PipelineError, RunRefused, Workflow, run
from vivarium.dagger.harness import FakeJobmon

_PROJECT: Mapping[str, Any] = {"project": "proj_test"}
"""Workflow resources that give every step a project."""

_DEFINITION = """\
from pathlib import Path

from tests.bridge import step_targets
from vivarium.dagger.frontend import Pipeline, Workflow

HERE = Path(__file__).resolve().parent


def pipeline() -> Pipeline:
    wf = Workflow(HERE / "out", run_name="run1")
    wf.python_step(step_targets.record_pid, inputs={}, produces="a.parquet")
    wf.python_step(
        step_targets.record_pid, inputs={"upstream": "a.parquet"}, produces="b.parquet"
    )
    return wf.pipeline()
"""
"""A definition that adds the chain a.parquet -> b.parquet to a Workflow and returns its
pipeline, with the products under ``out/run1`` beside the file."""


@pytest.fixture(autouse=True)
def _forget_loaded_definitions(tmp_path: Path) -> Iterator[None]:
    """Restore ``sys.path`` and drop the modules a test loaded from its definition files."""
    path = list(sys.path)
    modules = set(sys.modules)
    yield
    sys.path[:] = path
    for name in set(sys.modules) - modules:
        file = getattr(sys.modules.get(name), "__file__", None)
        if isinstance(file, str) and Path(file).resolve().is_relative_to(tmp_path.resolve()):
            del sys.modules[name]


def _chain(wf: Workflow, own: Mapping[str, dict[str, Any]] | None = None) -> Workflow:
    """Add the chain a -> b -> c, each step recording its process id, to ``wf``; return it.

    ``own`` gives a step's own resources by step name.
    """
    given: Mapping[str, dict[str, Any]] = {} if own is None else own
    sources: dict[str, dict[str, str]] = {A: {}, B: {"upstream": A}, C: {"upstream": B}}
    for name, inputs in sources.items():
        wf.python_step(
            step_targets.record_pid, inputs=inputs, produces=name, resources=given.get(name)
        )
    return wf


def _describe(result: Result) -> str:
    """Return ``result``'s output and any exception it caught, for an assert message."""
    text = result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += "".join(traceback.format_exception(result.exception))
    return text


@pytest.mark.usefixtures("node_pythonpath")
def test_a_workflow_pipeline_runs_through_fake_jobmon_and_the_real_runner(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: a three-step chain added to a Workflow with a project in its resources runs with run(pipeline, jobmon=True); every step succeeds in a process other than the test's, the products are under output_root/run_name, one plan is bound with the three steps, and the run's state and run lock are under output_root/.dagger/run_name."""
    output_root = (tmp_path / "out").resolve()
    wf = _chain(Workflow(tmp_path / "out", "run1", resources=_PROJECT))

    result = run(wf.pipeline(), jobmon=True)

    assert result == dict.fromkeys(CHAIN, TaskOutcome.SUCCESS)
    assert bound_names(fake_jobmon) == [list(CHAIN)]
    pids = recorded_pids(wf.registry, CHAIN)
    assert pids, "no step recorded its process id"
    assert os.getpid() not in pids
    for name in CHAIN:
        assert (output_root / "run1" / name).is_file(), name
    state = output_root / ".dagger" / "run1"
    assert wf.state == state
    assert (state / LOCKFILE).is_file()
    assert (state / RUN_LOCK_NAME).is_file()
    assert not (output_root / "run1" / RUN_LOCK_NAME).exists()


@pytest.mark.usefixtures("node_pythonpath")
def test_the_jobmon_workflow_is_named_after_the_run_name(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With the default state, the bind of a run through Jobmon gets the workflow's run name as its workflow_name."""
    wf = _chain(Workflow(tmp_path / "out", "study_run", resources=_PROJECT))

    run(wf.pipeline(), jobmon=True)

    [options] = fake_jobmon.options
    assert options["workflow_name"] == "study_run"


@pytest.mark.usefixtures("node_pythonpath")
def test_the_workflow_resources_reach_the_bound_plan(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Resources given to the Workflow reach Pipeline.resources and the bound plan's default resources, and a step's own resources= overrides them key by key."""
    resources = {"project": "proj_wf", "memory": 8}
    wf = Workflow(tmp_path / "out", "run1", resources=resources)
    _chain(wf, own={B: {"memory": 16}})
    pipeline = wf.pipeline()
    assert dict(pipeline.resources) == {"project": "proj_wf", "memory": 8}

    run(pipeline, jobmon=True)

    [options] = fake_jobmon.options
    defaults = dict(options["default_resources"])
    assert defaults == {**DEFAULT_RESOURCES, "project": "proj_wf", "memory": 8}
    [plan] = fake_jobmon.plans
    own = {entry.name: dict(entry.resources) for entry in plan}
    assert own == {A: {}, B: {"memory": 16}, C: {}}


@pytest.mark.usefixtures("node_pythonpath")
def test_two_workflows_with_different_run_names_do_not_share_a_run_lock(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """While another process holds the run lock of workflow 'a' under one output root, a run through Jobmon of workflow 'b' under the same output root succeeds, and a run of 'a' is refused with RunRefused naming a's lock file."""
    first = _chain(Workflow(tmp_path, "a", resources=_PROJECT))
    second = _chain(Workflow(tmp_path, "b", resources=_PROJECT))
    assert first.state != second.state
    lock = make_dir(first.state) / RUN_LOCK_NAME

    with held_lock(lock):
        result = run(second.pipeline(), jobmon=True)
        with pytest.raises(RunRefused) as info:
            run(first.pipeline(), jobmon=True)

    assert result == dict.fromkeys(CHAIN, TaskOutcome.SUCCESS)
    assert str(lock) in str(info.value), str(info.value)
    assert bound_names(fake_jobmon) == [list(CHAIN)]
    assert recorded_pids(first.registry, CHAIN) == set()


@pytest.mark.usefixtures("node_pythonpath")
def test_the_missing_project_message_names_the_workflows_resources(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A run through Jobmon of a Workflow's pipeline with no project in any layer raises PipelineError whose message names Workflow(resources={'project': ...}) as well as Pipeline(resources=, and binds nothing."""
    wf = _chain(Workflow(tmp_path / "out", "run1"))

    with pytest.raises(PipelineError) as info:
        run(wf.pipeline(), jobmon=True)

    message = str(info.value)
    assert "Workflow(resources={'project': ...})" in message, message
    assert "Pipeline(resources=" in message, message
    assert fake_jobmon.plans == []


@pytest.mark.usefixtures("node_pythonpath")
def test_dagger_run_of_a_workflow_definition_through_fake_jobmon_exits_zero(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger run -c def.py -P proj' on a definition that returns wf.pipeline() exits 0 under FakeJobmon, binds one plan with the definition's steps, and writes every product under output_root/run_name."""
    path = tmp_path / f"def_{uuid.uuid4().hex[:12]}.py"
    path.write_text(_DEFINITION)

    result = CliRunner().invoke(dagger, ["run", "-c", str(path), "-P", "proj_test"])

    assert result.exit_code == 0, _describe(result)
    assert bound_names(fake_jobmon) == [[A, B]]
    run_directory = tmp_path.resolve() / "out" / "run1"
    for name in (A, B):
        assert (run_directory / name).is_file(), name
