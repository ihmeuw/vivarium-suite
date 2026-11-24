"""Record every file and folder under a directory, to show that an action wrote none.

:func:`age_files` first sets each file's modification time far into the past, so that a
file rewritten with the same bytes still shows as changed. This module imports only the
standard library, so the tests of the backend and of the frontend can both use it.
"""

import os
from pathlib import Path

Snapshot = dict[str, tuple[bytes, int] | None]
"""Each path under a directory, relative to it: a file's bytes and ``st_mtime_ns``, or
``None`` for a folder."""

_PAST_NS = 1_000_000_000 * 10**9
"""A modification time, in nanoseconds, that no file written during a test has."""


def age_files(root: Path) -> None:
    """Set the access and modification time of every file under ``root`` to the past."""
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, ns=(_PAST_NS, _PAST_NS))


def snapshot_files(root: Path) -> Snapshot:
    """Return each file and folder under ``root`` by relative path, as a :data:`Snapshot`."""
    # A folder's mtime changes whenever a file in it is made or removed, even briefly, so
    # only its presence is recorded.
    return {
        path.relative_to(root).as_posix(): (
            (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }


def changed_files(before: Snapshot, after: Snapshot) -> list[str]:
    """Return the relative paths that appeared, disappeared, or changed bytes or mtime."""
    return sorted(
        path
        for path in before.keys() | after.keys()
        if path not in before or path not in after or before[path] != after[path]
    )
