"""Tests for running command steps under a declared environment (``backend.build``)."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from pytask import TaskOutcome

from vivarium.workflow.backend.build import (
    build,
    outcomes,
    run_command,
    run_command_with_bin_path,
)
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task

_TOOL = "vivarium_workflow_env_probe"


def _make_env(prefix: Path) -> Path:
    """Create a fake env at ``prefix`` with ``bin/python`` and a ``_TOOL`` script."""
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").touch()
    tool = bin_dir / _TOOL
    tool.write_text('#!/bin/sh\necho "$1" >> "$2"\n')
    tool.chmod(0o755)
    return prefix


def _make_tool_dir(bin_dir: Path) -> Path:
    """Create ``bin_dir`` holding a ``_TOOL`` script but no ``python``, and return it."""
    bin_dir.mkdir(parents=True)
    tool = bin_dir / _TOOL
    tool.write_text('#!/bin/sh\necho "$1" >> "$2"\n')
    tool.chmod(0o755)
    return bin_dir


def _fake_conda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *prefixes: Path) -> Path:
    """Point ``CONDA_EXE`` at a script listing ``prefixes``; return its call log."""
    calls = tmp_path / "conda_calls.log"
    calls.touch()
    listing = tmp_path / "conda_envs.json"
    listing.write_text(json.dumps({"envs": [str(prefix) for prefix in prefixes]}))
    conda = tmp_path / "fake_conda"
    conda.write_text(f"#!/bin/sh\necho call >> '{calls}'\ncat '{listing}'\n")
    conda.chmod(0o755)
    monkeypatch.setenv("CONDA_EXE", str(conda))
    return calls


def _command_task(name: str, run: str, env: str | None, product: Path) -> Task:
    """Build a command task that produces ``product`` under ``env``."""
    return Task(
        name=name,
        run=run,
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=env,
    )


def test_run_command_puts_the_env_bin_first_on_path(tmp_path: Path) -> None:
    """A tool that exists only in the env's bin directory runs when the env is declared."""
    env, log, seen = _make_env(tmp_path / "env"), tmp_path / "log", tmp_path / "path"

    run_command(f"{_TOOL} ran {log}", str(env))
    run_command(f'printf %s "$PATH" > {seen}', str(env))

    assert log.read_text() == "ran\n"
    assert seen.read_text() == f"{env / 'bin'}:{os.environ['PATH']}"


def test_run_command_covers_every_part_of_a_compound_command(tmp_path: Path) -> None:
    """Both halves of 'tool a && tool b' resolve the tool from the declared env."""
    env, log = _make_env(tmp_path / "env"), tmp_path / "log"

    run_command(f"{_TOOL} first {log} && {_TOOL} second {log}", str(env))

    assert log.read_text() == "first\nsecond\n"


def test_run_command_without_env_uses_the_current_path(tmp_path: Path) -> None:
    """With env None the command runs with the current PATH unchanged."""
    seen = tmp_path / "path"

    run_command(f'printf %s "$PATH" > {seen}', None)

    assert seen.read_text() == os.environ["PATH"]


def test_run_command_rejects_an_env_that_does_not_resolve(tmp_path: Path) -> None:
    """An env that does not resolve raises RuntimeError before the command runs."""
    marker = tmp_path / "ran"

    with pytest.raises(RuntimeError):
        run_command(f"touch {marker}", str(tmp_path / "missing"))

    assert not marker.exists()


def test_run_command_raises_on_a_nonzero_exit(tmp_path: Path) -> None:
    """A command that exits non-zero raises subprocess.CalledProcessError."""
    for env in (None, str(_make_env(tmp_path / "env"))):
        with pytest.raises(subprocess.CalledProcessError) as info:
            run_command("exit 3", env)
        assert info.value.returncode == 3


def test_run_command_adds_no_empty_path_entry_when_path_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With PATH unset the child's PATH holds only the env's bin directories, with no empty entry."""
    env, seen = _make_env(tmp_path / "env"), tmp_path / "path"
    monkeypatch.delenv("PATH")

    run_command(f'printf %s "$PATH" > {seen}', str(env))

    assert seen.read_text() == str(env / "bin")


def test_run_command_with_bin_path_puts_every_directory_first_on_path(
    tmp_path: Path,
) -> None:
    """A colon-joined bin_path of two directories precedes the current PATH in the child, and a tool found only in the second directory runs."""
    first, second = tmp_path / "first", _make_tool_dir(tmp_path / "second")
    first.mkdir()
    bin_path, log, seen = f"{first}:{second}", tmp_path / "log", tmp_path / "path"

    run_command_with_bin_path(f"{_TOOL} ran {log}", bin_path)
    run_command_with_bin_path(f'printf %s "$PATH" > {seen}', bin_path)

    assert log.read_text() == "ran\n"
    assert seen.read_text() == f"{bin_path}:{os.environ['PATH']}"


def test_run_command_with_bin_path_none_uses_the_current_path(tmp_path: Path) -> None:
    """With bin_path None the command runs with the current PATH unchanged."""
    seen = tmp_path / "path"

    run_command_with_bin_path(f'printf %s "$PATH" > {seen}', None)

    assert seen.read_text() == os.environ["PATH"]


def test_run_command_with_bin_path_covers_every_part_of_a_compound_command(
    tmp_path: Path,
) -> None:
    """Both halves of 'tool a && tool b' resolve the tool from bin_path."""
    bin_dir, log = _make_tool_dir(tmp_path / "bin"), tmp_path / "log"

    run_command_with_bin_path(f"{_TOOL} first {log} && {_TOOL} second {log}", str(bin_dir))

    assert log.read_text() == "first\nsecond\n"


@pytest.mark.parametrize("path", [None, ""], ids=["unset", "empty"])
def test_run_command_with_bin_path_adds_no_empty_path_entry_when_path_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str | None
) -> None:
    """With PATH unset the child's PATH is exactly bin_path, with no empty entry."""
    bin_path = f"{_make_tool_dir(tmp_path / 'first')}:{tmp_path / 'second'}"
    seen = tmp_path / "path"
    if path is None:
        monkeypatch.delenv("PATH")
    else:
        monkeypatch.setenv("PATH", path)

    run_command_with_bin_path(f'printf %s "$PATH" > {seen}', bin_path)

    assert seen.read_text() == bin_path


def test_run_command_with_bin_path_raises_on_a_nonzero_exit(tmp_path: Path) -> None:
    """A command that exits non-zero raises subprocess.CalledProcessError with its exit status."""
    for bin_path in (None, str(_make_tool_dir(tmp_path / "bin"))):
        with pytest.raises(subprocess.CalledProcessError) as info:
            run_command_with_bin_path("exit 3", bin_path)
        assert info.value.returncode == 3


def test_run_command_with_bin_path_does_not_require_bin_python(tmp_path: Path) -> None:
    """A bin directory holding a tool but no python, as in an R-only env, runs the tool; no env check is made."""
    bin_dir, log = _make_tool_dir(tmp_path / "r_env" / "bin"), tmp_path / "log"

    run_command_with_bin_path(f"{_TOOL} ran {log}", str(bin_dir))

    assert not (bin_dir / "python").exists()
    assert log.read_text() == "ran\n"


def test_build_runs_a_command_task_under_its_declared_env(tmp_path: Path) -> None:
    """End to end: build() runs a command Task whose env is a prefix path, the tool comes from that env, and its product is written."""
    env, product = _make_env(tmp_path / "env"), tmp_path / "product.txt"
    task = _command_task("tool_step", f"{_TOOL} built {product}", str(env), product)

    assert outcomes(build([task], tmp_path / "state")) == {"tool_step": TaskOutcome.SUCCESS}
    assert product.read_text() == "built\n"


def test_build_rejects_unresolved_envs_before_any_task_runs(tmp_path: Path) -> None:
    """build() raises one ValueError naming each unresolved env once with every task that declares it, and no task runs and no lockfile is written."""
    shared, single = str(tmp_path / "missing_shared"), str(tmp_path / "missing_single")
    good = str(_make_env(tmp_path / "good_env"))
    envs = {"alpha": shared, "beta": shared, "gamma": single, "delta": good}
    products = {name: tmp_path / f"{name}.txt" for name in envs}
    tasks = [
        _command_task(name, f"touch {products[name]}", env, products[name])
        for name, env in envs.items()
    ]
    root = tmp_path / "state"

    with pytest.raises(ValueError) as info:
        build(tasks, root)

    message = str(info.value)
    lines = message.splitlines()
    for env, declared in ((shared, {"alpha", "beta"}), (single, {"gamma"})):
        naming = [line for line in lines if env in line]
        assert len(naming) == 1, message
        assert naming[0].count(env) == 1, naming[0]
        assert "bin/python" in naming[0], naming[0]
        named = {name for name in envs if re.search(rf"\b{name}\b", naming[0])}
        assert named == declared, naming[0]
    assert "delta" not in message
    assert good not in message
    assert not any(product.exists() for product in products.values())
    assert not (root / "pytask.lock").exists()


def test_preview_rejects_unresolved_envs(tmp_path: Path) -> None:
    """preview() raises the same ValueError for an unresolved env."""
    missing, product = str(tmp_path / "missing"), tmp_path / "product.txt"
    tasks = [_command_task("alpha", f"touch {product}", missing, product)]

    with pytest.raises(ValueError) as built:
        build(tasks, tmp_path / "state")
    with pytest.raises(ValueError) as previewed:
        preview(tasks, tmp_path / "state")

    assert missing in str(previewed.value)
    assert str(previewed.value) == str(built.value)


def test_build_resolves_each_distinct_env_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many tasks that share one env cause one resolution of it during the pre-build check."""
    monkeypatch.chdir(tmp_path)
    shared = _make_env(tmp_path / "conda_envs" / "shared")
    calls = _fake_conda(tmp_path, monkeypatch, shared)
    names = ("first", "second", "third")
    tasks = [_command_task(name, "true", "shared", tmp_path / name) for name in names]

    build(tasks, tmp_path / "state", dry_run=True)

    assert calls.read_text().splitlines() == ["call"]
