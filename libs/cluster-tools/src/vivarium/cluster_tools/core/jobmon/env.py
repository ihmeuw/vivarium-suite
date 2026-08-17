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
    directory. A name is matched to a venv first (under ``.venv/<env>`` in
    current working directory), and then a conda env via ``conda env list --json``.

    When the name matches more than one distinct environment, the
    highest-precedence match wins and a warning names every match.
    Returned prefixes are fully resolved (symlinks followed) so that the
    same environment always yields the same prefix string.

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
            f"there is no venv at {Path.cwd() / VENV_DIR_NAME / env} and it "
            "was not found by `conda env list`."
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
    local_venv = Path.cwd() / VENV_DIR_NAME / env
    if _is_env_prefix(local_venv):
        candidates["local venv"] = str(local_venv.resolve())
    conda_prefix = _find_conda_env(env)
    if conda_prefix is not None:
        candidates["conda env"] = str(conda_prefix.resolve())
    return candidates


def _is_env_prefix(prefix: Path) -> bool:
    """Check that ``prefix`` is an environment root containing ``bin/python``."""
    return (prefix / "bin" / "python").exists()


def _find_conda_env(env: str) -> Path | None:
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
        (Path(path) for path in json.loads(result.stdout)["envs"] if Path(path).name == env),
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
