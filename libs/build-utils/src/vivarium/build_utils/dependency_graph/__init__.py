"""In-tree dependency graph for the vivarium-suite monorepo.

This package is the single source of truth for the dependency relationships
between the packages under ``libs/``. It powers the cross-package CI flows that
let a single PR (or merge) span interdependent packages without an interim
release:

1. **Editable-upstream install** (consumed by ``make install`` via the
   ``install-editable`` CLI subcommand). When a PR modifies several packages -
   for example, bumping ``vivarium-engine`` and consuming the new version from
   ``vivarium-public-health`` - the dependent's declared dependency would
   normally resolve the upstream from PyPI, where the new version does not yet
   exist. :func:`get_editable_upstreams` selects exactly the upstreams that are
   modified in the PR, reachable from the package under test, and version
   compatible, and :func:`build_install_plan` installs them editably (at their
   pending versions) alongside the target in a single ``uv`` invocation.

2. **Ordered release matrix** (consumed by the release workflow via the
   ``build-release-matrix`` CLI subcommand). When a merge to ``main`` bumps several
   packages, :func:`get_release_matrix` emits a GitHub Actions matrix ordered
   dependencies-first, where each entry carries the in-batch upstreams it must
   wait for on PyPI before installing; independent packages release in parallel
   while dependents serialize along real dependency edges.

3. **Downstream release check** (consumed by the Downstream Check workflow via the
   ``build-downstream-matrix`` CLI subcommand). When a PR bumps a library's version,
   :func:`get_transitive_downstreams` finds that library's in-tree dependents and
   ``build-downstream-matrix`` emits a GitHub Actions matrix so each dependent is
   tested against the pending version before the release can merge.

4. **Change detection** (consumed by the CI and Downstream Check workflows via the
   ``classify-changes`` CLI subcommand). Given the changed paths in a diff,
   :func:`classify_changed_libs` reports which libraries have source changes (the
   set to resolve editably in flows 1 and 3) and which are bumping a version, and
   :func:`build_python_matrix` fans the libraries to check out over their supported
   Python versions.

5. **Candidate Python check** (consumed by the Candidate Check workflow via the
   ``build-candidate-matrix`` CLI subcommand, and by the CI workflow via
   ``validate-candidates``). A library can declare Python versions it wants exercised
   before supporting them; :func:`build_candidate_matrix` fans every library out over
   its declared candidates for a scheduled run against ``main``. These never reach a
   gating matrix, so a version the ecosystem is not ready for cannot block a merge.
   :func:`find_candidate_conflicts` backs ``validate-candidates``, which fails a build
   whose declarations name a version as both supported and a candidate.

Run as ``python -m vivarium.build_utils.dependency_graph <subcommand>``.

The implementation is split across submodules - :mod:`models` (every data type:
the libraries, the install plan, and the GitHub Actions matrix payloads),
:mod:`loading` (parse ``libs/`` from disk), :mod:`graph` (reachability and
topological ordering), :mod:`editable` (editable-upstream install), :mod:`release`
(release matrix), :mod:`changes` (change detection and per-library matrix),
:mod:`candidates` (candidate Python matrix and its declaration guard), and
:mod:`cli` (command-line interface) - and the load-bearing names are re-exported here.
"""

from __future__ import annotations

from .candidates import (
    build_candidate_matrix,
    find_candidate_conflicts,
    format_candidate_conflicts,
)
from .changes import (
    BUILD_IRRELEVANT_PATTERN,
    build_python_matrix,
    classify_changed_libs,
    is_shared_path,
)
from .cli import _discover_libs_dir, main
from .editable import build_install_plan, get_editable_upstreams, run_install
from .graph import get_transitive_downstreams, get_transitive_upstreams, sort_topologically
from .loading import load_libs
from .models import (
    DEFAULT_EXTRAS,
    CandidateVersionConflictError,
    ChangedLibs,
    DependencyConflictError,
    DependencyCycleError,
    InstallPlan,
    Lib,
    MissingPythonVersionsError,
    PythonMatrix,
    PythonMatrixEntry,
    ReleaseMatrix,
    ReleaseMatrixEntry,
    WaitForEntry,
)
from .release import get_release_matrix
