"""Node constructors for backend tasks."""

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytask import PathNode


def file_node(path: Path | str) -> PathNode:
    """Return a content-hashed node for the file at ``path``, resolved to an absolute path."""
    return PathNode.from_path(Path(path).resolve())


@dataclass
class AlwaysStaleNode:
    """A read-only node that reports a new state on every build, so its task always re-runs.

    This is the default code identity for a step that has not declared a code
    fingerprint. Without a fingerprint there is no way to prove the step's code is
    unchanged between builds, so the safe default is to assume it changed and re-run.
    Declaring a fingerprint is the opt-in that lets a step skip when its code is
    unchanged.

    It satisfies pytask's node protocol as a dependency. ``state()`` returns a fresh
    value on every call, which never matches the state pytask recorded on the previous
    build, so the step is always stale on this dimension. ``signature`` is stable, so
    the node keeps one identity in the DAG across builds.
    """

    name: str
    """Identifier for the node, unique within its task."""
    attributes: dict[Any, Any] = field(default_factory=dict)
    """pytask node attributes; unused, kept to satisfy the node protocol."""

    @property
    def signature(self) -> str:
        """A stable hash of the node name, so its DAG identity is fixed across builds."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def state(self) -> str | None:
        """Return a fresh value on every call, so the node always compares as changed."""
        return uuid.uuid4().hex

    def load(self, is_product: bool = False) -> "AlwaysStaleNode":
        """Return the node itself; a code identity carries no value the task reads."""
        return self

    def save(self, value: Any) -> None:
        """Reject saves: this is a dependency, not a product."""
        raise NotImplementedError("AlwaysStaleNode is a dependency and cannot be saved.")
