"""Tests of a simulation step run through the Jobmon bridge under ``FakeJobmon``, with a
stub ``psimulate`` (see ``tests.frontend.simulations``).

The steps' targets must be importable on the node, so a test that runs a python consumer
uses the ``node_pythonpath`` fixture.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import TaskOutcome

from tests.frontend.simulations import (
    EXIT_VARIABLE,
    KNOB_VARIABLES,
    LOG_VARIABLE,
    METRIC,
    NO_PREVIOUS,
    UNFINISHED_VARIABLE,
    assert_no_command,
    deaths_by_draw,
    make_stub_env,
    read_argv_log,
    scratch_for,
    summarize_run,
    summary_values,
    write_branches,
    write_spec,
)
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun
from vivarium.dagger.bridge.payload import payload_for
from vivarium.dagger.frontend import Pipeline, StepFailed, python_step, run, simulation_step
from vivarium.dagger.frontend._collect_simulation import _MESSAGE_FILE, _SENTINEL
from vivarium.dagger.harness import FakeJobmon, with_stable_code_id

_KEY = "sims/toy"
"""The simulation step's produces key, which is also its name."""

_CONSUMER = "summary"
"""The name of the python step that reads the run directory."""

_SUMMARY_KEY = "summary.parquet"
"""The consumer's product key."""

_OPTIONS: dict[str, str | int | float | bool] = {
    "project": "proj_simscience",
    "no_slack": True,
}

_SUCCEEDED = {_KEY: TaskOutcome.SUCCESS, _CONSUMER: TaskOutcome.SUCCESS}

_SKIPPED = {_KEY: TaskOutcome.SKIP_UNCHANGED, _CONSUMER: TaskOutcome.SKIP_UNCHANGED}


@dataclass(frozen=True)
class _Simulation:
    """A toy spec, a branches file of 4 simulations and a stub env, all under ``base``."""

    base: Path
    spec: Path
    branches: Path
    env: Path
    log: Path

    @property
    def run_dir(self) -> Path:
        """Return the run directory that ``_KEY`` resolves to."""
        return self.registry().path_for(_KEY)

    @property
    def scratch(self) -> Path:
        """Return the scratch directory psimulate writes to."""
        return scratch_for(self.run_dir)

    def registry(self) -> ArtifactRegistry:
        """Return a new registry whose products go under ``base / "out"``."""
        return ArtifactRegistry(self.base / "out", "run1")

    def calls(self) -> list[list[str]]:
        """Return the arguments of each call of the stub psimulate, in call order."""
        return read_argv_log(self.log)


@pytest.fixture
def simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Simulation:
    """Write the toy inputs and a stub env under ``tmp_path``, and log the stub's calls."""
    base = tmp_path.resolve()
    log = base / "psimulate_calls.jsonl"
    monkeypatch.setenv(LOG_VARIABLE, str(log))
    for variable in KNOB_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return _Simulation(
        base=base,
        spec=write_spec(base / "model" / "toy_model.yaml"),
        branches=write_branches(base / "model" / "branches.yaml"),
        env=make_stub_env(base / "env"),
        log=log,
    )


def _pipeline(
    simulation: _Simulation, *, consumer: bool = False, stable_ids: bool = False
) -> Pipeline:
    """Return the simulation step and, with ``consumer``, a python step that reads its run."""
    registry = simulation.registry()
    tasks = [
        simulation_step(
            spec=simulation.spec,
            branches=simulation.branches,
            produces=_KEY,
            registry=registry,
            env=str(simulation.env),
            options=_OPTIONS,
        )
    ]
    if consumer:
        tasks.append(
            python_step(
                summarize_run,
                inputs={"simulation": _KEY},
                produces=_SUMMARY_KEY,
                registry=registry,
                name=_CONSUMER,
            )
        )
    if stable_ids:
        tasks = [with_stable_code_id(task) for task in tasks]
    return Pipeline(tasks, simulation.base / "state", registry)


def _failure_line(pipeline: Pipeline) -> str:
    """Run ``pipeline`` with jobmon=True; assert StepFailed and return its message."""
    with pytest.raises(StepFailed) as excinfo:
        run(pipeline, jobmon=True)
    return str(excinfo.value)


@dataclass
class _EnvGoneOnTheNode(FakeJobmon):
    """Stand in for Jobmon on a node that cannot see a step's env: remove it, then run."""

    env: Path | None = None
    """The env prefix to remove before the run, as the node does not have it."""

    def run(self, jobmon_run: JobmonRun, *, seconds_until_timeout: int | None = None) -> None:
        """Remove the env, then run the entries as FakeJobmon does."""
        assert self.env is not None
        shutil.rmtree(self.env)
        super().run(jobmon_run, seconds_until_timeout=seconds_until_timeout)


def test_payload_for_a_simulation_step_is_a_command_with_the_env_bin_path(
    simulation: _Simulation, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """payload_for on a simulation_step task gives kind 'command', a command equal to the task's run, and an env_path equal to the stub env's resolved bin path."""
    [task] = _pipeline(simulation).tasks

    recipe = dict(payload_for(task, pytask_kwargs(task)))

    assert recipe["kind"] == "command"
    assert recipe["command"] == task.run
    assert recipe["env_path"] == f"{simulation.env.resolve()}/bin"


@pytest.mark.usefixtures("node_pythonpath")
def test_a_simulation_and_its_consumer_run_through_fake_jobmon(
    simulation: _Simulation, fake_jobmon: FakeJobmon
) -> None:
    """run(pipeline, jobmon=True) under FakeJobmon runs the simulation step and a downstream python_step; the target receives a Path equal to the run directory, as it writes str(path) and the parquet file count to its output, and the run directory holds the results and the sentinel."""
    outcomes = run(_pipeline(simulation, consumer=True), jobmon=True)

    assert outcomes == _SUCCEEDED
    bound = {entry.name for plan in fake_jobmon.plans for entry in plan}
    assert bound == {_KEY, _CONSUMER}
    summary = pd.read_parquet(simulation.registry().path_for(_SUMMARY_KEY))
    assert set(summary["received"]) == {str(simulation.run_dir)}
    assert summary["is_path"].all()
    assert set(summary["files"]) == {4}
    assert summary_values(summary) == deaths_by_draw()
    assert len(list((simulation.run_dir / "results" / METRIC).glob("*.parquet"))) == 4
    assert (simulation.run_dir / _SENTINEL).is_file()
    assert not simulation.scratch.exists()
    assert len(simulation.calls()) == 1


@pytest.mark.usefixtures("node_pythonpath")
def test_the_controller_records_the_run_from_the_sentinel_so_a_second_run_skips(
    simulation: _Simulation, fake_jobmon: FakeJobmon
) -> None:
    """With stable code identities, a second run(jobmon=True) gives both steps SKIP_UNCHANGED and the stub's argv log still has one entry."""
    pipeline = _pipeline(simulation, consumer=True, stable_ids=True)
    assert run(pipeline, jobmon=True) == _SUCCEEDED

    outcomes = run(pipeline, jobmon=True)

    assert outcomes == _SKIPPED
    assert len(simulation.calls()) == 1


@pytest.mark.usefixtures("node_pythonpath")
def test_removing_the_sentinel_makes_a_third_bridged_run_run_both_steps(
    simulation: _Simulation, fake_jobmon: FakeJobmon
) -> None:
    """After two bridged runs and the sentinel's removal, a third run(jobmon=True) runs both the simulation step and its consumer."""
    pipeline = _pipeline(simulation, consumer=True, stable_ids=True)
    assert run(pipeline, jobmon=True) == _SUCCEEDED
    assert run(pipeline, jobmon=True) == _SKIPPED
    (simulation.run_dir / _SENTINEL).unlink()

    outcomes = run(pipeline, jobmon=True)

    assert outcomes == _SUCCEEDED
    assert len(simulation.calls()) == 2
    assert (simulation.run_dir / _SENTINEL).is_file()


@pytest.mark.usefixtures("fake_jobmon")
def test_one_simulation_missing_under_fake_jobmon_fails_with_the_counts_message(
    simulation: _Simulation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridged run with one simulation unfinished raises StepFailed whose line carries the check's message with '3 of 4'."""
    pipeline = _pipeline(simulation)
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")

    line = _failure_line(pipeline)

    [partial] = sorted(path for path in simulation.scratch.glob("*/*") if path.is_dir())
    assert line.startswith(
        f"Step '{_KEY}' failed: psimulate finished 3 of 4 simulations; the partial run is "
        f"at {partial}."
    ), line
    assert line.endswith(NO_PREVIOUS), line
    assert_no_command(line, pipeline, _KEY)
    assert not simulation.run_dir.exists()


@pytest.mark.usefixtures("fake_jobmon")
def test_a_psimulate_that_exits_non_zero_under_fake_jobmon_gets_the_bridge_line(
    simulation: _Simulation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridged run whose stub exits non-zero before writing raises StepFailed whose line says the step's command failed before writing a message, without blaming psimulate, and names the scratch directory, with no exit status and without the generated command."""
    pipeline = _pipeline(simulation)
    monkeypatch.setenv(EXIT_VARIABLE, "3")

    line = _failure_line(pipeline)

    assert line.startswith(
        f"Step '{_KEY}' failed: the simulation step's command failed before writing a "
        "message;"
    ), line
    assert "psimulate did not finish" not in line, line
    assert f"psimulate's logs are under {simulation.scratch}." in line
    assert line.endswith(NO_PREVIOUS), line
    assert "status" not in line
    assert_no_command(line, pipeline, _KEY)
    assert len(simulation.calls()) == 1


def test_a_stale_message_file_is_not_read_when_the_env_is_gone_on_the_node(
    simulation: _Simulation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a bridged run that fails through the check and leaves the message file, a second bridged run under a FakeJobmon subclass that removes the stub env before running raises StepFailed whose line is the FileNotFoundError description and holds none of the earlier counts message."""
    pipeline = _pipeline(simulation)
    message_file = simulation.scratch / _MESSAGE_FILE
    monkeypatch.setenv(UNFINISHED_VARIABLE, "1")
    with FakeJobmon():
        first = _failure_line(pipeline)
    assert "psimulate finished 3 of 4 simulations" in first
    assert message_file.is_file()

    with _EnvGoneOnTheNode(env=simulation.env):
        line = _failure_line(pipeline)

    assert line.startswith(f"Step '{_KEY}' failed: FileNotFoundError: "), line
    assert f"{simulation.env}/bin" in line
    assert "3 of 4" not in line
    assert "psimulate finished" not in line
    assert message_file.is_file()
    assert len(simulation.calls()) == 1
