"""Tests for a Pipeline run through the Jobmon bridge (MIC-7805, MIC-7810).

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

import pandas as pd
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
from vivarium.dagger.frontend import Pipeline, PipelineError, RunRefused, StepFailed, run
from vivarium.dagger.frontend.builder import freeze
from vivarium.dagger.harness import FakeJobmon

_PROJECT: Mapping[str, Any] = {"project": "proj_test"}
"""Pipeline resources that give every step a project."""

_DEFINITION = """\
from pathlib import Path

from tests.bridge import step_targets
from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent


def pipeline() -> Pipeline:
    p = Pipeline(HERE / "out", run_name="run1")
    p.add_python_step(step_targets.record_pid, inputs={}, outputs="a.parquet")
    p.add_python_step(
        step_targets.record_pid, inputs={"upstream": "a.parquet"}, outputs="b.parquet"
    )
    return p
"""
"""A definition that adds the chain a.parquet -> b.parquet to a Pipeline and returns it,
with the outputs under ``out/run1`` beside the file."""


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


def _chain(p: Pipeline, own: Mapping[str, dict[str, Any]] | None = None) -> Pipeline:
    """Add the chain a -> b -> c, each step recording its process id, to ``p``; return it.

    ``own`` gives a step's own resources by step name.
    """
    given: Mapping[str, dict[str, Any]] = {} if own is None else own
    sources: dict[str, dict[str, str]] = {A: {}, B: {"upstream": A}, C: {"upstream": B}}
    for name, inputs in sources.items():
        p.add_python_step(
            step_targets.record_pid, inputs=inputs, outputs=name, resources=given.get(name)
        )
    return p


def _describe(result: Result) -> str:
    """Return ``result``'s output and any exception it caught, for an assert message."""
    text = result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += "".join(traceback.format_exception(result.exception))
    return text


@pytest.mark.usefixtures("node_pythonpath")
def test_a_pipeline_runs_through_fake_jobmon_and_the_real_runner(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: a three-step chain added to a Pipeline with a project in its resources runs with run(p, jobmon=True); every step succeeds in a process other than the test's, the outputs are under output_root/run_name, one plan is bound with the three steps, and the run's state and run lock are under output_root/.dagger/run_name."""
    output_root = (tmp_path / "out").resolve()
    p = _chain(Pipeline(tmp_path / "out", "run1", resources=_PROJECT))

    result = run(p, jobmon=True)

    assert result == dict.fromkeys(CHAIN, TaskOutcome.SUCCESS)
    assert bound_names(fake_jobmon) == [list(CHAIN)]
    registry = freeze(p).registry
    assert registry is not None
    pids = recorded_pids(registry, CHAIN)
    assert pids, "no step recorded its process id"
    assert os.getpid() not in pids
    for name in CHAIN:
        assert (output_root / "run1" / name).is_file(), name
    state = output_root / ".dagger" / "run1"
    assert p.state == state
    assert (state / LOCKFILE).is_file()
    assert (state / RUN_LOCK_NAME).is_file()
    assert not (output_root / "run1" / RUN_LOCK_NAME).exists()


@pytest.mark.usefixtures("node_pythonpath")
def test_the_jobmon_workflow_is_named_after_the_run_name(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """With the default state, the bind of a run through Jobmon gets the pipeline's run name as its workflow_name."""
    p = _chain(Pipeline(tmp_path / "out", "study_run", resources=_PROJECT))

    run(p, jobmon=True)

    [options] = fake_jobmon.options
    assert options["workflow_name"] == "study_run"


@pytest.mark.usefixtures("node_pythonpath")
def test_the_pipeline_resources_reach_the_bound_plan(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Resources given to the Pipeline reach its snapshot's resources and the bound plan's default resources, and a step's own resources= overrides them key by key."""
    resources = {"project": "proj_wf", "memory": 8}
    p = Pipeline(tmp_path / "out", "run1", resources=resources)
    _chain(p, own={B: {"memory": 16}})
    assert dict(freeze(p).resources) == {"project": "proj_wf", "memory": 8}

    run(p, jobmon=True)

    [options] = fake_jobmon.options
    defaults = dict(options["default_resources"])
    assert defaults == {**DEFAULT_RESOURCES, "project": "proj_wf", "memory": 8}
    [plan] = fake_jobmon.plans
    own = {entry.name: dict(entry.resources) for entry in plan}
    assert own == {A: {}, B: {"memory": 16}, C: {}}


@pytest.mark.usefixtures("node_pythonpath")
def test_two_pipelines_with_different_run_names_do_not_share_a_run_lock(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """While another process holds the run lock of pipeline 'a' under one output root, a run through Jobmon of pipeline 'b' under the same output root succeeds, and a run of 'a' is refused with RunRefused naming a's lock file."""
    first = _chain(Pipeline(tmp_path, "a", resources=_PROJECT))
    second = _chain(Pipeline(tmp_path, "b", resources=_PROJECT))
    assert first.state != second.state
    lock = make_dir(first.state) / RUN_LOCK_NAME

    with held_lock(lock):
        result = run(second, jobmon=True)
        with pytest.raises(RunRefused) as info:
            run(first, jobmon=True)

    assert result == dict.fromkeys(CHAIN, TaskOutcome.SUCCESS)
    assert str(lock) in str(info.value), str(info.value)
    assert bound_names(fake_jobmon) == [list(CHAIN)]
    first_registry = freeze(first).registry
    assert first_registry is not None
    assert recorded_pids(first_registry, CHAIN) == set()


@pytest.mark.usefixtures("node_pythonpath")
def test_the_missing_project_message_names_the_pipelines_resources_and_not_workflow(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A run through Jobmon of a Pipeline with no project in any layer raises PipelineError whose message names Pipeline(resources={'project': ...}) and does not name Workflow, and binds nothing."""
    p = _chain(Pipeline(tmp_path / "out", "run1"))

    with pytest.raises(PipelineError) as info:
        run(p, jobmon=True)

    message = str(info.value)
    assert "Pipeline(resources={'project': ...})" in message, message
    assert "Workflow" not in message, message
    assert fake_jobmon.plans == []


@pytest.mark.usefixtures("node_pythonpath")
def test_dagger_run_of_a_pipeline_definition_through_fake_jobmon_exits_zero(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger run -c def.py -P proj' on a definition that returns its Pipeline p exits 0 under FakeJobmon, binds one plan with the definition's steps, and writes every output under output_root/run_name."""
    path = tmp_path / f"def_{uuid.uuid4().hex[:12]}.py"
    path.write_text(_DEFINITION)

    result = CliRunner().invoke(dagger, ["run", "-c", str(path), "-P", "proj_test"])

    assert result.exit_code == 0, _describe(result)
    assert bound_names(fake_jobmon) == [[A, B]]
    run_directory = tmp_path.resolve() / "out" / "run1"
    for name in (A, B):
        assert (run_directory / name).is_file(), name


@pytest.mark.usefixtures("node_pythonpath")
def test_a_step_added_after_a_run_through_jobmon_is_in_the_next_run_and_not_the_finished_one(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: after run(p, jobmon=True) of a two-step chain returns its two outcomes and binds a plan of those two steps, a third step added to p does not change that result; a second run(p, jobmon=True) binds a plan that has the third step, returns SUCCESS for all three steps, and writes the third step's output."""
    p = Pipeline(tmp_path / "out", "run1", resources=_PROJECT)
    p.add_python_step(step_targets.record_pid, inputs={}, outputs=A)
    p.add_python_step(step_targets.record_pid, inputs={"upstream": A}, outputs=B)
    third = p.output_root / "run1" / C

    finished = run(p, jobmon=True)
    p.add_python_step(step_targets.record_pid, inputs={"upstream": B}, outputs=C)

    assert finished == {A: TaskOutcome.SUCCESS, B: TaskOutcome.SUCCESS}
    assert bound_names(fake_jobmon) == [[A, B]]
    assert not third.exists()

    again = run(p, jobmon=True)

    assert again == dict.fromkeys(CHAIN, TaskOutcome.SUCCESS)
    assert len(fake_jobmon.plans) == 2
    assert C in bound_names(fake_jobmon)[1]
    assert third.is_file()


def _split_and_consumers(p: Pipeline, target: Any) -> Pipeline:
    """Add the step 'split' with ``target`` and a step that copies each of its outputs."""
    p.add_python_step(
        target,
        inputs={},
        outputs={"population": "population.parquet", "deaths": "deaths.csv"},
        name="split",
    )
    p.add_python_step(
        step_targets.copy_table,
        inputs={"source": "population.parquet"},
        outputs="population_copy.parquet",
    )
    p.add_python_step(
        step_targets.copy_table, inputs={"source": "deaths.csv"}, outputs="deaths_copy.csv"
    )
    return p


_SPLIT_STEPS = ["split", "population_copy.parquet", "deaths_copy.csv"]
"""The steps that :func:`_split_and_consumers` adds, in the order it adds them."""


@pytest.mark.usefixtures("node_pythonpath")
def test_a_python_step_with_several_outputs_runs_through_jobmon(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: a python step whose outputs maps 'population' and 'deaths' to a Parquet and a CSV key, and a consumer of each, run with run(p, jobmon=True); every step succeeds, one plan binds the three steps, and the outputs are under output_root/run_name in their formats."""
    p = Pipeline(tmp_path / "out", "run1", resources=_PROJECT)
    _split_and_consumers(p, step_targets.split_frames)

    result = run(p, jobmon=True)

    assert result == dict.fromkeys(_SPLIT_STEPS, TaskOutcome.SUCCESS)
    [bound] = bound_names(fake_jobmon)
    assert sorted(bound) == sorted(_SPLIT_STEPS)
    assert bound[0] == "split"
    run_directory = tmp_path.resolve() / "out" / "run1"
    expected = [1, 2, 3]
    assert pd.read_parquet(run_directory / "population.parquet")["n"].tolist() == expected
    assert pd.read_csv(run_directory / "deaths.csv")["n"].tolist() == expected
    assert (
        pd.read_parquet(run_directory / "population_copy.parquet")["n"].tolist() == expected
    )
    assert pd.read_csv(run_directory / "deaths_copy.csv")["n"].tolist() == expected


@pytest.mark.usefixtures("node_pythonpath")
def test_a_dict_missing_an_output_fails_the_step_through_jobmon(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Through Jobmon, a python step with several outputs whose dict leaves out 'deaths' makes run raise StepFailed whose line names the step and the missing output; its consumers are skipped and write nothing."""
    p = Pipeline(tmp_path / "out", "run1", resources=_PROJECT)
    _split_and_consumers(p, step_targets.split_frames_without_deaths)

    with pytest.raises(StepFailed) as info:
        run(p, jobmon=True)

    line = str(info.value)
    assert line.startswith("Step 'split' failed:"), line
    assert (
        "returned a dict whose keys do not match its output names: the output 'deaths' "
        "is missing." in line
    ), line
    assert info.value.outcomes == {
        "split": TaskOutcome.FAIL,
        "population_copy.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
        "deaths_copy.csv": TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    run_directory = tmp_path.resolve() / "out" / "run1"
    assert not (run_directory / "population_copy.parquet").exists()
    assert not (run_directory / "deaths_copy.csv").exists()
