"""Load the in-tree dependency graph from the ``libs/`` tree on disk."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from functools import reduce
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python < 3.11
    import tomli as tomllib

from .models import DEFAULT_EXTRAS, Lib


def load_libs(libs_dir: Path, extras: Sequence[str] = DEFAULT_EXTRAS) -> dict[str, Lib]:
    """Parse every library under ``libs_dir`` into a :class:`Lib`.

    For each ``libs/<pkg>`` directory, reads the distribution name and the
    declared dependencies from ``pyproject.toml`` and the pending version from
    the first line of ``CHANGELOG.rst``. Dependencies are resolved over
    ``[project].dependencies`` plus the requested ``extras``, transitively
    expanding self-referential extras (e.g. ``ci_github = ["vivarium-foo[test,
    docs]"]`` pulls in the requirements of ``foo``'s ``test`` and ``docs``
    extras). Only requirements whose distribution name matches another library
    under ``libs_dir`` are retained in each :class:`Lib`'s ``upstreams``.
    When a library declares more than one specifier against the same upstream
    (across runtime and extras) the specifiers are combined.

    Parameters
    ----------
    libs_dir
        Path to the monorepo's ``libs/`` directory.
    extras
        Optional-dependency extras to fold into each library's resolved
        dependency set, in addition to its runtime dependencies.

    Returns
    -------
        Mapping of library ``name`` (directory name) to its :class:`Lib`.

    Raises
    ------
    FileNotFoundError
        If ``libs_dir`` does not exist.
    ValueError
        If a library's ``pyproject.toml`` has no parseable distribution name,
        or its ``CHANGELOG.rst`` first line has no parseable version.
    """
    if not libs_dir.exists():
        raise FileNotFoundError(f"libs directory does not exist: {libs_dir}")

    library_dirs = sorted(
        p for p in libs_dir.iterdir() if p.is_dir() and (p / "pyproject.toml").exists()
    )

    # Read every library from disk and collect the pyproject, dist name, and pending version for each
    pyprojects: dict[str, dict[str, object]] = {}
    dist_names: dict[str, str] = {}
    versions: dict[str, str] = {}
    for lib_dir in library_dirs:
        name = lib_dir.name
        with (lib_dir / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        pyprojects[name] = pyproject
        dist_names[name] = _get_dist_name(pyproject, lib_dir)
        versions[name] = _get_pending_version(lib_dir / "CHANGELOG.rst")

    monorepo_dists = {canonicalize_name(dist): dist for dist in dist_names.values()}

    # Resolve each library's in-tree dependencies and build the Lib objects
    libs: dict[str, Lib] = {}
    for lib_dir in library_dirs:
        name = lib_dir.name
        dist = dist_names[name]
        upstream_specs = _resolve_upstreams(pyprojects[name], dist, extras, monorepo_dists)
        # Collaps multiple specifiers on the same upstream into a single SpecifierSet
        upstreams = {
            target_dist: reduce(lambda a, b: a & b, specs, SpecifierSet())
            for target_dist, specs in upstream_specs.items()
        }
        libs[name] = Lib(
            name=name,
            dist_name=dist,
            path=lib_dir.resolve(),
            version=versions[name],
            upstreams=upstreams,
        )
    return libs


def _get_dist_name(pyproject: Mapping[str, object], pkg_dir: Path) -> str:
    """Return ``[project].name`` from a parsed pyproject."""
    project = pyproject.get("project")
    name = project.get("name") if isinstance(project, Mapping) else None
    if not isinstance(name, str) or not name:
        raise ValueError(f"no [project].name in {pkg_dir / 'pyproject.toml'}")
    return name


def _get_pending_version(changelog: Path) -> str:
    """Parse the pending version from the first line of a CHANGELOG.rst."""
    if not changelog.exists():
        raise FileNotFoundError(f"changelog not found: {changelog}")
    lines = changelog.read_text().splitlines()
    first_line = lines[0] if lines else ""
    match = re.search(r"\d+\.\d+\.\d+", first_line)
    if match is None:
        raise ValueError(f"no parseable version in first line of {changelog}")
    return match.group(0)


def _resolve_upstreams(
    pyproject: Mapping[str, object],
    own_dist: str,
    extras: Sequence[str],
    monorepo_dists: Mapping[NormalizedName, str],
) -> dict[str, list[SpecifierSet]]:
    """Resolve in-tree upstream specifiers over runtime deps plus ``extras``.

    Self-referential extras (a requirement targeting ``own_dist`` with extras)
    are expanded transitively via a worklist queue; an expanded extra may pull
    in further self-referential extras whereas an in-tree edge to another dist records
    its specifier without expanding that dist's extras.
    """
    project = pyproject.get("project")
    project_map: Mapping[str, object] = project if isinstance(project, Mapping) else {}
    runtime = project_map.get("dependencies", [])
    runtime_reqs: list[str] = list(runtime) if isinstance(runtime, list) else []
    optional = project_map.get("optional-dependencies", {})
    optional_map: Mapping[str, object] = optional if isinstance(optional, Mapping) else {}

    own_canon = canonicalize_name(own_dist)
    upstreams: dict[str, list[SpecifierSet]] = {}
    seen_extras: set[str] = set()
    queue: list[str] = list(runtime_reqs)
    for extra in extras:
        queue.extend(_get_extra_requirements(optional_map, extra))
        seen_extras.add(extra)

    while queue:
        req = Requirement(queue.pop(0))
        req_canon = canonicalize_name(req.name)
        if req_canon == own_canon:
            for extra in req.extras:
                if extra not in seen_extras:
                    seen_extras.add(extra)
                    queue.extend(_get_extra_requirements(optional_map, extra))
            continue
        if req_canon in monorepo_dists:
            upstreams.setdefault(monorepo_dists[req_canon], []).append(req.specifier)

    return upstreams


def _get_extra_requirements(optional_map: Mapping[str, object], extra: str) -> list[str]:
    """Return the raw requirement strings declared for ``extra``."""
    value = optional_map.get(extra, [])
    return [r for r in value if isinstance(r, str)] if isinstance(value, list) else []
