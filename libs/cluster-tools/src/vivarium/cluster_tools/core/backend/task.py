"""An encapsulation of individual pipeline tasks."""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pytask import PNode


@dataclass
class Task:
    """A single pipeline task, ready to be built into a pytask DAG.

    This is the frontend-agnostic representation that both the YAML and Python
    authoring frontends compile to and that the pytask build path consumes.
    """

    name: str
    """Name of the task, unique within its pipeline."""
    run: Callable[..., Any] | str
    """Python callable to execute, or a shell command string to run."""
    inputs: dict[str, PNode]
    """Dependency nodes keyed by name. Each must satisfy pytask's ``PNode`` protocol."""
    outputs: dict[str, PNode]
    """Product nodes keyed by name. Each must satisfy pytask's ``PNode`` protocol."""
    resources: dict[str, Any]
    """Compute resources, kept in the shape Jobmon's ``compute_resources`` expects."""
    env: str | None
    """Environment to run the task in. ``None`` means the pipeline's own environment."""
    code_id: PNode
    """Node whose ``state()`` fingerprints the step's code. A dynamically built task has
    no useful source hash of its own, so this node stands in for one."""

    def __post_init__(self) -> None:
        """Validate the name, the run target, and every node-valued field."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"Task name must be a non-empty string, got {self.name!r}.")
        if not (callable(self.run) or isinstance(self.run, str)):
            raise TypeError(
                f"Task '{self.name}': run must be a callable or a shell-command string, "
                f"got {type(self.run).__name__}: {self.run!r}."
            )
        for field_name, nodes in (("inputs", self.inputs), ("outputs", self.outputs)):
            for key, value in nodes.items():
                if not isinstance(value, PNode):
                    raise TypeError(
                        f"Task '{self.name}': {field_name}[{key!r}] must satisfy pytask's "
                        f"PNode protocol, got {type(value).__name__}."
                    )
        if not isinstance(self.code_id, PNode):
            raise TypeError(
                f"Task '{self.name}': code_id must satisfy pytask's PNode protocol, "
                f"got {type(self.code_id).__name__}."
            )


def check_unique_task_names(tasks: Iterable[Task]) -> None:
    """Raise if any two tasks share a name.

    pytask's ``TaskWithoutPath.signature`` is a hash of the task name alone, and
    pytask performs no uniqueness check on pre-built tasks, so two same-named tasks
    would silently merge into one DAG node.

    Parameters
    ----------
    tasks
        Tasks to check. Consumed exactly once, so a one-shot iterator is accepted.

    Raises
    ------
    ValueError
        If any task name occurs more than once.
    """
    counts = Counter(task.name for task in tasks)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"Task names must be unique. Duplicate names found: {duplicates}.")
