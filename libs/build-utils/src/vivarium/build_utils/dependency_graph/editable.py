"""Editable-upstream selection and ``uv pip install`` plan composition.

Powers the cross-library CI install: pick the modified, reachable, version-
compatible upstreams of the library under build, and compose a single editable
``uv pip install`` that resolves them from in-tree source at their pending versions.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .graph import get_transitive_upstreams
from .models import DependencyConflictError, InstallPlan, Lib


def get_editable_upstreams(
    target: str, libs: Mapping[str, Lib], changed: Sequence[str]
) -> list[Lib]:
    """Select the upstream libraries to install editably for a build of ``target``.

    Returns the libraries in ``changed`` that are reachable from ``target``;
    libraries in ``changed`` that are not reachable from ``target`` are ignored
    (a change elsewhere in the monorepo does not affect this build). Before
    returning, validates that each selected upstream's pending version satisfies
    every reachable library's declared constraint on it.

    Notes
    -----
    The returned order is not meaningful - both consumers (a single combined install
    and the verify check) are order-insensitive.

    Parameters
    ----------
    target
        Library ``name`` being built/tested.
    libs
        The full set of parsed libraries.
    changed
        Library ``name``s whose own source changed in the PR.

    Returns
    -------
        The selected upstream libraries as :class:`Lib`s.

    Raises
    ------
    DependencyConflictError
        If a selected upstream library's pending version does not satisfy some reachable
        library's version constraint on it.
    KeyError
        If ``target`` or any entry of ``changed`` is not a key in ``libs``.
    """
    for name in (target, *changed):
        if name not in libs:
            raise KeyError(name)

    reachable_upstreams = get_transitive_upstreams(target, libs)
    editable_upstream_names = [name for name in changed if name in reachable_upstreams]

    constrainers = reachable_upstreams | {target}
    for upstream in editable_upstream_names:
        upstream_dist = libs[upstream].dist_name
        upstream_version = libs[upstream].version  # pending release version
        for library in constrainers:
            specifier = libs[library].upstreams.get(upstream_dist)
            if specifier is None:
                # No declared constraint on this upstream from this library, so nothing to check
                continue
            if not specifier.contains(upstream_version, prereleases=True):
                raise DependencyConflictError(
                    f"in-tree upstream {upstream_dist} pending version "
                    f"{upstream_version} does not satisfy specifier "
                    f"'{specifier}' declared by {libs[library].dist_name}"
                )

    return [libs[name] for name in editable_upstream_names]


def build_install_plan(
    target_lib: Lib,
    editable_upstreams: Sequence[Lib],
    *,
    env_reqs: str,
    ihme_pypi: str,
    uv_flags: str,
) -> InstallPlan:
    """Compose the single ``uv pip install`` invocation for a cross-library build.

    Builds one command that installs ``target_lib`` editably with its
    ``env_reqs`` extra and each upstream editably, by absolute path, so ``uv``
    resolves the named in-tree distributions from local source rather than
    PyPI. Each editable library gets the ``editable_mode=compat`` config setting
    (keyed by its ``dist_name``, matching ``make install``'s classic-``.pth``
    editable mode), and the extra-index flags are included only when
    ``ihme_pypi`` is non-empty. The returned plan's ``env`` carries a
    ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` entry for each upstream so its
    editable install reports its pending release version (a feature branch has
    no release tag, so ``setuptools_scm`` would otherwise derive a dev version
    that fails a bumped pin). The target needs no pretend version - nothing in
    the build depends on it.

    Parameters
    ----------
    target_lib
        The library being built.
    editable_upstreams
        Upstream libraries to install editably.
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
    for upstream in editable_upstreams:
        argv.extend(["-e", str(upstream.path)])
        config_settings.extend(
            [
                "--config-settings-package",
                f"{upstream.dist_name}:editable_mode=compat",
            ]
        )
        dist_upper = upstream.dist_name.upper().replace("-", "_")
        env[f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{dist_upper}"] = upstream.version

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
