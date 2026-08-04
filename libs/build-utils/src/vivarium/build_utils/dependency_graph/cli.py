"""Command-line interface for ``vivarium.build_utils.dependency_graph``.

Exposes the install-editable, classify-changes, build-release-matrix,
build-downstream-matrix, verify-editable, and check-acyclic subcommands consumed by
``make install`` and the CI/release workflows.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .changes import build_python_matrix, classify_changed_libs
from .editable import build_install_plan, get_editable_upstreams, run_install
from .graph import get_transitive_downstreams, sort_topologically
from .loading import load_libs
from .models import DependencyConflictError, DependencyCycleError, MissingPythonVersionsError
from .release import get_release_matrix


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Subcommands:

    ``install-editable <target> --changed "<names>" --env-reqs <extra>
        --ihme-pypi <url> --uv-flags <flags> [--libs-dir <path>]``
        Determine the editable upstreams of ``target`` and run the combined editable
        install. Used by ``make install`` when ``CHANGED_LIBS`` is set.

    ``classify-changes --changed-files-file <file> [--libs-dir <path>]``
        Read repository-relative changed paths (one per line) from
        ``--changed-files-file`` and print the JSON classification of which
        libraries the diff touched, plus the GitHub Actions matrix to build. Used by
        the CI and Downstream Check workflows' detect jobs.

    ``build-release-matrix --versions <file> [--libs-dir <path>]``
        Read ``"<name> <version>"`` lines from the ``--versions`` file and print
        the dependency-ordered release matrix JSON to stdout. Used by the
        release workflow's detect job.

    ``build-downstream-matrix --released "<names>" [--libs-dir <path>]``
        Print the GitHub Actions matrix JSON of the libraries downstream of the
        released ``<names>`` (their transitive dependents, excluding the released
        set), one entry per dependent per Python version in its
        ``python_versions.json``. Used by the Downstream Check workflow to test
        dependents against the releasing libs' pending versions.

    ``verify-editable <target> --changed "<names>" [--libs-dir <path>]``
        Recompute the editable upstreams selected of ``target`` and assert each
        one is installed editably (not resolved from PyPI). Used by the CI workflow
        after ``make install``.

    ``check-acyclic [--libs-dir <path>]``
        Validate that the whole in-tree dependency graph is acyclic. Used by the
        CI workflow as a pre-merge guard.

    Parameters
    ----------
    argv
        Argument list (defaults to ``sys.argv[1:]``).

    Returns
    -------
        Process exit code: 0 on success, non-zero on any handled error, i.e. a dependency
        conflict, a dependency cycle, a missing version, or an unknown library name.
    """
    parser = argparse.ArgumentParser(prog="vivarium-build-utils-dependency-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # install-editable
    install_parser = subparsers.add_parser("install-editable")
    install_parser.add_argument("target")
    install_parser.add_argument("--changed", default="")
    install_parser.add_argument("--env-reqs", default="")
    install_parser.add_argument("--ihme-pypi", default="")
    install_parser.add_argument("--uv-flags", default="")
    install_parser.add_argument("--libs-dir", default=None)

    # classify-changes
    classify_parser = subparsers.add_parser("classify-changes")
    classify_parser.add_argument("--changed-files-file", required=True)
    classify_parser.add_argument("--libs-dir", default=None)

    # build-release-matrix
    matrix_parser = subparsers.add_parser("build-release-matrix")
    matrix_parser.add_argument("--versions", required=True)
    matrix_parser.add_argument("--libs-dir", default=None)

    # build-downstream-matrix
    downstream_parser = subparsers.add_parser("build-downstream-matrix")
    downstream_parser.add_argument("--released", default="")
    downstream_parser.add_argument("--libs-dir", default=None)

    # verify-editable
    verify_parser = subparsers.add_parser("verify-editable")
    verify_parser.add_argument("target")
    verify_parser.add_argument("--changed", default="")
    verify_parser.add_argument("--libs-dir", default=None)

    # check-acyclic
    check_parser = subparsers.add_parser("check-acyclic")
    check_parser.add_argument("--libs-dir", default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "install-editable":
        return _run_install_editable(args)
    if args.command == "verify-editable":
        return _run_verify_editable(args)
    if args.command == "check-acyclic":
        return _run_check_acyclic(args)
    if args.command == "build-downstream-matrix":
        return _run_build_downstream_matrix(args)
    if args.command == "classify-changes":
        return _run_classify_changes(args)
    return _run_build_release_matrix(args)


def _run_install_editable(args: argparse.Namespace) -> int:
    """Handle the ``install-editable`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    changed = args.changed.split()
    try:
        upstreams = get_editable_upstreams(args.target, libs, changed)
    except DependencyConflictError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"unknown library: {error.args[0]}", file=sys.stderr)
        return 1

    plan = build_install_plan(
        libs[args.target],
        upstreams,
        env_reqs=args.env_reqs,
        ihme_pypi=args.ihme_pypi,
        uv_flags=args.uv_flags,
    )
    run_install(plan, libs_dir)
    return 0


def _run_verify_editable(args: argparse.Namespace) -> int:
    """Handle the ``verify-editable`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    libs = load_libs(libs_dir)
    changed_libs = args.changed.split()
    editable_upstreams = get_editable_upstreams(args.target, libs, changed_libs)
    if not editable_upstreams:
        print(f"{args.target}: no changed in-tree upstreams to verify")
        return 0

    failed = False
    for upstream in editable_upstreams:
        editable = _is_editable_install(upstream.dist_name)
        if not editable:
            print(
                f"::error::{upstream.dist_name} is not editable - it resolved "
                "from PyPI, not in-tree source",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


def _run_check_acyclic(args: argparse.Namespace) -> int:
    """Handle the ``check-acyclic`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    # Runtime deps only: the ``ci_github`` extra pulls in test dependencies that
    # legitimately cycle (e.g. config-tree's tests use pytest-vivarium, which
    # depends on config-tree at runtime). Only a *runtime* dependency cycle is a
    # real problem, and that graph is the one release ordering must be acyclic over.
    libs = load_libs(libs_dir, extras=())
    try:
        sort_topologically(list(libs), libs)
    except DependencyCycleError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"in-tree dependency graph is acyclic ({len(libs)} libraries)")
    return 0


def _run_build_release_matrix(args: argparse.Namespace) -> int:
    """Handle the ``build-release-matrix`` subcommand."""
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
    # Runtime deps only: release order need only respect *runtime* edges (a
    # dependent's install can't resolve until its runtime upstreams are on PyPI;
    # its test deps just need to already be published). The ci_github extra adds
    # test-dep edges that legitimately cycle (e.g. config-tree's tests use
    # pytest-vivarium, which depends on config-tree at runtime), and a topological
    # sort can't be defined over a cyclic graph.
    libs = load_libs(libs_dir, extras=())
    try:
        matrix = get_release_matrix(release_versions, libs)
    except DependencyCycleError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"unknown library: {error.args[0]}", file=sys.stderr)
        return 1
    print(json.dumps(matrix))
    return 0


def _run_build_downstream_matrix(args: argparse.Namespace) -> int:
    """Handle the ``build-downstream-matrix`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    # Default (ci_github) extras: a release can break a dependent through a
    # test-dep edge too, and downstream reachability tolerates the cycles those add.
    libs = load_libs(libs_dir)
    released = args.released.split()
    try:
        downstream = get_transitive_downstreams(released, libs)
        # Every dependent runs on its full python_versions.json matrix: the check runs
        # once at merge, so there's no cost reason to sample a single canonical version.
        matrix = build_python_matrix(downstream, libs)
    except KeyError as error:
        print(f"unknown library: {error.args[0]}", file=sys.stderr)
        return 1
    except MissingPythonVersionsError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(json.dumps(matrix))
    return 0


def _run_classify_changes(args: argparse.Namespace) -> int:
    """Handle the ``classify-changes`` subcommand."""
    libs_dir = _discover_libs_dir(args.libs_dir)
    changed_files = Path(args.changed_files_file).read_text().splitlines()
    libs = load_libs(libs_dir)
    changed = classify_changed_libs(changed_files, libs)
    try:
        matrix = build_python_matrix(changed.build, libs)
    except MissingPythonVersionsError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    # Keys are hyphenated to match the GitHub Actions step outputs they populate.
    print(
        json.dumps(
            {
                "source-changed": changed.source_changed,
                "releasing": changed.releasing,
                "build": changed.build,
                "shared-changed": changed.shared_changed,
                "matrix": matrix,
                "has-changes": bool(matrix["include"]),
            }
        )
    )
    return 0


def _discover_libs_dir(libs_path: str | None) -> Path:
    """Locate the monorepo ``libs/`` directory.

    Uses ``libs_path`` if given. Otherwise walks up from the cwd looking for a
    ``libs/`` directory containing a ``build-utils`` library; failing that,
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
