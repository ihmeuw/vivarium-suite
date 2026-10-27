"""An encapsulation of individual pipeline tasks. Front-end representations
compile to tasks, which are consumed by the backend to build the execution DAG."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pytask import PNode

from vivarium.cluster_tools.core.backend.nodes import AlwaysStaleNode


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
    code_id: PNode | None = None
    """Node whose ``state()`` fingerprints the step's code. Omit it to default to an
    :class:`~vivarium.cluster_tools.core.backend.nodes.AlwaysStaleNode`, which re-runs
    the step on every build until a code fingerprint is declared."""

    def __post_init__(self) -> None:
        """Reject an empty name and default an omitted ``code_id`` to always-stale."""
        if not self.name:
            raise ValueError(f"Task name must be a non-empty string, got {self.name!r}.")
        if self.code_id is None:
            self.code_id = AlwaysStaleNode(name=f"{self.name}::code")


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
