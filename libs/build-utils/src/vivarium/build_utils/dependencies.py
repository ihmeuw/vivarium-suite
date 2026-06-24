"""In-tree dependency graph for the vivarium-suite monorepo.

This module is the single source of truth for the dependency relationships
*between* the packages under ``libs/``. It powers two cross-package CI flows
that let a single PR (or merge) span interdependent packages without an interim
release:

1. **Editable-sibling install** (consumed by ``make install`` via the
   ``editable-install`` CLI subcommand). When a PR modifies several packages -
   for example bumping ``vivarium-engine`` and consuming the new version from
   ``vivarium-public-health`` - the dependent's declared dependency would
   normally resolve the upstream from PyPI, where the new version does not yet
   exist. :func:`editable_siblings` selects exactly the siblings that are
   modified in the PR, reachable from the package under test, and version
   compatible, and :func:`build_install_plan` installs them editably (at their
   pending versions) alongside the target in a single ``uv`` invocation.

2. **Ordered release matrix** (consumed by the release workflow via the
   ``release-matrix`` CLI subcommand). When a merge to ``main`` bumps several
   packages, :func:`release_matrix` emits a GitHub Actions matrix ordered
   dependencies-first, where each entry carries the in-batch upstreams it must
   wait for on PyPI before installing - so independent packages release in
   parallel while dependents serialize only along real dependency edges.

Run as ``python -m vivarium.build_utils.dependencies <subcommand>``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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


class DependencyConflictError(Exception):
    """A selected in-tree sibling's pending version violates a declared pin.

    Raised by :func:`editable_siblings` when a sibling that would be installed
    editably has a pending version that does not satisfy some reachable
    package's version constraint on it - typically a forgotten pin bump in the
    dependent. The message names the sibling, its pending version, and the
    conflicting package and specifier.
    """


class DependencyCycleError(Exception):
    """The in-tree dependency graph contains a cycle and cannot be ordered."""


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
        Mapping of in-tree ``dist_name`` to the combined version specifier this
        package declares against it, over runtime dependencies plus the extras
        :func:`load_libs` was asked to resolve. Only dependencies that resolve
        to another ``libs/`` package appear here; external dependencies
        (``numpy``, ``dill``, ...) are omitted.
    """

    name: str
    dist_name: str
    path: Path
    version: str
    sibling_deps: Mapping[str, SpecifierSet]


@dataclass(frozen=True)
class InstallPlan:
    """A fully-composed ``uv pip install`` invocation.

    Attributes
    ----------
    argv
        The argument vector to execute (e.g. ``["uv", "pip", "install", "-e",
        ...]``).
    env
        Environment overrides to apply on top of the current environment when
        executing ``argv`` - notably the per-sibling
        ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` entries that make each
        editable sibling present its pending release version.
    """

    argv: Sequence[str]
    env: Mapping[str, str]


def load_libs(libs_dir: Path, extras: Sequence[str] = DEFAULT_EXTRAS) -> dict[str, Lib]:
    """Parse every package under ``libs_dir`` into a :class:`Lib`.

    For each ``libs/<pkg>`` directory, reads the distribution name and the
    declared dependencies from ``pyproject.toml`` and the pending version from
    the first line of ``CHANGELOG.rst``. Dependencies are resolved over
    ``[project].dependencies`` plus the requested ``extras``, recursively
    expanding self-referential extras (e.g. ``ci_github = ["vivarium-foo[test,
    docs]"]`` pulls in the requirements of ``foo``'s ``test`` and ``docs``
    extras). Only requirements whose distribution name matches another package
    under ``libs_dir`` are retained in each :class:`Lib`'s ``sibling_deps``;
    when a package declares more than one specifier against the same sibling
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

    pyprojects: dict[str, dict[str, object]] = {}
    dist_names: dict[str, str] = {}
    versions: dict[str, str] = {}
    for pkg_dir in pkg_dirs:
        name = pkg_dir.name
        pyproject = _load_pyproject(pkg_dir / "pyproject.toml")
        pyprojects[name] = pyproject
        dist_names[name] = _read_dist_name(pyproject, pkg_dir)
        versions[name] = _read_pending_version(pkg_dir / "CHANGELOG.rst")

    in_tree = {canonicalize_name(dist): dist for dist in dist_names.values()}

    libs: dict[str, Lib] = {}
    for pkg_dir in pkg_dirs:
        name = pkg_dir.name
        dist = dist_names[name]
        sibling_specs = _resolve_sibling_deps(pyprojects[name], dist, extras, in_tree)
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


def _load_pyproject(path: Path) -> dict[str, object]:
    """Parse a ``pyproject.toml`` file."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _read_dist_name(pyproject: Mapping[str, object], pkg_dir: Path) -> str:
    """Return ``[project].name`` from a parsed pyproject."""
    project = pyproject.get("project")
    name = project.get("name") if isinstance(project, Mapping) else None
    if not isinstance(name, str) or not name:
        raise ValueError(f"no [project].name in {pkg_dir / 'pyproject.toml'}")
    return name


def _read_pending_version(changelog: Path) -> str:
    """Parse the pending version from the first line of a CHANGELOG.rst."""
    if not changelog.exists():
        raise FileNotFoundError(f"changelog not found: {changelog}")
    lines = changelog.read_text().splitlines()
    first_line = lines[0] if lines else ""
    match = re.search(r"\d+\.\d+\.\d+", first_line)
    if match is None:
        raise ValueError(f"no parseable version in first line of {changelog}")
    return match.group(0)


def _dist_to_name(libs: Mapping[str, Lib]) -> dict[str, str]:
    """Map each in-tree ``dist_name`` to its package ``name``."""
    return {lib.dist_name: name for name, lib in libs.items()}


def _in_scope_upstreams(name: str, libs: Mapping[str, Lib], scope: set[str]) -> set[str]:
    """Return ``name``'s direct in-tree upstream names that are in ``scope``."""
    dist_to_name = _dist_to_name(libs)
    upstreams: set[str] = set()
    for dep_dist in libs[name].sibling_deps:
        dep_name = dist_to_name.get(dep_dist)
        if dep_name in scope and dep_name != name:
            upstreams.add(dep_name)
    return upstreams


def _resolve_sibling_deps(
    pyproject: Mapping[str, object],
    own_dist: str,
    extras: Sequence[str],
    in_tree: Mapping[str, str],
) -> dict[str, list[SpecifierSet]]:
    """Resolve in-tree sibling specifiers over runtime deps plus ``extras``.

    Self-referential extras (a requirement targeting ``own_dist`` with extras)
    are recursively expanded; an in-tree edge to another dist records its
    specifier without expanding that dist's extras.
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
        queue.extend(_extra_requirements(optional_map, extra))
        seen_extras.add(extra)

    while queue:
        req = Requirement(queue.pop(0))
        req_canon = canonicalize_name(req.name)
        if req_canon == own_canon:
            for extra in req.extras:
                if extra not in seen_extras:
                    seen_extras.add(extra)
                    queue.extend(_extra_requirements(optional_map, extra))
            continue
        if req_canon in in_tree:
            siblings.setdefault(in_tree[req_canon], []).append(req.specifier)

    return siblings


def _extra_requirements(optional_map: Mapping[str, object], extra: str) -> list[str]:
    """Return the raw requirement strings declared for ``extra``."""
    value = optional_map.get(extra, [])
    return [r for r in value if isinstance(r, str)] if isinstance(value, list) else []


def reachable_siblings(target: str, libs: Mapping[str, Lib]) -> set[str]:
    """Return all in-tree package names transitively reachable from ``target``.

    Walks ``target``'s ``sibling_deps`` and those of every package it reaches,
    following only in-tree edges. The result excludes ``target`` itself.

    Parameters
    ----------
    target
        Package ``name`` to compute reachability from.
    libs
        The full set of parsed packages, as returned by :func:`load_libs`.

    Returns
    -------
        Names of the in-tree packages reachable from ``target``.

    Raises
    ------
    KeyError
        If ``target`` is not a key in ``libs``.
    """
    if target not in libs:
        raise KeyError(target)
    dist_to_name = _dist_to_name(libs)

    reached: set[str] = set()
    stack = [target]
    while stack:
        current = stack.pop()
        for dep_dist in libs[current].sibling_deps:
            dep_name = dist_to_name.get(dep_dist)
            if dep_name is not None and dep_name not in reached:
                reached.add(dep_name)
                stack.append(dep_name)
    reached.discard(target)
    return reached


def editable_siblings(
    target: str, libs: Mapping[str, Lib], changed: Sequence[str]
) -> list[Lib]:
    """Select the siblings to install editably for a build of ``target``.

    Returns the packages in ``changed`` that are reachable from ``target``,
    ordered dependencies-first. Packages in ``changed`` that are not reachable
    from ``target`` are ignored (a change elsewhere in the monorepo does not
    affect this build). Before returning, validates that each selected
    sibling's pending version satisfies every reachable package's declared
    constraint on it.

    Parameters
    ----------
    target
        Package ``name`` being built/tested.
    libs
        The full set of parsed packages, as returned by :func:`load_libs`.
    changed
        Package ``name``s whose own source changed in the PR - the only
        packages eligible for in-tree resolution.

    Returns
    -------
        The selected siblings as :class:`Lib`s, ordered so a sibling appears
        after every other selected sibling it depends on.

    Raises
    ------
    DependencyConflictError
        If a selected sibling's pending version does not satisfy some reachable
        package's version constraint on it.
    KeyError
        If ``target`` or any entry of ``changed`` is not a key in ``libs``.
    """
    for name in (target, *changed):
        if name not in libs:
            raise KeyError(name)

    reachable = reachable_siblings(target, libs)
    selected = [name for name in changed if name in reachable]

    constrainers = reachable | {target}
    for sibling in selected:
        sibling_dist = libs[sibling].dist_name
        sibling_version = libs[sibling].version
        for package in constrainers:
            specifier = libs[package].sibling_deps.get(sibling_dist)
            if specifier is None:
                continue
            if not specifier.contains(sibling_version, prereleases=True):
                raise DependencyConflictError(
                    f"in-tree sibling {sibling_dist} pending version "
                    f"{sibling_version} does not satisfy specifier "
                    f"'{specifier}' declared by {libs[package].dist_name}"
                )

    ordered = release_order(selected, libs)
    return [libs[name] for name in ordered]


def release_order(names: Sequence[str], libs: Mapping[str, Lib]) -> list[str]:
    """Topologically sort ``names`` dependencies-first.

    Orders only the packages in ``names`` relative to one another, using the
    in-tree edges among them; packages outside ``names`` are ignored. Among
    packages with no dependency relationship the input order is preserved.

    Parameters
    ----------
    names
        Package ``name``s to order.
    libs
        The full set of parsed packages, as returned by :func:`load_libs`.

    Returns
    -------
        ``names`` reordered so each package follows every package in ``names``
        it depends on.

    Raises
    ------
    DependencyCycleError
        If the packages in ``names`` form a dependency cycle.
    """
    name_set = list(dict.fromkeys(names))
    in_scope = set(name_set)

    deps: dict[str, set[str]] = {
        name: _in_scope_upstreams(name, libs, in_scope) for name in name_set
    }

    ordered: list[str] = []
    placed: set[str] = set()
    while len(ordered) < len(name_set):
        progressed = False
        for name in name_set:
            if name in placed:
                continue
            if deps[name] <= placed:
                ordered.append(name)
                placed.add(name)
                progressed = True
        if not progressed:
            remaining = [n for n in name_set if n not in placed]
            raise DependencyCycleError(
                f"dependency cycle among packages: {sorted(remaining)}"
            )
    return ordered


def release_matrix(pairs: Mapping[str, str], libs: Mapping[str, Lib]) -> dict[str, object]:
    """Build the dependency-ordered GitHub Actions release matrix.

    Given the packages to release and their versions, returns a matrix object
    suitable for ``strategy.matrix`` in the release workflow. The ``include``
    entries are ordered dependencies-first (see :func:`release_order`), and
    each entry carries the in-batch upstreams it must wait for on PyPI before
    it can install - i.e. the packages it depends on that are *also* part of
    this release batch. Upstreams that are not in the batch are already
    released and so are omitted from ``wait_for``.

    Parameters
    ----------
    pairs
        Mapping of package ``name`` to the version being released.
    libs
        The full set of parsed packages, as returned by :func:`load_libs`.

    Returns
    -------
        ``{"include": [{"library": name, "version": version, "wait_for":
        [{"dist": dist_name, "version": version}, ...]}, ...]}``. ``include``
        is empty when ``pairs`` is empty.

    Raises
    ------
    DependencyCycleError
        If the release batch forms a dependency cycle.
    KeyError
        If a key of ``pairs`` is not a key in ``libs``.
    """
    for name in pairs:
        if name not in libs:
            raise KeyError(name)

    batch = set(pairs)
    ordered = release_order(list(pairs), libs)

    include: list[dict[str, object]] = []
    for name in ordered:
        upstreams = release_order(sorted(reachable_siblings(name, libs) & batch), libs)
        wait_for = [
            {"dist": libs[upstream].dist_name, "version": pairs[upstream]}
            for upstream in upstreams
        ]
        include.append(
            {
                "library": name,
                "version": pairs[name],
                "wait_for": wait_for,
            }
        )
    return {"include": include}


def build_install_plan(
    target_lib: Lib,
    siblings: Sequence[Lib],
    *,
    env_reqs: str,
    ihme_pypi: str,
    uv_flags: str,
) -> InstallPlan:
    """Compose the single ``uv pip install`` invocation for a cross-package build.

    Builds one command that installs ``target_lib`` editably with its
    ``env_reqs`` extra and each sibling editably, by absolute path, so ``uv``
    resolves the named in-tree distributions from local source rather than
    PyPI. Each editable package gets the ``editable_mode=compat`` config setting
    (keyed by its ``dist_name``, matching ``make install``'s classic-``.pth``
    editable mode), and the extra-index flags are included only when
    ``ihme_pypi`` is non-empty. The returned plan's ``env`` carries a
    ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` entry for each sibling so its
    editable install reports its pending release version (a feature branch has
    no release tag, so ``setuptools_scm`` would otherwise derive a dev version
    that fails a bumped pin). The target needs no pretend version - nothing in
    the build depends on it.

    Parameters
    ----------
    target_lib
        The package being built; installed editably (by absolute path) with
        its ``[env_reqs]`` extra.
    siblings
        Siblings to install editably, dependency-ordered (as returned by
        :func:`editable_siblings`).
    env_reqs
        The extra to install on the target (e.g. ``"ci_github"``); when empty,
        the target is installed with no extra.
    ihme_pypi
        IHME artifactory base URL, or empty to disable the extra index (as on
        firewalled GitHub-hosted runners).
    uv_flags
        Extra flags to pass through to ``uv pip install`` (e.g. ``"--system"``).

    Returns
    -------
        The composed :class:`InstallPlan`.
    """
    target_spec = f"{target_lib.path}[{env_reqs}]" if env_reqs else str(target_lib.path)
    argv: list[str] = ["uv", "pip", "install", "-e", target_spec]
    config_settings: list[str] = [
        "--config-settings-package",
        f"{target_lib.dist_name}:editable_mode=compat",
    ]

    env: dict[str, str] = {}
    for sibling in siblings:
        argv.extend(["-e", str(sibling.path)])
        config_settings.extend(
            [
                "--config-settings-package",
                f"{sibling.dist_name}:editable_mode=compat",
            ]
        )
        dist_upper = sibling.dist_name.upper().replace("-", "_")
        env[f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{dist_upper}"] = sibling.version

    argv.extend(config_settings)

    if ihme_pypi:
        argv.extend(
            [
                "--extra-index-url",
                f"{ihme_pypi}simple/",
                "--index-strategy",
                "unsafe-best-match",
            ]
        )

    if uv_flags.strip():
        argv.extend(uv_flags.split())

    return InstallPlan(argv=argv, env=env)


def run_install(plan: InstallPlan, libs_dir: Path) -> None:
    """Execute an :class:`InstallPlan`.

    Runs ``plan.argv`` with ``plan.env`` overlaid on the current environment,
    from ``libs_dir`` as the working directory so the editable source paths
    resolve.

    Parameters
    ----------
    plan
        The plan to execute.
    libs_dir
        The monorepo's ``libs/`` directory, used as the working directory.

    Raises
    ------
    subprocess.CalledProcessError
        If the install command exits non-zero.
    """
    subprocess.run(
        list(plan.argv),
        cwd=libs_dir,
        env={**os.environ, **plan.env},
        check=True,
    )


def _discover_libs_dir(explicit: str | None) -> Path:
    """Locate the monorepo ``libs/`` directory.

    Uses ``explicit`` if given. Otherwise walks up from the cwd looking for a
    ``libs/`` directory containing a ``build-utils`` package; failing that,
    treats the cwd as the libs dir if it directly contains ``build-utils``, or
    returns ``<cwd>/libs``.
    """
    if explicit:
        return Path(explicit).resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        libs = candidate / "libs"
        if (libs / "build-utils").is_dir():
            return libs
    if (cwd / "build-utils").is_dir():
        return cwd
    return cwd / "libs"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Subcommands:

    ``editable-install <target> --changed "<names>" --env-reqs <extra>
    --ihme-pypi <url> --uv-flags <flags> [--libs-dir <path>]``
        Select editable siblings for ``target`` and run the combined editable
        install. Used by ``make install`` when ``IN_TREE_SIBLINGS`` is set.

    ``release-matrix [--pairs <file>] [--libs-dir <path>]``
        Read ``"<name> <version>"`` lines (from ``--pairs`` or stdin) and print
        the dependency-ordered release matrix JSON to stdout. Used by the
        release workflow's detect job.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
        Process exit code: 0 on success, non-zero on any handled error - a
        dependency conflict, a dependency cycle, a missing version, or an
        unknown package name.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-deps")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("editable-install")
    install_parser.add_argument("target")
    install_parser.add_argument("--changed", default="")
    install_parser.add_argument("--env-reqs", default="")
    install_parser.add_argument("--ihme-pypi", default="")
    install_parser.add_argument("--uv-flags", default="")
    install_parser.add_argument("--libs-dir", default=None)

    matrix_parser = subparsers.add_parser("release-matrix")
    matrix_parser.add_argument("--pairs", default=None)
    matrix_parser.add_argument("--libs-dir", default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "editable-install":
        return _run_editable_install(args)
    return _run_release_matrix(args)


def _run_editable_install(args: argparse.Namespace) -> int:
    """Handle the ``editable-install`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    changed = args.changed.split()
    try:
        siblings = editable_siblings(args.target, libs, changed)
    except (DependencyConflictError, DependencyCycleError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"unknown package: {error.args[0]}", file=sys.stderr)
        return 1

    plan = build_install_plan(
        libs[args.target],
        siblings,
        env_reqs=args.env_reqs,
        ihme_pypi=args.ihme_pypi,
        uv_flags=args.uv_flags,
    )
    run_install(plan, libs_dir)
    return 0


def _run_release_matrix(args: argparse.Namespace) -> int:
    """Handle the ``release-matrix`` subcommand."""
    if args.pairs:
        raw = Path(args.pairs).read_text()
    else:
        raw = sys.stdin.read()

    pairs: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            print(f"missing version for line: {line!r}", file=sys.stderr)
            return 1
        pairs[parts[0]] = parts[1]

    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    try:
        matrix = release_matrix(pairs, libs)
    except DependencyCycleError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"unknown package: {error.args[0]}", file=sys.stderr)
        return 1
    print(json.dumps(matrix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
