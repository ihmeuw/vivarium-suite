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


@dataclass(kw_only=True)
class RunDirectoryNode(PathNode):
    """A directory written whole by one command, whose state is a sentinel file inside it.

    The command writes the sentinel last, only once everything else in the directory is
    complete. The node's state is the hash of that one file, so a directory without it
    counts as missing and its producer runs again, and a new sentinel marks the directory
    changed for its consumers. The other files in the directory are not hashed.

    ``load()`` returns the directory's path, so a consumer receives the directory and
    reads the files in it. The directory cannot be saved from a value. ``signature`` is
    pytask's ``PathNode`` signature, a hash of the path, so the node keeps one identity in
    the DAG across builds.
    """

    sentinel: str
    """The file name, inside the directory, of the file whose content is the node's state."""

    def state(self) -> str | None:
        """Return the sha256 hex digest of the sentinel file's bytes, or ``None`` if it is missing.

        The state is also ``None`` when the directory itself is missing or is a file.
        """
        try:
            content = (self.path / self.sentinel).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return hashlib.sha256(content).hexdigest()

    def save(self, value: Any) -> None:
        """Reject saves: the directory is written by its command, not saved from a value.

        Raises
        ------
        TypeError
            Always.
        """
        raise TypeError(
            f"The run directory {self.path} is written by its command and cannot be saved "
            f"from a value of type {type(value).__name__}."
        )


def run_directory_node(path: Path | str, sentinel: str) -> RunDirectoryNode:
    """Return a node for the run directory at ``path`` whose state is its ``sentinel`` file.

    Parameters
    ----------
    path
        The run directory. It is resolved to an absolute path, and the node's name is
        that path in POSIX form, as for :func:`file_node`.
    sentinel
        The file name, inside the directory, of the file whose content is the node's
        state.

    Returns
    -------
        The node, whose ``path`` is the resolved directory.
    """
    resolved = Path(path).resolve()
    return RunDirectoryNode(name=resolved.as_posix(), path=resolved, sentinel=sentinel)


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
