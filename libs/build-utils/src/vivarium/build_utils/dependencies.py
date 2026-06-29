"""Cross-package CI flows for the vivarium-suite monorepo.

This module wires together the in-tree package model
(:mod:`vivarium.build_utils._parsing`) and the dependency-graph queries
(:mod:`vivarium.build_utils._graph`) into the two cross-package flows that let a
single PR (or merge) span interdependent packages without an interim release:

1. **Editable-sibling install** (consumed by ``make install`` via the
   ``editable-install`` CLI subcommand). When a PR modifies several packages -
   for example, bumping ``vivarium-engine`` and consuming the new version from
   ``vivarium-public-health`` - the dependent's declared dependency would
   normally resolve the upstream from PyPI, where the new version does not yet
   exist. :func:`get_ordered_editable_siblings` selects exactly the siblings that
   are modified in the PR, reachable from the package under test, and version
   compatible, and :func:`build_install_plan` installs them editably (at their
   pending versions) alongside the target in a single ``uv`` invocation.

2. **Ordered release matrix** (consumed by the release workflow via the
   ``release-matrix`` CLI subcommand). When a merge to ``main`` bumps several
   packages, :func:`get_release_matrix` emits a GitHub Actions matrix ordered
   dependencies-first, where each entry carries the in-batch upstreams it must
   wait for on PyPI before installing; independent packages release in parallel
   while dependents serialize along real dependency edges.

The in-tree graph itself (parsing, reachability, topological ordering) lives in
the ``_parsing`` and ``_graph`` modules; the names they own are re-exported here
so this module remains the single public surface and CLI entry point. Run as
``python -m vivarium.build_utils.dependencies <subcommand>``.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from vivarium.build_utils._graph import (
    DependencyCycleError,
    get_reachable_siblings,
    get_release_order,
)
from vivarium.build_utils._parsing import DEFAULT_EXTRAS, Lib, _discover_libs_dir, load_libs

# Re-exported so ``vivarium.build_utils.dependencies`` stays the public surface
# (and the CLI entry point) even though parsing and graph queries now live in
# sibling modules.
__all__ = [
    "DEFAULT_EXTRAS",
    "DependencyConflictError",
    "DependencyCycleError",
    "InstallPlan",
    "Lib",
    "build_install_plan",
    "get_ordered_editable_siblings",
    "get_reachable_siblings",
    "get_release_matrix",
    "get_release_order",
    "load_libs",
    "main",
    "run_install",
]


class DependencyConflictError(Exception):
    """A selected in-tree sibling's pending version violates a declared pin."""


@dataclass(frozen=True)
class InstallPlan:
    """A fully-composed ``uv pip install`` invocation.

    Attributes
    ----------
    argv
        The argument vector to execute (e.g. ``["uv", "pip", "install", "-e", ...]``).
    env
        Environment overrides to apply on top of the current environment when
        executing ``argv`` (notably the per-sibling ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>``
        entries that make each editable sibling present its pending release version).
    """

    argv: Sequence[str]
    env: Mapping[str, str]


def get_ordered_editable_siblings(
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
        The full set of parsed packages.
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

    reachable_siblings = get_reachable_siblings(target, libs)
    editable_sibling_names = [name for name in changed if name in reachable_siblings]

    constrainers = reachable_siblings | {target}
    for sibling in editable_sibling_names:
        sibling_dist = libs[sibling].dist_name
        sibling_version = libs[sibling].version  # pending release version
        for package in constrainers:
            specifier = libs[package].sibling_deps.get(sibling_dist)
            if specifier is None:
                # No declared constraint on this sibling from this package, so nothing to check
                continue
            if not specifier.contains(sibling_version, prereleases=True):
                raise DependencyConflictError(
                    f"in-tree sibling {sibling_dist} pending version "
                    f"{sibling_version} does not satisfy specifier "
                    f"'{specifier}' declared by {libs[package].dist_name}"
                )

    ordered = get_release_order(editable_sibling_names, libs)
    return [libs[name] for name in ordered]


def get_release_matrix(
    release_versions: Mapping[str, str], libs: Mapping[str, Lib]
) -> dict[str, object]:
    """Build the dependency-ordered GitHub Actions release matrix.

    Given the packages to release and their versions, returns a matrix object
    suitable for ``strategy.matrix`` in the release workflow. The ``include``
    entries are ordered dependencies-first (see :func:`get_release_order`), and
    each entry carries the in-batch upstreams it must wait for on PyPI before
    it can install - i.e. the packages it depends on that are *also* part of
    this release batch. Upstreams that are not in the batch are already
    released and so are omitted from ``wait_for``.

    Parameters
    ----------
    release_versions
        Mapping of package ``name`` to the version being released.
    libs
        The full set of parsed packages.

    Returns
    -------
        A dictionary suitable for ``strategy.matrix`` in the release workflow:
        ``{"include": [{"library": name, "version": version, "wait_for":
        [{"dist": dist_name, "version": version}, ...]}, ...]}``. ``include``
        is empty when ``release_versions`` is empty.

    Raises
    ------
    DependencyCycleError
        If the release batch forms a dependency cycle.
    KeyError
        If a key of ``release_versions`` is not a key in ``libs``.
    """
    for name in release_versions:
        if name not in libs:
            raise KeyError(name)

    batch = set(release_versions)
    ordered = get_release_order(list(release_versions), libs)

    include: list[dict[str, object]] = []
    for name in ordered:
        upstreams = get_release_order(
            sorted(get_reachable_siblings(name, libs) & batch), libs
        )
        wait_for = [
            {"dist": libs[upstream].dist_name, "version": release_versions[upstream]}
            for upstream in upstreams
        ]
        include.append(
            {
                "library": name,
                "version": release_versions[name],
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
        The package being built.
    siblings
        Siblings to install editably, dependency-ordered.
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
    from ``libs_dir`` as the working directory so the editable source paths resolve.

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


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Subcommands:

    ``editable-install <target> --changed "<names>" --env-reqs <extra>
    --ihme-pypi <url> --uv-flags <flags> [--libs-dir <path>]``
        Select editable siblings for ``target`` and run the combined editable
        install. Used by ``make install`` when ``IN_TREE_SIBLINGS`` is set.

    ``release-matrix --versions <file> [--libs-dir <path>]``
        Read ``"<name> <version>"`` lines from the ``--versions`` file and print
        the dependency-ordered release matrix JSON to stdout. Used by the
        release workflow's detect job.

    ``verify-editable <target> --changed "<names>" [--libs-dir <path>]``
        Recompute the editable siblings selected for ``target`` (the same
        selection ``editable-install`` uses) and assert each one is installed
        editably, not silently resolved from PyPI. Exits non-zero if any is
        not. Used by the CI workflow after ``make install``.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
        Process exit code: 0 on success, non-zero on any handled error, i.e. a dependency
        conflict, a dependency cycle, a missing version, or an unknown package name.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-deps")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Editable install subcommand
    install_parser = subparsers.add_parser("editable-install")
    install_parser.add_argument("target")
    install_parser.add_argument("--changed", default="")
    install_parser.add_argument("--env-reqs", default="")
    install_parser.add_argument("--ihme-pypi", default="")
    install_parser.add_argument("--uv-flags", default="")
    install_parser.add_argument("--libs-dir", default=None)

    # Release matrix subcommand
    matrix_parser = subparsers.add_parser("release-matrix")
    matrix_parser.add_argument("--versions", required=True)
    matrix_parser.add_argument("--libs-dir", default=None)

    # Verify-editable subcommand
    verify_parser = subparsers.add_parser("verify-editable")
    verify_parser.add_argument("target")
    verify_parser.add_argument("--changed", default="")
    verify_parser.add_argument("--libs-dir", default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "editable-install":
        return _run_editable_install(args)
    if args.command == "verify-editable":
        return _run_verify_editable(args)
    return _run_release_matrix(args)


def _run_editable_install(args: argparse.Namespace) -> int:
    """Handle the ``editable-install`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    changed = args.changed.split()
    try:
        siblings = get_ordered_editable_siblings(args.target, libs, changed)
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


def _is_editable_install(dist_name: str) -> bool:
    """Return whether the installed distribution ``dist_name`` is an editable install.

    Reads the PEP 610 ``direct_url.json`` metadata pip/uv records for an
    installed distribution; ``dir_info.editable`` is true only for an editable
    (``-e``) install from a local directory.
    """
    direct_url = importlib.metadata.distribution(dist_name).read_text("direct_url.json")
    if not direct_url:
        return False
    return bool(json.loads(direct_url).get("dir_info", {}).get("editable"))


def _run_verify_editable(args: argparse.Namespace) -> int:
    """Handle the ``verify-editable`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    changed = args.changed.split()
    expected = get_ordered_editable_siblings(args.target, libs, changed)
    if not expected:
        print(f"{args.target}: no changed in-tree siblings to verify")
        return 0

    failed = False
    for sibling in expected:
        editable = _is_editable_install(sibling.dist_name)
        if not editable:
            print(
                f"::error::{sibling.dist_name} is not editable - it resolved "
                "from PyPI, not in-tree source",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


def _run_release_matrix(args: argparse.Namespace) -> int:
    """Handle the ``release-matrix`` subcommand."""
    raw = Path(args.versions).read_text()

    release_versions: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            print(f"missing version for line: {line!r}", file=sys.stderr)
            return 1
        release_versions[parts[0]] = parts[1]
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    try:
        matrix = get_release_matrix(release_versions, libs)
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
