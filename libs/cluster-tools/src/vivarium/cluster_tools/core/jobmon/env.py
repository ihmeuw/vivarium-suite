"""
==========================
Jobmon Task Env Resolution
==========================

Conda env -> filesystem prefix resolution used when constructing Jobmon
worker commands so each task picks up the configured env's ``python``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def resolve_env_prefix(env: str) -> str:
    """Resolve a conda env name to its absolute filesystem prefix.

    Uses ``CONDA_PREFIX`` directly when *env* matches the active env. For
    other env names, queries ``conda env list --json`` via ``CONDA_EXE``
    to find the matching prefix.

    """
    if env == os.environ.get("CONDA_DEFAULT_ENV"):
        env_prefix: str | None = os.environ["CONDA_PREFIX"]
    else:
        result = subprocess.run(
            [os.environ["CONDA_EXE"], "env", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        env_prefix = next(
            (
                str(path)
                for path in json.loads(result.stdout)["envs"]
                if Path(path).name == env
            ),
            None,
        )
    if env_prefix is None:
        raise RuntimeError(
            f"Could not resolve conda env {env!r} to a filesystem prefix "
            "via `conda env list`."
        )
    return env_prefix
