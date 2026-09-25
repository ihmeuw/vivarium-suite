"""An encapsulation of individual pipeline tasks. Front-end representations
compile to tasks, which are consumed by the backend to build the execution DAG."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pytask import PNode


@dataclass
class Task:
    """A single pipeline task, ready to be built into a pytask DAG."""

    name: str
    """Name of the task, unique within its pipeline."""
    run: Callable[..., Any] | str
    """Python callable to execute, or a shell command string to run."""
    inputs: Mapping[str, PNode]
    """Dependency nodes keyed by name."""
    outputs: Mapping[str, PNode]
    """Product nodes keyed by name."""
    resources: dict[str, Any]
    """Compute resources following Jobmon's ``compute_resources``."""
    env: str | None
    """Environment to run the task in. ``None`` means the pipeline's own environment."""
    code_id: PNode
    """Node whose ``state()`` fingerprints the step's code."""

    def __post_init__(self) -> None:
        """Validate the name, the run target, and every node-valued field.

        ``resources`` and ``env`` are not checked here; the Jobmon bridge and the
        environment resolution that consume them own their validation.
        """
        if not isinstance(self.name, str):
            raise TypeError(
                f"Task name must be a string, got {type(self.name).__name__}: {self.name!r}."
            )
        if not self.name:
            raise ValueError(f"Task name must be a non-empty string, got {self.name!r}.")
        if not (callable(self.run) or isinstance(self.run, str)):
            raise TypeError(
                f"Task '{self.name}': run must be a callable or a shell-command string, "
                f"got {type(self.run).__name__}: {self.run!r}."
            )
        for field_name, nodes in (("inputs", self.inputs), ("outputs", self.outputs)):
            if not isinstance(nodes, Mapping):
                raise TypeError(
                    f"Task '{self.name}': {field_name} must be a mapping of argument name "
                    f"to node, got {type(nodes).__name__}."
                )
            for key, value in nodes.items():
                self._check_node(value, f"{field_name}[{key!r}]")
        self._check_node(self.code_id, "code_id")

    def _check_node(self, value: object, label: str) -> None:
        """Raise unless ``value`` satisfies pytask's PNode protocol."""
        if not isinstance(value, PNode):
            raise TypeError(
                f"Task '{self.name}': {label} must satisfy pytask's PNode protocol, "
                f"got {type(value).__name__}."
            )


def check_unique_task_names(tasks: Iterable[Task]) -> None:
    """Raise if any two tasks share a name. pytask hashes on the task name
    and overwrites tasks without raising.

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
