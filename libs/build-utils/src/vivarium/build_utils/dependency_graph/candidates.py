"""Fan candidate Python versions out into a check matrix, and guard their declaration.

A *candidate* is a Python version a library wants exercised before committing to
support it, declared in its own ``pyproject.toml``, e.g.::

    [tool.vivarium.python-support]
    candidates = ["3.14"]

Candidates never enter the matrices that gate a merge - they are checked on a
schedule against ``main`` (see ``.github/workflows/candidate-check.yml``), so a
version the ecosystem is not ready for cannot block anyone's PR. Promoting one is
an ordinary PR: move it into ``python_versions.json``, drop it from ``candidates``,
and it becomes a gating matrix entry like any other supported version.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..versions import read_python_versions, version_key
from .models import CandidateVersionConflictError, Lib, PythonMatrix, PythonMatrixEntry


def find_candidate_conflicts(libs: Mapping[str, Lib]) -> dict[str, list[str]]:
    """Find libraries declaring a Python version as both supported and a candidate.

    This is the half-finished promotion: the version was added to
    ``python_versions.json`` but not removed from ``candidates``, which would check a
    version that is already gating.

    Parameters
    ----------
    libs
        The full set of parsed libraries.

    Returns
    -------
        Library ``name`` mapped to its conflicting versions, ascending. Libraries
        with no conflict are absent, so an empty mapping means the tree is clean.
    """
    conflicts: dict[str, list[str]] = {}
    for name, lib in sorted(libs.items()):
        supported = set(read_python_versions(lib.path))
        overlap = sorted(set(lib.candidates) & supported, key=version_key)
        if overlap:
            conflicts[name] = overlap
    return conflicts


def build_candidate_matrix(libs: Mapping[str, Lib]) -> PythonMatrix:
    """Fan every library out over the candidate versions it declares.

    Parameters
    ----------
    libs
        The full set of parsed libraries. Every library is considered, not a
        changed subset: the weekly run asks "what is each library's state on its
        candidates", which no diff narrows.

    Returns
    -------
        A :class:`~.models.PythonMatrix` holding one entry per library per declared
        candidate, ordered by library name then candidate ascending. Duplicates
        within a library's declaration are collapsed, so a repeated version cannot
        emit the same job twice.

        ``include`` is empty when no library declares a candidate, which callers
        must detect before fanning out - an empty ``matrix.include`` fails a GitHub
        Actions job rather than skipping it.

        E.g. for an ``engine`` checking 3.14 and a ``validation`` catching up on two
        versions at once, with every other library declaring none::

            {
                "include": [
                    {"library": "engine", "python-version": "3.14"},
                    {"library": "validation", "python-version": "3.13"},
                    {"library": "validation", "python-version": "3.14"},
                ]
            }

    Raises
    ------
    CandidateVersionConflictError
        If any library declares a candidate it already supports. Raised here rather
        than skipped so a half-finished promotion cannot quietly check a version that
        is already gating.
    """
    conflicts = find_candidate_conflicts(libs)
    if conflicts:
        raise CandidateVersionConflictError(format_candidate_conflicts(conflicts))

    include: list[PythonMatrixEntry] = []
    for name, lib in sorted(libs.items()):
        for candidate in sorted(set(lib.candidates), key=version_key):
            include.append({"library": name, "python-version": candidate})
    return {"include": include}


def format_candidate_conflicts(conflicts: Mapping[str, list[str]]) -> str:
    """Render :func:`find_candidate_conflicts` output as one error message."""
    declarations = ", ".join(
        f"libs/{name} declares {', '.join(versions)}"
        for name, versions in sorted(conflicts.items())
    )
    return (
        f"{declarations} as both a supported and a candidate Python version; a "
        "promoted candidate must be removed from [tool.vivarium.python-support] "
        "candidates"
    )
