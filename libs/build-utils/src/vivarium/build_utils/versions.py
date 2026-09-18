"""Version-string helpers shared across the build utilities."""

from __future__ import annotations

import json
from pathlib import Path


def read_python_versions(lib_path: Path) -> list[str]:
    """Read a library's ``python_versions.json``, preserving declared order.

    Parameters
    ----------
    lib_path
        A ``libs/<name>/`` directory.

    Returns
    -------
        The declared versions, or an empty list when the file is absent. Order is
        the file's: consumers outside this package - the Jenkins deploy pipeline and
        ``make build-env`` - read the last entry as the canonical version, so this
        must not reorder.
    """
    versions_file = lib_path / "python_versions.json"
    if not versions_file.exists():
        return []
    return list(json.loads(versions_file.read_text()))


def version_key(version: str) -> tuple[int, ...]:
    """Return a numeric sort key so ``3.9`` orders before ``3.10``.

    Parameters
    ----------
    version
        A dotted numeric version string, e.g. ``"3.11"``.

    Returns
    -------
        The dotted parts as ints, for use as a ``sorted`` key.
    """
    return tuple(int(part) for part in version.split("."))
