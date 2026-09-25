"""Unit tests for the backend Task model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytask import PNode

from vivarium.cluster_tools.core.backend.task import Task, check_unique_task_names

_MUST_NOT_BE_CALLED = "must not be called during Task construction"


class _StubNode:
    """Node that satisfies pytask's ``PNode`` protocol structurally, with no pytask base.

    ``PNode`` is a runtime-checkable protocol: pytask treats any object with these six
    members as a node, and ``Task`` promises to accept such objects.
    ``state``, ``load`` and ``save`` raise because the runtime check is supposed to
    not actually invoke them during Task construction.
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


def _stub_node_missing_attributes() -> _StubNode:
    """Return a stub node with ``attributes`` deleted, so it no longer satisfies PNode."""
    node = _StubNode("n")
    del node.attributes
    return node


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
    """Accept an in-test node class with the six PNode members and no pytask base.

    The ``isinstance`` assertions cross-check the stub against the real protocol so the
    six-member list cannot drift silently. The identity assertions pin that nodes are
    stored by reference, which the producer-to-consumer wiring relies on.
    """
    assert _StubNode.__mro__ == (_StubNode, object)
    assert isinstance(_StubNode("n"), PNode)
    assert not isinstance(_stub_node_missing_attributes(), PNode)

    raw = _StubNode("raw")
    code_id = _StubNode("code")
    task = Task(
        name="clean_data",
        run=_run_step,
        inputs={"raw": raw, "config": _StubNode("config")},
        outputs={"clean": _StubNode("clean")},
        resources={},
        env=None,
        code_id=code_id,
    )

    assert task.inputs["raw"] is raw
    assert task.code_id is code_id
    assert set(task.inputs) == {"raw", "config"}
    assert set(task.outputs) == {"clean"}


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("inputs", Path("/data/raw.csv")),
        ("outputs", Path("/data/clean.csv")),
        ("code_id", Path("/src/clean_data.py")),
        ("inputs", _stub_node_missing_attributes()),
        ("outputs", _stub_node_missing_attributes()),
        ("code_id", _stub_node_missing_attributes()),
    ],
    ids=[
        "inputs_path",
        "outputs_path",
        "code_id_path",
        "inputs_missing_attributes",
        "outputs_missing_attributes",
        "code_id_missing_attributes",
    ],
)
def test_task_rejects_non_conforming_node_values(field: str, bad_value: Any) -> None:
    """Reject a bare Path or a node missing a PNode member in inputs, outputs, or code_id.

    The bad value sits behind a valid one so the error must name the offending key.
    """
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
