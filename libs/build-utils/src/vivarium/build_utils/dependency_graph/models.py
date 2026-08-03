"""Core data types for the in-tree dependency graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
