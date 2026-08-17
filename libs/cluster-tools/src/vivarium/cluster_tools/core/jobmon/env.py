"""
==========================
Jobmon Task Env Resolution
==========================

Environment -> filesystem prefix resolution used when constructing Jobmon
worker commands so each task picks up the configured env's ``python``.
Environments may be conda envs or venvs, including the shared-env venv
overlays created by ``make build-shared-env``.

"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from loguru import logger

VENV_DIR_NAME = ".venv"
"""Directory under the current working directory searched when resolving an
environment name to a local venv; matches where ``make build-shared-env``
creates its venv overlays."""


def resolve_env_prefix(env: str) -> str:
    """Resolve an environment name or path to its absolute filesystem prefix.

    *env* may name a conda env or a venv, or be a path to either's prefix
    directory. A path (*env* contains a path separator or starts with
    ``~``) is validated and returned directly; this can never mistake a
    name for a path, since conda forbids separators in env names and a
    venv name is a single directory name. A name is matched against, in
    order of precedence:

    1. the active conda env (``CONDA_DEFAULT_ENV`` -> ``CONDA_PREFIX``);
    2. the active venv (``VIRTUAL_ENV``), matched by directory name;
    3. ``.venv/<env>`` under the current working directory, where
       ``make build-shared-env`` creates its venv overlays;
    4. ``conda env list --json`` via ``CONDA_EXE``, matched by env name
       (skipped when ``CONDA_EXE`` is unset).

    When the name matches more than one distinct environment, the
    highest-precedence match wins and a warning names every match.
    Returned prefixes are fully resolved (symlinks followed) so that the
    same environment always yields the same prefix string - Jobmon task
    hashes depend on it, and an unstable spelling would defeat
    ``dagger restart``.

    Raises
    ------
    RuntimeError
        If *env* is a path that is not an environment prefix, or a name
        that no lookup can resolve.
    """
    if os.sep in env or env.startswith("~"):
        prefix = Path(env).expanduser().resolve()
        if not _is_env_prefix(prefix):
            raise RuntimeError(
                f"Environment path {str(prefix)!r} is not a conda env or venv: "
                "expected a prefix directory containing bin/python."
            )
        return str(prefix)

    candidates = _find_env_candidates(env)
    if not candidates:
        raise RuntimeError(
            f"Could not resolve environment {env!r} to a filesystem prefix: "
            "it is not the active conda env or venv, was not found by "
            f"`conda env list`, and there is no venv at "
            f"{Path.cwd() / VENV_DIR_NAME / env}."
        )
    winner_label, winner_prefix = next(iter(candidates.items()))
    if len(set(candidates.values())) > 1:
        listing = "; ".join(
            f"the {label} at {prefix}" for label, prefix in candidates.items()
        )
        logger.warning(
            f"Environment name {env!r} is ambiguous: it matches {listing}. "
            f"Using the {winner_label}. Pass a path as the environment to "
            "select a specific one."
        )
    return winner_prefix


def resolve_env_bin_path(env_prefix: str) -> str:
    """Return the colon-joined bin directories to prepend to ``PATH`` for an env.

    For a conda env this is ``<prefix>/bin``. For a venv, the base
    interpreter's directory (the ``home`` key in ``pyvenv.cfg``) follows the
    venv's own ``bin`` so that console scripts installed only in the base
    environment (e.g. ``psimulate`` in the shared conda env under a
    ``make build-shared-env`` overlay) also resolve - mirroring the ``PATH``
    the overlay's patched activate script builds.
    """
    bin_dirs = [f"{env_prefix}/bin"]
    base_bin_dir = _venv_base_bin_dir(Path(env_prefix))
    if base_bin_dir is not None:
        bin_dirs.append(base_bin_dir)
    return ":".join(bin_dirs)


def _find_env_candidates(env: str) -> dict[str, str]:
    """Gather resolved prefixes matching *env* by name, keyed by source, in precedence order."""
    candidates: dict[str, str] = {}
    if env == os.environ.get("CONDA_DEFAULT_ENV"):
        candidates["active conda env"] = _normalize(os.environ["CONDA_PREFIX"])
    active_venv = os.environ.get("VIRTUAL_ENV")
    if active_venv is not None and Path(active_venv).name == env:
        candidates["active venv"] = _normalize(active_venv)
    local_venv = Path.cwd() / VENV_DIR_NAME / env
    if _is_env_prefix(local_venv):
        candidates["local venv"] = _normalize(str(local_venv))
    # The active conda env is definitionally in `conda env list`; skip the
    # subprocess when it already matched.
    if "active conda env" not in candidates:
        conda_prefix = _find_conda_env(env)
        if conda_prefix is not None:
            candidates["conda env"] = _normalize(conda_prefix)
    return candidates


def _normalize(prefix: str) -> str:
    """Fully resolve a prefix so the same env always yields one spelling."""
    return str(Path(prefix).resolve())


def _is_env_prefix(prefix: Path) -> bool:
    """Check that ``prefix`` is an environment root containing ``bin/python``."""
    return (prefix / "bin" / "python").exists()


def _find_conda_env(env: str) -> str | None:
    """Look up *env* in ``conda env list``; None when absent or conda is unavailable."""
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe is None:
        return None
    result = subprocess.run(
        [conda_exe, "env", "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return next(
        (str(path) for path in json.loads(result.stdout)["envs"] if Path(path).name == env),
        None,
    )


def _venv_base_bin_dir(env_prefix: Path) -> str | None:
    """Read the base interpreter's bin dir from a venv's ``pyvenv.cfg``, if any."""
    pyvenv_cfg = env_prefix / "pyvenv.cfg"
    if not pyvenv_cfg.is_file():
        return None
    for line in pyvenv_cfg.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip()
    return None
