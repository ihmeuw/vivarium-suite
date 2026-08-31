"""Core data types for the in-tree dependency graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from packaging.specifiers import SpecifierSet

# The pyproject extra whose dependency closure CI activates (``make install
# ENV_REQS=ci_github`` in both the GitHub Actions test matrix and the release
# job). The dependency graph is resolved over runtime dependencies plus this
# extra so the editable-upstream and release-ordering decisions reflect the
# dependency set the install actually pulls in.
DEFAULT_EXTRAS: tuple[str, ...] = ("ci_github",)


class DependencyConflictError(Exception):
    """A selected in-tree upstream's pending version violates a declared pin."""


class DependencyCycleError(Exception):
    """The in-tree dependency graph contains a cycle and cannot be ordered."""


class MissingPythonVersionsError(Exception):
    """A library has no ``python_versions.json``, so its CI matrix cannot be built."""


class CandidateVersionConflictError(Exception):
    """A library declares a candidate Python version it already supports.

    Promoting a candidate means moving it into ``python_versions.json`` *and* dropping
    it from ``candidates``; leaving both would emit the version twice, once gating and
    once not.
    """


@dataclass(frozen=True)
class Lib:
    """A single independently-released library under ``libs/``.

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
    upstreams
        This library's dependencies on *other monorepo libraries*: a mapping from
        each depended-on upstream's ``dist_name`` to the version constraint this
        library places on it. For example, ``vivarium-public-health`` yields
        ``{"vivarium-engine": SpecifierSet(">=5.1.1"), "vivarium-config-tree":
        SpecifierSet(">=5.0.0"), ...}``. External dependencies (``numpy``,
        ``dill``, ...) are excluded; only ``libs/`` libraries appear. Collected
        over the runtime dependencies plus whichever extras :func:`load_libs`
        resolved; if a upstream is constrained in more than one of those places,
        the constraints are intersected into a single :class:`SpecifierSet`.
    """

    name: str
    dist_name: str
    path: Path
    version: str
    upstreams: Mapping[str, SpecifierSet]


@dataclass(frozen=True)
class InstallPlan:
    """A fully-composed ``uv pip install`` invocation.

    Attributes
    ----------
    argv
        The argument vector to execute (e.g. ``["uv", "pip", "install", "-e", ...]``).
    env
        Environment overrides to apply on top of the current environment when
        executing ``argv`` (notably the per-upstream ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>``
        entries that make each editable upstream present its pending release version).
    """

    argv: Sequence[str]
    env: Mapping[str, str]


@dataclass(frozen=True)
class ChangedLibs:
    """The libraries a diff touched, partitioned by what CI does about each.

    Attributes
    ----------
    source_changed
        Libraries with at least one changed file under ``libs/<name>/``. These are
        the libraries to resolve editably from in-tree source when installing any
        library under test, since their pending versions do not exist on PyPI.
    pending_release
        Libraries whose ``CHANGELOG.rst`` changed, i.e. those the diff is bumping
        toward a release. Always a subset of ``source_changed``.
    to_build
        Libraries whose full check suite CI should run: ``source_changed``, or every
        library when the diff touches a shared path (see ``shared_changed``).
    shared_changed
        Whether the diff touched a shared path - one outside ``libs/`` that no
        single library owns, so every library must be rebuilt. See
        ``is_shared_path`` in :mod:`changes`.
    """

    source_changed: tuple[str, ...]
    pending_release: tuple[str, ...]
    to_build: tuple[str, ...]
    shared_changed: bool


# The GitHub Actions ``strategy.matrix`` payloads. Both are matrix objects, so both
# wrap an ``include`` list of per-job entries; a shared generic base would need
# PEP 646 generic TypedDicts (3.11+) and this package supports 3.10.
#
# ``PythonMatrixEntry`` needs the functional TypedDict form because ``python-version``
# is the key GitHub Actions expects and a hyphen is not a valid attribute name. Its
# ``library`` is the ``libs/`` directory name and ``python-version`` is one entry from
# that library's ``python_versions.json``.
#
# ``experimental`` marks a candidate version (one being soaked in CI but not yet
# supported). It is emitted on every entry, not just candidates.
PythonMatrixEntry = TypedDict(
    "PythonMatrixEntry",
    {"library": str, "python-version": str, "experimental": bool},
)


class PythonMatrix(TypedDict):
    """The GitHub Actions ``strategy.matrix`` object for a per-library job.

    Attributes
    ----------
    include
        One entry per library per Python version that library should be checked on.
    """

    include: list[PythonMatrixEntry]


class WaitForEntry(TypedDict):
    """A single in-batch upstream a release must wait for on PyPI.

    Attributes
    ----------
    dist
        The upstream's PyPI distribution name, i.e. what to poll for.
    version
        The version being released, i.e. what to poll until it appears.
    """

    dist: str
    version: str


class ReleaseMatrixEntry(TypedDict):
    """One library's entry in the release matrix.

    Attributes
    ----------
    library
        The ``libs/`` directory name.
    dist
        The PyPI distribution name, which is also the git tag prefix.
    version
        The version being released.
    wait_for
        The in-batch upstreams this release must wait for on PyPI before it can
        install. Upstreams outside the batch are already released, so they are
        omitted rather than waited on.
    """

    library: str
    dist: str
    version: str
    wait_for: list[WaitForEntry]


class ReleaseMatrix(TypedDict):
    """The GitHub Actions ``strategy.matrix`` object for the release workflow.

    Attributes
    ----------
    include
        One entry per library being released, ordered dependencies-first by
        :func:`get_release_matrix` so dependents serialize behind their upstreams.
    """

    include: list[ReleaseMatrixEntry]
