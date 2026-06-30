"""Editable-sibling selection and ``uv pip install`` plan composition.

Powers the cross-package CI install: pick the modified, reachable, version-
compatible siblings of the package under build, and compose a single editable
``uv pip install`` that resolves them from in-tree source at their pending
versions.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .graph import get_reachable_siblings
from .models import DependencyConflictError, InstallPlan, Lib


def get_editable_siblings(
    target: str, libs: Mapping[str, Lib], changed: Sequence[str]
) -> list[Lib]:
    """Select the siblings to install editably for a build of ``target``.

    Returns the packages in ``changed`` that are reachable from ``target``.
    Packages in ``changed`` that are not reachable from ``target`` are ignored
    (a change elsewhere in the monorepo does not affect this build). Before
    returning, validates that each selected sibling's pending version satisfies
    every reachable package's declared constraint on it.

    Notes
    -----
    The returned order is not meaningful - both consumers (a single combined install
    and the verify check) are order-insensitive.

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
        The selected siblings as :class:`Lib`s.

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

    return [libs[name] for name in editable_sibling_names]


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
