"""Check the arguments a simulation step gives psimulate against the installed
``psimulate run`` command.

The stub ``psimulate`` records the arguments a step passes, and these tests parse them
with the real command's ``make_context``, which runs its option callbacks and checks
without running the command. ``vivarium.psimulate.cli`` imports jobmon, so this module is
skipped where jobmon is not installed, as in GitHub CI.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("jobmon")

import vivarium.psimulate.cli

from tests.frontend.simulations import (
    KNOB_VARIABLES,
    LOG_VARIABLE,
    make_stub_env,
    read_argv_log,
    write_branches,
    write_spec,
)
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.frontend import Pipeline, run, simulation_step

_KEY = "sims/toy"
"""The simulation step's produces key."""


def _recorded_argv(
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, str | int | float | bool],
    artifact: Path | None = None,
) -> list[str]:
    """Run a simulation step under ``base`` with the stub psimulate; return its arguments.

    With ``artifact``, the step's artifact is a root input at that path, which must exist.
    """
    log = base / "psimulate_calls.jsonl"
    monkeypatch.setenv(LOG_VARIABLE, str(log))
    for variable in KNOB_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    root_inputs: dict[str, Path] = {} if artifact is None else {"artifact": artifact}
    registry = ArtifactRegistry(base / "out", "run1", root_inputs=root_inputs)
    task = simulation_step(
        spec=write_spec(base / "model" / "toy_model.yaml"),
        branches=write_branches(base / "model" / "branches.yaml"),
        produces=_KEY,
        registry=registry,
        env=str(make_stub_env(base / "env")),
        options=options,
        artifact=None if artifact is None else "artifact",
    )
    run(Pipeline([task], base / "state", registry))
    [argv] = read_argv_log(log)
    return argv


def _parse(argv: list[str]) -> dict[str, Any]:
    """Parse ``argv`` with the installed psimulate's run command, without running it."""
    assert argv[0] == "run", argv
    command = vivarium.psimulate.cli.psimulate.commands["run"]
    with command.make_context("run", list(argv[1:])) as context:
        return dict(context.params)


def test_the_argv_with_every_option_but_no_slack_parses_with_psimulate_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The argv the stub recorded for a step with an artifact file that exists and every allowed option except no_slack, with values psimulate accepts, parses with the installed psimulate group's run command through make_context."""
    base = tmp_path.resolve()
    artifact = base / "artifacts" / "model_artifact.hdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"an artifact")
    options: dict[str, str | int | float | bool] = {
        "project": "proj_simscience",
        "queue": "all.q",
        "max_runtime": "00:10:00",
        "peak_memory": 3,
        "hardware": "r650xs",
        "max_workers": 4,
        "max_attempts": 2,
        "backup_freq": 30,
        "slack_channel": "simulation-runs",
        "slack_tag": "someone",
    }

    argv = _recorded_argv(base, monkeypatch, options, artifact)
    params = _parse(argv)

    assert "-i" in argv
    assert "--no-slack" not in argv
    assert params["project"] == "proj_simscience"


def test_the_argv_with_no_slack_parses_with_psimulate_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The argv the stub recorded for a step with no_slack=True and no Slack options parses with the installed psimulate group's run command through make_context."""
    options: dict[str, str | int | float | bool] = {
        "project": "proj_simscience",
        "no_slack": True,
    }

    argv = _recorded_argv(tmp_path.resolve(), monkeypatch, options)
    params = _parse(argv)

    assert "--no-slack" in argv
    assert params["project"] == "proj_simscience"
