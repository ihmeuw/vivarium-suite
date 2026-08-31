"""Classify a changed-file diff into libraries and fan libraries out into a CI matrix."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .loading import read_candidate_versions
from .models import (
    CandidateVersionConflictError,
    ChangedLibs,
    Lib,
    MissingPythonVersionsError,
    PythonMatrix,
    PythonMatrixEntry,
)

# Paths outside ``libs/`` that provably cannot affect any package's build. Everything
# else outside ``libs/`` is treated as shared and rebuilds every package, so a shared
# file nobody thought to list over-builds (wasteful, obvious) instead of under-building
# (untested, silent). This is deliberately a deny-list: an allow-list of shared paths
# goes stale the moment someone adds one, and CI then quietly tests nothing.
#
# The root ``Jenkinsfile`` is exempt because it drives Jenkins, not the GitHub Actions
# matrix this feeds. ``tools/`` holds the Claude Code plugins, which ship separately.
BUILD_IRRELEVANT_PATTERN = re.compile(
    r"^(README\.md|CLAUDE\.md|CONTRIBUTING\.rst|CODE_OF_CONDUCT\.rst|LICENSE"
    r"|Jenkinsfile|\.gitignore|\.gitattributes|\.readthedocs\.yaml)$"
    r"|^tools/|^\.claude-plugin/"
    r"|^\.github/(CODEOWNERS|labeler\.yml|pull_request_template\.md)$"
)


def is_shared_path(path: str) -> bool:
    """Return whether a changed path affects every package's build.

    A path is shared when it lies outside ``libs/`` - so no single package owns it -
    and is not one of the paths known to be irrelevant to a build (see
    :data:`BUILD_IRRELEVANT_PATTERN`).

    Parameters
    ----------
    path
        A repository-relative changed path.

    Returns
    -------
        Whether a change to ``path`` requires rebuilding every package.
    """
    return not path.startswith("libs/") and not BUILD_IRRELEVANT_PATTERN.search(path)


def classify_changed_libs(
    changed_files: Iterable[str], lib_names: Iterable[str]
) -> ChangedLibs:
    """Partition ``lib_names`` by what ``changed_files`` touched in each.

    Classification is purely path matching, so this needs only the library names -
    not the parsed dependency graph.

    Parameters
    ----------
    changed_files
        Repository-relative paths from the diff under test, one per item. Blank
        entries are ignored so a raw ``git diff --name-only`` dump can be passed
        through unfiltered.
    lib_names
        Every library directory name under ``libs/``.

    Returns
    -------
        A :class:`ChangedLibs`. Every name list is sorted, and names are ``libs/``
        directory names.
    """
    changed_file_paths = [path.strip() for path in changed_files if path.strip()]
    all_names = tuple(sorted(lib_names))

    source_changed = tuple(
        name
        for name in all_names
        if any(path.startswith(f"libs/{name}/") for path in changed_file_paths)
    )
    pending_release = tuple(
        name for name in all_names if f"libs/{name}/CHANGELOG.rst" in changed_file_paths
    )
    shared_changed = any(is_shared_path(path) for path in changed_file_paths)

    return ChangedLibs(
        source_changed=source_changed,
        pending_release=pending_release,
        to_build=all_names if shared_changed else source_changed,
        shared_changed=shared_changed,
    )


def build_python_matrix(names: Iterable[str], libs: Mapping[str, Lib]) -> PythonMatrix:
    """Fan each library in ``names`` out over the versions in its ``python_versions.json``.

    Each library also gets a non-gating ``experimental`` entry for every version in its
    own ``[tool.vivarium.python-support] candidates`` (see
    :func:`~.loading.read_candidate_versions`); these are python versions being
    soaked in CI without blocking the builds if they fail.

    Parameters
    ----------
    names
        Library ``name``s to build matrix entries for.
    libs
        The full set of parsed libraries.

    Returns
    -------
        A :class:`PythonMatrix`: a dict with a single ``include`` key mapping
        to a list holding one entry per library per Python version, across every
        library in ``names``. Each entry also carries an ``experimental`` flag
        of ``False`` for a supported version or ``True`` for a candidate. Entries
        are ordered by library name, then that library's supported versions in
        declared order, then its candidates ascending.

        ``include`` is empty when ``names`` is empty, which callers must detect
        before fanning out (an empty ``matrix.include`` fails a GitHub Actions
        job rather than skipping it).

        E.g., for ``engine`` supporting 3.12 and 3.13 with 3.14 as a
        candidate, alongside a ``profiling`` held back to 3.11 with no candidate::

            {
                "include": [
                    {"library": "engine", "python-version": "3.12", "experimental": False},
                    {"library": "engine", "python-version": "3.13", "experimental": False},
                    {"library": "engine", "python-version": "3.14", "experimental": True},
                    {"library": "profiling", "python-version": "3.11", "experimental": False},
                ]
            }

    Raises
    ------
    KeyError
        If any of ``names`` is not a key in ``libs``.
    MissingPythonVersionsError
        If a library's ``python_versions.json`` is absent or empty. This fails the
        build rather than silently dropping the library from the matrix.
    CandidateVersionConflictError
        If a library declares a candidate it already supports.
    """
    include: list[PythonMatrixEntry] = []
    for name in sorted(names):
        supported_versions = _read_python_versions(libs[name].path)
        if not supported_versions:
            raise MissingPythonVersionsError(
                f"libs/{name}/python_versions.json not found or empty"
            )
        candidates = read_candidate_versions(libs[name].path)
        already_supported = sorted(
            set(candidates) & set(supported_versions), key=_version_key
        )
        if already_supported:
            raise CandidateVersionConflictError(
                f"libs/{name} declares {', '.join(already_supported)} as both a "
                "supported and a candidate Python version; a promoted candidate "
                "must be removed from [tool.vivarium.python-support] candidates"
            )
        for version in supported_versions:
            include.append(
                {"library": name, "python-version": version, "experimental": False}
            )
        for candidate in sorted(candidates, key=_version_key):
            include.append(
                {"library": name, "python-version": candidate, "experimental": True}
            )
    return {"include": include}


def _version_key(version: str) -> tuple[int, int]:
    """Return ``version``'s ``(major, minor)`` as ints, so ``3.9`` sorts below ``3.10``."""
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


def _read_python_versions(lib_path: Path) -> list[str]:
    """Read a library's ``python_versions.json`` (empty list if absent)."""
    versions_file = lib_path / "python_versions.json"
    if not versions_file.exists():
        return []
    return list(json.loads(versions_file.read_text()))
