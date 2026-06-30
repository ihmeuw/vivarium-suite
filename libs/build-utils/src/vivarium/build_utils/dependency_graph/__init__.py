"""In-tree dependency graph for the vivarium-suite monorepo.

This package is the single source of truth for the dependency relationships
between the packages under ``libs/``. It powers two cross-package CI flows that
let a single PR (or merge) span interdependent packages without an interim
release:

1. **Editable-sibling install** (consumed by ``make install`` via the
   ``install-editable`` CLI subcommand). When a PR modifies several packages -
   for example, bumping ``vivarium-engine`` and consuming the new version from
   ``vivarium-public-health`` - the dependent's declared dependency would
   normally resolve the upstream from PyPI, where the new version does not yet
   exist. :func:`get_editable_siblings` selects exactly the siblings that are
   modified in the PR, reachable from the package under test, and version
   compatible, and :func:`build_install_plan` installs them editably (at their
   pending versions) alongside the target in a single ``uv`` invocation.

2. **Ordered release matrix** (consumed by the release workflow via the
   ``build-release-matrix`` CLI subcommand). When a merge to ``main`` bumps several
   packages, :func:`get_release_matrix` emits a GitHub Actions matrix ordered
   dependencies-first, where each entry carries the in-batch upstreams it must
   wait for on PyPI before installing; independent packages release in parallel
   while dependents serialize along real dependency edges.

Run as ``python -m vivarium.build_utils.dependency_graph <subcommand>``.

The implementation is split across submodules - :mod:`models` (data types),
:mod:`loading` (parse ``libs/`` from disk), :mod:`graph` (reachability and
topological ordering), :mod:`editable` (editable-sibling install), :mod:`release`
(release matrix), and :mod:`cli` (command-line interface) - and the load-bearing
names are re-exported here.
"""

from __future__ import annotations

from .cli import _discover_libs_dir, main
from .editable import build_install_plan, get_editable_siblings, run_install
from .graph import get_reachable_siblings, get_release_order
from .loading import load_libs
from .models import (
    DEFAULT_EXTRAS,
    DependencyConflictError,
    DependencyCycleError,
    InstallPlan,
    Lib,
)
from .release import get_release_matrix

__all__ = [
    "DEFAULT_EXTRAS",
    "DependencyConflictError",
    "DependencyCycleError",
    "InstallPlan",
    "Lib",
    "build_install_plan",
    "get_editable_siblings",
    "get_reachable_siblings",
    "get_release_matrix",
    "get_release_order",
    "load_libs",
    "main",
    "run_install",
]
