"""An encapsulation of individual pipeline tasks."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pytask import PNode


@dataclass
class Task:
    name: str
    run: Callable[..., Any] | str
    inputs: dict[str, PNode]
    outputs: dict[str, PNode]
    resources: dict[str, Any]
    env: str | None
    code_id: PNode
