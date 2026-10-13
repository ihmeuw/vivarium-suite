"""Node constructors for backend tasks."""

from pathlib import Path

from pytask import PathNode


def file_node(path: Path | str) -> PathNode:
    """Return a content-hashed node for the file at ``path``, resolved to an absolute path."""
    return PathNode.from_path(Path(path).resolve())
