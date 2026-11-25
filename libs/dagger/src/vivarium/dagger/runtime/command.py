"""Run a command step's shell command with its env's directories first on ``PATH``."""

import os
import socket
import subprocess
from pathlib import Path

COLLECT_SIMULATION_VARIABLE = "DAGGER_COLLECT_SIMULATION"
"""Environment variable that gives a simulation step's command the path of its check."""

COLLECT_SIMULATION_SCRIPT = (
    Path(__file__).resolve().parent.parent / "frontend" / "_collect_simulation.py"
)
"""The check a simulation step's command runs, found without importing the frontend."""


def run_command_with_bin_path(command: str, bin_path: str | None) -> None:
    """Run ``command`` in a shell with the directories in ``bin_path`` first on ``PATH``.

    This is :func:`~vivarium.dagger.backend.build.run_command` after its env is resolved,
    for a caller that resolved the env elsewhere, such as a compute node running a step
    the controller planned. The directories are set on the child process's ``PATH``, so
    they apply to every part of a compound command such as ``a && b``, and no empty entry
    is added to ``PATH``. The command's environment also sets ``DAGGER_COLLECT_SIMULATION``
    to the path of the check that a simulation step's command runs. Output goes to this
    process's standard output and standard error.

    Parameters
    ----------
    command
        The shell command to run.
    bin_path
        Colon-joined directories to put first on ``PATH``, as
        :func:`~vivarium.dagger.backend.env.resolve_env_bin_path` returns. ``None``
        runs the command with the current ``PATH``.

    Raises
    ------
    FileNotFoundError
        If the env's own ``bin`` directory, the first in ``bin_path``, does not exist on
        this host, as when the env was resolved on a controller whose paths a compute node
        does not have. The command is then not run, so it cannot fall back to a program of
        the same name later on ``PATH``.
    subprocess.CalledProcessError
        If the command exits with a non-zero status.
    """
    child_env = {**os.environ, COLLECT_SIMULATION_VARIABLE: str(COLLECT_SIMULATION_SCRIPT)}
    if bin_path is None:
        subprocess.run(command, shell=True, check=True, env=child_env)
        return
    env_bin = bin_path.split(":")[0]
    if not os.path.isdir(env_bin):
        raise FileNotFoundError(
            f"The env's bin directory {env_bin} does not exist on host "
            f"{socket.gethostname()}, so the command was not run. A step's env must be at "
            "the same path on the host that plans the build and on every host that runs "
            "its steps."
        )
    parts = [*bin_path.split(":"), os.environ.get("PATH", "")]
    path = ":".join(part for part in parts if part)
    subprocess.run(command, shell=True, check=True, env={**child_env, "PATH": path})
