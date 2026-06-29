"""Parse the ``libs/`` tree into the in-tree package model.

This module owns everything that reads the filesystem - each package's
``pyproject.toml`` (distribution name and declared dependencies, runtime plus a
CI extra, with self-referential extras expanded) and its ``CHANGELOG.rst``
(pending version) - and turns it into the :class:`Lib` records that the
dependency-graph queries in :mod:`vivarium.build_utils._graph` and the
cross-package flows in :mod:`vivarium.build_utils.dependencies` operate on.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

# The pyproject extra whose dependency closure CI activates (``make install
# ENV_REQS=ci_github`` in both the GitHub Actions test matrix and the release
# job). The dependency graph is resolved over runtime dependencies plus this
# extra so the editable-sibling and release-ordering decisions reflect the
# dependency set the install actually pulls in.
DEFAULT_EXTRAS: tuple[str, ...] = ("ci_github",)


@dataclass(frozen=True)
class Lib:
    """A single independently-released package under ``libs/``.

    Attributes
    ----------
    name
        Directory name under ``libs/`` (e.g. ``"engine"``).
    dist_name
        PyPI distribution name from ``pyproject.toml`` ``[project].name``
        (e.g. ``"vivarium-engine"``).
    path
        Absolute path to the ``libs/<name>`` directory.
    version
        Pending release version, parsed from the first line of
        ``CHANGELOG.rst`` (format ``**X.Y.Z - MM/DD/YY**``).
    sibling_deps
        This package's dependencies on *other monorepo packages*: a mapping from
        each depended-on sibling's ``dist_name`` to the version constraint this
        package places on it. For example, ``vivarium-public-health`` yields
        ``{"vivarium-engine": SpecifierSet(">=5.1.1"), "vivarium-config-tree":
        SpecifierSet(">=5.0.0"), ...}``. External dependencies (``numpy``,
        ``dill``, ...) are excluded - only ``libs/`` packages appear. Collected
        over the runtime dependencies plus whichever extras :func:`load_libs`
        resolved; if a sibling is constrained in more than one of those places,
        the constraints are intersected into a single :class:`SpecifierSet`.
    """

    name: str
    dist_name: str
    path: Path
    version: str
    sibling_deps: Mapping[str, SpecifierSet]


def load_libs(libs_dir: Path, extras: Sequence[str] = DEFAULT_EXTRAS) -> dict[str, Lib]:
    """Parse every package under ``libs_dir`` into a :class:`Lib`.

    For each ``libs/<pkg>`` directory, reads the distribution name and the
    declared dependencies from ``pyproject.toml`` and the pending version from
    the first line of ``CHANGELOG.rst``. Dependencies are resolved over
    ``[project].dependencies`` plus the requested ``extras``, transitively
    expanding self-referential extras (e.g. ``ci_github = ["vivarium-foo[test,
    docs]"]`` pulls in the requirements of ``foo``'s ``test`` and ``docs``
    extras). Only requirements whose distribution name matches another package
    under ``libs_dir`` are retained in each :class:`Lib`'s ``sibling_deps``.
    When a package declares more than one specifier against the same sibling
    (across runtime and extras) the specifiers are combined.

    Parameters
    ----------
    libs_dir
        Path to the monorepo's ``libs/`` directory.
    extras
        Optional-dependency extras to fold into each package's resolved
        dependency set, in addition to its runtime dependencies.

    Returns
    -------
        Mapping of package ``name`` (directory name) to its :class:`Lib`.

    Raises
    ------
    FileNotFoundError
        If ``libs_dir`` does not exist.
    ValueError
        If a package's ``pyproject.toml`` has no parseable distribution name,
        or its ``CHANGELOG.rst`` first line has no parseable version.
    """
    if not libs_dir.exists():
        raise FileNotFoundError(f"libs directory does not exist: {libs_dir}")

    pkg_dirs = sorted(
        p for p in libs_dir.iterdir() if p.is_dir() and (p / "pyproject.toml").exists()
    )

    # Read every package from disk and collect the pyproject, dist name, and pending version for each
    pyprojects: dict[str, dict[str, object]] = {}
    dist_names: dict[str, str] = {}
    versions: dict[str, str] = {}
    for pkg_dir in pkg_dirs:
        name = pkg_dir.name
        with (pkg_dir / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        pyprojects[name] = pyproject
        dist_names[name] = _get_dist_name(pyproject, pkg_dir)
        versions[name] = _get_pending_version(pkg_dir / "CHANGELOG.rst")

    in_tree = {canonicalize_name(dist): dist for dist in dist_names.values()}

    # Resolve each package's in-tree dependencies and build the Lib objects
    libs: dict[str, Lib] = {}
    for pkg_dir in pkg_dirs:
        name = pkg_dir.name
        dist = dist_names[name]
        sibling_specs = _resolve_sibling_deps(pyprojects[name], dist, extras, in_tree)
        # Collaps multiple specifiers on the same sibling into a single SpecifierSet
        sibling_deps = {
            target_dist: reduce(lambda a, b: a & b, specs, SpecifierSet())
            for target_dist, specs in sibling_specs.items()
        }
        libs[name] = Lib(
            name=name,
            dist_name=dist,
            path=pkg_dir.resolve(),
            version=versions[name],
            sibling_deps=sibling_deps,
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


def _resolve_sibling_deps(
    pyproject: Mapping[str, object],
    own_dist: str,
    extras: Sequence[str],
    in_tree: Mapping[str, str],
) -> dict[str, list[SpecifierSet]]:
    """Resolve in-tree sibling specifiers over runtime deps plus ``extras``.

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
    siblings: dict[str, list[SpecifierSet]] = {}
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
        if req_canon in in_tree:
            siblings.setdefault(in_tree[req_canon], []).append(req.specifier)

    return siblings


def _get_extra_requirements(optional_map: Mapping[str, object], extra: str) -> list[str]:
    """Return the raw requirement strings declared for ``extra``."""
    value = optional_map.get(extra, [])
    return [r for r in value if isinstance(r, str)] if isinstance(value, list) else []


def _discover_libs_dir(libs_path: str | None) -> Path:
    """Locate the monorepo ``libs/`` directory.

    Uses ``libs_path`` if given. Otherwise walks up from the cwd looking for a
    ``libs/`` directory containing a ``build-utils`` package; failing that,
    treats the cwd as the libs dir if it directly contains ``build-utils``, or
    returns ``<cwd>/libs``.
    """
    if libs_path:
        return Path(libs_path).resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        libs = candidate / "libs"
        # Be sure that this is the monorepo's libs dir, not some other directory
        # named "libs" that happens to be in the cwd's ancestry
        if (libs / "build-utils").is_dir():
            return libs
    return cwd / "libs"
