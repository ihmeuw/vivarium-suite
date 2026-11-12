"""Tests that a ``command_step`` crosses the Jobmon bridge as an ordinary command task.

These tests sit under ``tests/bridge`` so that the bridge fixtures apply, including the
autouse ``_outside_slurm``. Their pipelines use command steps only, so a node runner
imports no test module and needs no ``node_pythonpath``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pytask import TaskOutcome

from tests.bridge.helpers import bound_names
from vivarium.dagger.backend.env import resolve_env_bin_path, resolve_env_prefix
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge.payload import payload_for
from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, StepFailed, command_step, run
from vivarium.dagger.harness import FakeJobmon

_TOOL = "vivarium_dagger_command_bridge_probe"
"""A tool that exists only in the bin directory of the fake envs these tests make."""


def _make_env(prefix: Path) -> Path:
    """Create a fake env at ``prefix`` with ``bin/python`` and a ``_TOOL`` script.

    The tool appends its first argument as a line to the file named by its second.
    """
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").touch()
    tool = bin_dir / _TOOL
    tool.write_text('#!/bin/sh\necho "$1" >> "$2"\n')
    tool.chmod(0o755)
    return prefix


def test_payload_for_a_command_step_carries_its_command_and_the_env_bin_path(
    tmp_path: Path, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """payload_for on a command_step task under a fake env gives kind "command", command equal to the task's run, and env_path equal to the env's resolved bin path."""
    env = str(_make_env(tmp_path / "env"))
    task = command_step(
        _TOOL + " ran {out}",
        produces={"out": "out.txt"},
        registry=ArtifactRegistry(tmp_path / "out", "run1"),
        env=env,
        name="tooled",
    )

    payload = dict(payload_for(task, pytask_kwargs(task)))

    assert payload["kind"] == "command"
    assert isinstance(task.run, str)
    assert payload["command"] == task.run
    assert payload["env_path"] == resolve_env_bin_path(resolve_env_prefix(env))


def test_payload_for_a_command_step_without_an_env_has_no_env_path(
    tmp_path: Path, pytask_kwargs: Callable[[Task], dict[str, Any]]
) -> None:
    """payload_for on a command_step task with env=None gives kind "command", command equal to the task's run, and env_path None."""
    task = command_step(
        "printf x > {out}",
        produces={"out": "out.txt"},
        registry=ArtifactRegistry(tmp_path / "out", "run1"),
        name="plain",
    )

    payload = dict(payload_for(task, pytask_kwargs(task)))

    assert payload["kind"] == "command"
    assert isinstance(task.run, str)
    assert payload["command"] == task.run
    assert payload["env_path"] is None


def test_two_chained_command_steps_run_through_fake_jobmon_and_the_env_tool_runs(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """frontend.run(pipeline, jobmon=True) under FakeJobmon on two chained command steps, the second under a fake env given by its prefix path, gives SUCCESS for both, writes both products, and the second product records that the env's tool ran."""
    env = _make_env(tmp_path / "env")
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    first = command_step(
        "printf 'first\\n' > {first}",
        produces={"first": "chain/first.txt"},
        registry=registry,
        name="first",
    )
    second = command_step(
        "cat {upstream} > {second} && " + _TOOL + " tool-ran {second}",
        inputs={"upstream": "chain/first.txt"},
        produces={"second": "chain/second.txt"},
        registry=registry,
        env=str(env),
        name="second",
    )

    result = run(
        Pipeline([first, second], tmp_path / "state", registry),
        jobmon=True,
        jobmon_resources={"project": "proj_test"},
    )

    assert result == {"first": TaskOutcome.SUCCESS, "second": TaskOutcome.SUCCESS}
    assert bound_names(fake_jobmon) == [["first", "second"]]
    assert registry.path_for("chain/first.txt").read_text() == "first\n"
    assert registry.path_for("chain/second.txt").read_text() == "first\ntool-ran\n"


@pytest.mark.parametrize(
    "command, exit_text",
    [
        ("exit 3", "the command exited with status 3"),
        ("kill -KILL $$", "the command was stopped by signal SIGKILL"),
    ],
    ids=["status", "signal"],
)
def test_a_command_step_that_fails_on_its_node_is_described_as_a_local_failure(
    tmp_path: Path, fake_jobmon: FakeJobmon, command: str, exit_text: str
) -> None:
    """A command step that exits 3, or is killed by SIGKILL, on its node through FakeJobmon makes run() raise StepFailed whose line gives the exit as a local failure does and leaves the generated command out."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    step = command_step(
        command + " && printf 'never' > {out}",
        produces={"out": "out.txt"},
        registry=registry,
        name="failing",
    )

    with pytest.raises(StepFailed) as info:
        run(
            Pipeline([step], tmp_path / "state", registry),
            jobmon=True,
            jobmon_resources={"project": "proj_test"},
        )

    line = str(info.value)
    assert line == (
        f"Step 'failing' failed: {exit_text}; its output is in pytask's report for the step."
    ), line
    assert "printf" not in line
    assert bound_names(fake_jobmon) == [["failing"]]
