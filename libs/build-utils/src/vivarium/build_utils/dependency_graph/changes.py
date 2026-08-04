"""Classify a changed-file diff into libraries and fan libraries out into a CI matrix."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .models import Lib, MissingPythonVersionsError

# Paths outside any ``libs/<lib>/`` that affect every package. A change to one of
# these builds the whole matrix rather than nothing, since no single library owns it.
# ``.github/actions/`` counts for the same reason ``.github/workflows/`` does: it holds
# the shared CI recipe every per-library job runs.
SHARED_PATH_PATTERN = re.compile(
    r"^(pyproject\.toml|Makefile)$|^\.github/(workflows|actions)/"
)

MatrixEntry = TypedDict("MatrixEntry", {"library": str, "python-version": str})


class PythonMatrix(TypedDict):
    """The GitHub Actions ``strategy.matrix`` object for a per-library job."""

    include: list[MatrixEntry]


@dataclass(frozen=True)
class ChangedLibs:
    """The libraries a diff touched, partitioned by what CI does about each.

    Attributes
    ----------
    source_changed
        Libraries with at least one changed file under ``libs/<name>/``. These are
        the libraries to resolve editably from in-tree source when installing any
        library under test, since their pending versions do not exist on PyPI.
    releasing
        Libraries whose ``CHANGELOG.rst`` changed, i.e. those the diff is bumping
        toward a release. Always a subset of ``source_changed``.
    build
        Libraries whose full check suite CI should run: ``source_changed``, or every
        library when the diff touches a shared path (see ``shared_changed``).
    shared_changed
        Whether the diff touched a path matching :data:`SHARED_PATH_PATTERN`.
    """

    source_changed: tuple[str, ...]
    releasing: tuple[str, ...]
    build: tuple[str, ...]
    shared_changed: bool


def classify_changed_libs(
    changed_files: Iterable[str], libs: Mapping[str, Lib]
) -> ChangedLibs:
    """Partition ``libs`` by what ``changed_files`` touched in each.

    Parameters
    ----------
    changed_files
        Repository-relative paths from the diff under test, one per item. Blank
        entries are ignored so a raw ``git diff --name-only`` dump can be passed
        through unfiltered.
    libs
        The full set of parsed libraries.

    Returns
    -------
        A :class:`ChangedLibs`. Every name list is sorted, and names are ``libs/``
        directory names.
    """
    paths = [path.strip() for path in changed_files if path.strip()]
    all_names = tuple(sorted(libs))

    source_changed = tuple(
        name for name in all_names if any(p.startswith(f"libs/{name}/") for p in paths)
    )
    releasing = tuple(name for name in all_names if f"libs/{name}/CHANGELOG.rst" in paths)
    shared_changed = any(SHARED_PATH_PATTERN.search(path) for path in paths)

    return ChangedLibs(
        source_changed=source_changed,
        releasing=releasing,
        build=all_names if shared_changed else source_changed,
        shared_changed=shared_changed,
    )


def build_python_matrix(names: Iterable[str], libs: Mapping[str, Lib]) -> PythonMatrix:
    """Fan each library in ``names`` out over the versions in its ``python_versions.json``.

    Parameters
    ----------
    names
        Library ``name``s to build matrix entries for.
    libs
        The full set of parsed libraries.

    Returns
    -------
        A :class:`PythonMatrix` with one ``include`` entry per library per supported
        Python version, ordered by library name then declared version order. Empty
        when ``names`` is empty, which callers must detect before fanning out: an
        empty ``matrix.include`` fails a GitHub Actions job rather than skipping it.

    Raises
    ------
    KeyError
        If any of ``names`` is not a key in ``libs``.
    MissingPythonVersionsError
        If a library's ``python_versions.json`` is absent or empty. This fails the
        build rather than silently dropping the library from the matrix.
    """
    include: list[MatrixEntry] = []
    for name in sorted(names):
        if name not in libs:
            raise KeyError(name)
        versions = _read_python_versions(libs[name].path)
        if not versions:
            raise MissingPythonVersionsError(
                f"libs/{name}/python_versions.json not found or empty"
            )
        for python_version in versions:
            include.append({"library": name, "python-version": python_version})
    return {"include": include}


def _read_python_versions(lib_path: Path) -> list[str]:
    """Read a library's ``python_versions.json`` (empty list if absent)."""
    versions_file = lib_path / "python_versions.json"
    if not versions_file.exists():
        return []
    return list(json.loads(versions_file.read_text()))
