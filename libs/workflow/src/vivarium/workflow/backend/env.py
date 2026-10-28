"""Resolve a declared environment to its prefix and the ``PATH`` entries that select it.

A command step that declares an environment runs with that environment's ``bin``
directories first on ``PATH``. Environments may be conda envs or venvs, including
venv overlays on a shared conda env. Adapted from vivarium-cluster-tools' Jobmon
environment resolution, so this library needs neither cluster-tools nor Jobmon.
"""

import json
import os
import subprocess
import warnings
from pathlib import Path

# TODO(MIC-7713): keep one copy of environment resolution; dagger and psimulate still use
# vivarium.cluster_tools.core.jobmon.env.

VENV_DIR_NAME = ".venv"
"""Directory under the current working directory searched for a venv of the requested name."""


def resolve_env_prefix(env: str) -> str:
    """Resolve an environment name or path to its absolute filesystem prefix.

    ``env`` may name a conda env or a venv, or be a path to either's prefix directory
    (anything containing a path separator or starting with ``~`` is a path). A name is
    matched first to a venv at ``.venv/<env>`` under the current working directory, then
    to a conda env through ``conda env list --json``, using ``CONDA_EXE``; without
    ``CONDA_EXE`` the conda lookup is skipped. A conda env matches when the last component
    of its prefix equals the name, so the base env, whose prefix is the conda install
    directory, is selected by its path rather than by ``base``. When a name matches both a
    local venv and a conda env in different directories, the venv wins and a
    :class:`UserWarning` names both. Returned prefixes are fully resolved, so one
    environment always yields one string.

    Parameters
    ----------
    env
        The environment's name, or a path to its prefix.

    Returns
    -------
        The resolved prefix directory. A path or a local venv must contain ``bin/python``;
        a conda env found by name is not checked for it.

    Raises
    ------
    RuntimeError
        If ``env`` is a path that does not contain ``bin/python`` or cannot be checked (an
        unknown ``~user``, or a parent directory that cannot be searched), a name that no
        lookup resolves, or a name whose conda lookup fails. Every message names ``env``; a
        failed conda lookup says whether ``CONDA_EXE`` could not be run, exited with a
        non-zero status, or printed output that is not JSON.
    """
    if os.sep in env or env.startswith("~"):
        try:
            prefix = Path(env).expanduser().resolve()
            is_prefix = _is_env_prefix(prefix)
        except (RuntimeError, OSError) as error:
            raise RuntimeError(
                f"Environment path {env!r} could not be checked: {error}"
            ) from error
        if not is_prefix:
            named = repr(env) if env == str(prefix) else f"{env!r} ({prefix})"
            raise RuntimeError(
                f"Environment path {named} is not a conda env or venv: expected a prefix "
                "directory containing bin/python."
            )
        return str(prefix)

    candidates = _find_env_candidates(env)
    if not candidates:
        conda_search = (
            "no conda env of that name is listed"
            if os.environ.get("CONDA_EXE")
            else "conda was not searched because CONDA_EXE is not set"
        )
        raise RuntimeError(
            f"Could not resolve environment {env!r}: there is no venv at "
            f"{Path.cwd() / VENV_DIR_NAME / env}, and {conda_search}."
        )
    winner_label, winner_prefix = candidates[0]
    if len({prefix for _, prefix in candidates}) > 1:
        listing = "; ".join(f"the {label} at {prefix}" for label, prefix in candidates)
        warnings.warn(
            f"Environment name {env!r} is ambiguous: it matches {listing}. Using the "
            f"{winner_label}. Pass a path as the environment to select a specific one.",
            UserWarning,
            stacklevel=2,
        )
    return winner_prefix


def resolve_env_bin_path(env_prefix: str) -> str:
    """Return the colon-joined ``bin`` directories to put first on ``PATH`` for an env.

    For a conda env this is ``<prefix>/bin``. For a venv, the base interpreter's directory
    (the ``home`` key in the venv's ``pyvenv.cfg``) follows the venv's own ``bin``, so
    console scripts installed only in the base environment still resolve.

    Parameters
    ----------
    env_prefix
        A prefix as returned by :func:`resolve_env_prefix`.

    Returns
    -------
        The directories, joined with ``:``.
    """
    bin_dirs = [f"{env_prefix}/bin"]
    base_bin_dir = _get_venv_base_bin_dir(Path(env_prefix))
    if base_bin_dir is not None:
        bin_dirs.append(base_bin_dir)
    return ":".join(bin_dirs)


def _find_env_candidates(env: str) -> list[tuple[str, str]]:
    """Return (source, prefix) pairs matching ``env`` by name, in precedence order."""
    candidates: list[tuple[str, str]] = []
    local_venv = Path.cwd() / VENV_DIR_NAME / env
    if _is_env_prefix(local_venv):
        candidates.append(("local venv", str(local_venv.resolve())))
    conda_prefix = _find_conda_env(env)
    if conda_prefix is not None:
        candidates.append(("conda env", str(conda_prefix.resolve())))
    return candidates


def _is_env_prefix(prefix: Path) -> bool:
    """Check that ``prefix`` is an environment root containing ``bin/python``."""
    return (prefix / "bin" / "python").exists()


def _find_conda_env(env: str) -> Path | None:
    """Return the conda env named ``env``, or None; raise RuntimeError if the lookup fails."""
    conda_exe = os.environ.get("CONDA_EXE")
    if not conda_exe:
        return None
    failed = f"Could not resolve environment {env!r}: the conda lookup failed"
    listing = f"'{conda_exe} env list --json'"
    try:
        result = subprocess.run(
            [conda_exe, "env", "list", "--json"], check=True, capture_output=True, text=True
        )
    except OSError as error:
        raise RuntimeError(
            f"{failed} because CONDA_EXE {conda_exe!r} could not be run ({error})."
        ) from error
    except subprocess.CalledProcessError as error:
        stderr = " ".join((error.stderr or "").split())
        status = f"{failed} because {listing} exited with status {error.returncode}"
        raise RuntimeError(f"{status}: {stderr}" if stderr else f"{status}.") from error
    try:
        prefixes = [Path(path) for path in json.loads(result.stdout)["envs"]]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"{failed} because the output of {listing} is not JSON with an 'envs' list "
            f"({type(error).__name__}: {error})."
        ) from error
    return next((prefix for prefix in prefixes if prefix.name == env), None)


def _get_venv_base_bin_dir(env_prefix: Path) -> str | None:
    """Return the base interpreter's bin directory (``home`` in ``pyvenv.cfg``), if any."""
    pyvenv_cfg = env_prefix / "pyvenv.cfg"
    if not pyvenv_cfg.is_file():
        return None
    for line in pyvenv_cfg.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            return value.strip() or None
    return None
