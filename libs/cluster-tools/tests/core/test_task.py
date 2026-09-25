"""Unit tests for the backend Task model."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pytask import PNode

from vivarium.cluster_tools.core.backend.task import Task, check_unique_task_names

_MUST_NOT_BE_CALLED = "must not be called during Task construction"


class _StubNode:
    """Structural PNode with all six members and no pytask base class.

    ``state``, ``load`` and ``save`` raise so that any node I/O during Task
    construction fails the test. ``signature`` must not raise because Python
    3.11's runtime protocol check evaluates properties.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[Any, Any] = {}

    @property
    def signature(self) -> str:
        return f"stub:{self.name}"

    def state(self) -> str | None:
        raise AssertionError(_MUST_NOT_BE_CALLED)

    def load(self, is_product: bool = False) -> Any:
        raise AssertionError(_MUST_NOT_BE_CALLED)

    def save(self, value: Any) -> None:
        raise AssertionError(_MUST_NOT_BE_CALLED)


class _FiveMemberNode:
    """Node with every PNode member except ``attributes``; not a ``_StubNode`` subclass."""

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def signature(self) -> str:
        return f"stub:{self.name}"

    def state(self) -> str | None:
        raise AssertionError(_MUST_NOT_BE_CALLED)

    def load(self, is_product: bool = False) -> Any:
        raise AssertionError(_MUST_NOT_BE_CALLED)

    def save(self, value: Any) -> None:
        raise AssertionError(_MUST_NOT_BE_CALLED)


def _run_step() -> None:
    """Stand in for a Python task body; never invoked by these tests."""


def _task_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return keyword arguments for a valid Task with ``overrides`` applied on top."""
    kwargs: dict[str, Any] = {
        "name": "clean_data",
        "run": "echo hello",
        "inputs": {"raw": _StubNode("raw")},
        "outputs": {"clean": _StubNode("clean")},
        "resources": {"memory": "4G", "cores": 1},
        "env": None,
        "code_id": _StubNode("code"),
    }
    return {**kwargs, **overrides}


def _make_task(name: str) -> Task:
    """Build a valid Task distinguished only by ``name``."""
    return Task(**_task_kwargs(name=name))


@pytest.mark.parametrize(
    "run",
    [_run_step, "echo hello", ""],
    ids=["callable", "command_string", "empty_command_string"],
)
def test_task_accepts_callable_and_command_string_runs(
    run: Callable[..., Any] | str,
) -> None:
    """Construct a Task with a callable run and with a command-string run; both validate."""
    inputs: dict[str, Any] = {"raw": _StubNode("raw")}
    outputs: dict[str, Any] = {"clean": _StubNode("clean")}
    resources: dict[str, Any] = {"memory": "4G", "cores": 1, "runtime": 3600}
    code_id = _StubNode("code")

    task = Task(
        name="clean_data",
        run=run,
        inputs=inputs,
        outputs=outputs,
        resources=resources,
        env="analysis_env",
        code_id=code_id,
    )

    assert task.name == "clean_data"
    assert task.run is run
    assert task.inputs is inputs
    assert task.outputs is outputs
    assert task.resources is resources
    assert task.env == "analysis_env"
    assert task.code_id is code_id


@pytest.mark.parametrize(
    "run",
    [42, None, ["echo", "hello"]],
    ids=["int", "none", "list"],
)
def test_task_rejects_run_that_is_neither_callable_nor_str(run: Any) -> None:
    """Reject a run that is neither callable nor str with an error naming the task."""
    with pytest.raises(TypeError, match=r"Task 'clean_data'.*\brun\b"):
        Task(**_task_kwargs(run=run))


def test_task_rejects_empty_name() -> None:
    """Reject an empty name with an error naming the field."""
    with pytest.raises(ValueError, match="non-empty"):
        Task(**_task_kwargs(name=""))


def test_task_accepts_structurally_conforming_nodes() -> None:
    """Accept an in-test node class with the six PNode members and no pytask base."""
    assert _StubNode.__mro__ == (_StubNode, object)
    assert _FiveMemberNode.__mro__ == (_FiveMemberNode, object)
    assert isinstance(_StubNode("n"), PNode)
    assert not isinstance(_FiveMemberNode("n"), PNode)

    code_id = _StubNode("code")
    task = Task(
        name="clean_data",
        run=_run_step,
        inputs={"raw": _StubNode("raw"), "config": _StubNode("config")},
        outputs={"clean": _StubNode("clean")},
        resources={},
        env=None,
        code_id=code_id,
    )

    assert task.code_id is code_id
    assert set(task.inputs) == {"raw", "config"}
    assert set(task.outputs) == {"clean"}


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("inputs", Path("/data/raw.csv")),
        ("outputs", Path("/data/clean.csv")),
        ("code_id", Path("/src/clean_data.py")),
        ("inputs", _FiveMemberNode("raw")),
        ("outputs", _FiveMemberNode("clean")),
        ("code_id", _FiveMemberNode("code")),
    ],
    ids=[
        "inputs_path",
        "outputs_path",
        "code_id_path",
        "inputs_five_member_node",
        "outputs_five_member_node",
        "code_id_five_member_node",
    ],
)
def test_task_rejects_non_conforming_node_values(field: str, bad_value: Any) -> None:
    """Reject a bare Path or a five-member node in inputs, outputs, or code_id."""
    if field == "code_id":
        kwargs = _task_kwargs(code_id=bad_value)
        expected = r"Task 'clean_data'.*code_id"
    else:
        nodes = {"good": _StubNode("good"), "bad_key": bad_value}
        kwargs = _task_kwargs(**{field: nodes})
        expected = rf"Task 'clean_data'.*{field}\['bad_key'\]"

    with pytest.raises(TypeError, match=expected):
        Task(**kwargs)


def test_check_unique_task_names() -> None:
    """Raise on duplicate task names, listing them; accept distinct names and no tasks."""
    with pytest.raises(ValueError, match=r"\['a'\]"):
        check_unique_task_names([_make_task("a"), _make_task("a"), _make_task("b")])

    with pytest.raises(ValueError, match=r"\['a', 'b'\]"):
        check_unique_task_names([_make_task(n) for n in ("b", "a", "b", "a")])

    with pytest.raises(ValueError, match=r"\['a'\]"):
        check_unique_task_names(_make_task(n) for n in ("a", "b", "a"))

    check_unique_task_names([_make_task("a"), _make_task("b")])
    check_unique_task_names([])
    check_unique_task_names(_make_task(n) for n in ("a", "b"))
