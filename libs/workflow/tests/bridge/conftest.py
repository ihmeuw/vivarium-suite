"""Fixtures shared by the bridge tests."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from vivarium.workflow.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.workflow.backend.task import Task

_PROBE_TOOL = "vivarium_workflow_bridge_probe"
"""A tool that exists only in the fake env bin directories these tests make."""


@pytest.fixture
def fake_conda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Return a function that points CONDA_EXE at a script listing the given prefixes.

    The function returns a file that gets one line each time the script runs, so a test
    can count conda lookups.
    """

    def point_conda_at(*prefixes: Path) -> Path:
        listing = tmp_path / "conda_envs.json"
        listing.write_text(json.dumps({"envs": [str(prefix) for prefix in prefixes]}))
        calls = tmp_path / "conda_calls.log"
        calls.touch()
        conda = tmp_path / "fake_conda"
        conda.write_text(f"#!/bin/sh\necho \"$*\" >> '{calls}'\ncat '{listing}'\n")
        conda.chmod(0o755)
        monkeypatch.setenv("CONDA_EXE", str(conda))
        return calls

    return point_conda_at


@pytest.fixture
def probe_tool() -> str:
    """Return the name of the tool that ``make_tool_dir`` puts in a bin directory."""
    return _PROBE_TOOL


@pytest.fixture
def make_tool_dir() -> Callable[[Path], Path]:
    """Return a function that creates a bin directory holding only the probe tool.

    The tool is a shell script that prints ``<tool> says <its first argument>``. The
    directory has no ``python``.
    """

    def make(bin_dir: Path) -> Path:
        bin_dir.mkdir(parents=True)
        tool = bin_dir / _PROBE_TOOL
        tool.write_text(f'#!/bin/sh\necho "{_PROBE_TOOL} says $1"\n')
        tool.chmod(0o755)
        return bin_dir

    return make


@pytest.fixture
def pytask_kwargs() -> Callable[[Task], dict[str, Any]]:
    """Return a function giving the keyword arguments pytask passes to a task's function."""

    def kwargs_for(task: Task) -> dict[str, Any]:
        kwargs = {name: node.load() for name, node in task.inputs.items()}
        kwargs.update(
            {name: node.load(is_product=True) for name, node in task.outputs.items()}
        )
        assert task.code_id is not None
        kwargs[CODE_ID_KEY] = task.code_id.load()
        if isinstance(task.run, str):
            kwargs[COMMAND_KEY] = task.run
        return kwargs

    return kwargs_for
