"""Unit tests for the backend Task model."""

from __future__ import annotations

from typing import Any

import pytest

from vivarium.cluster_tools.core.backend.task import Task, check_unique_task_names

_MUST_NOT_BE_CALLED = "must not be called during Task construction"


class _StubNode:
    """Node with pytask's six ``PNode`` members whose methods raise if called.

    Constructing a ``Task`` must never touch its nodes, so any call fails the test.
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


def test_task_rejects_empty_name() -> None:
    """Reject an empty name."""
    with pytest.raises(ValueError, match="non-empty"):
        Task(**_task_kwargs(name=""))


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
