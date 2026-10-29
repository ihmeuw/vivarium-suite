"""Build shared-representation tasks with pytask."""

import inspect
import keyword
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytask

from vivarium.workflow.backend.env import resolve_env_bin_path, resolve_env_prefix
from vivarium.workflow.backend.task import Task, check_unique_task_names

CODE_ID_KEY = "__code_id__"
"""Reserved ``depends_on`` key under which a task's ``code_id`` node is attached."""

COMMAND_KEY = "__command__"
"""Reserved ``depends_on`` key under which a command-string ``run`` is attached."""

TASK_KEY = "task"
"""Key under which a pytask task's attributes hold its shared-representation task."""


def run_command(command: str, env: str | None) -> None:
    """Run ``command`` in a shell with ``env``'s ``bin`` directories first on ``PATH``.

    The directories are set on the child process's ``PATH``, so they apply to every part
    of a compound command such as ``a && b``. Output goes to this process's standard
    output and standard error.

    Parameters
    ----------
    command
        The shell command to run.
    env
        The environment to run it in: a name or a prefix path, as
        :func:`~vivarium.workflow.backend.env.resolve_env_prefix` accepts. ``None`` runs
        the command with the current ``PATH``.

    Raises
    ------
    RuntimeError
        If ``env`` does not resolve.
    subprocess.CalledProcessError
        If the command exits with a non-zero status.
    """
    bin_path = None if env is None else resolve_env_bin_path(resolve_env_prefix(env))
    run_command_with_bin_path(command, bin_path)


def run_command_with_bin_path(command: str, bin_path: str | None) -> None:
    """Run ``command`` in a shell with the directories in ``bin_path`` first on ``PATH``.

    This is :func:`run_command` after its env is resolved, for a caller that resolved the
    env elsewhere, such as a compute node running a step the controller planned. The
    directories are set on the child process's ``PATH``, so they apply to every part of a
    compound command such as ``a && b``, and no empty entry is added to ``PATH``. Output
    goes to this process's standard output and standard error.

    Parameters
    ----------
    command
        The shell command to run.
    bin_path
        Colon-joined directories to put first on ``PATH``, as
        :func:`~vivarium.workflow.backend.env.resolve_env_bin_path` returns. ``None``
        runs the command with the current ``PATH``.

    Raises
    ------
    subprocess.CalledProcessError
        If the command exits with a non-zero status.
    """
    if bin_path is None:
        subprocess.run(command, shell=True, check=True)
        return
    parts = [*bin_path.split(":"), os.environ.get("PATH", "")]
    path = ":".join(part for part in parts if part)
    subprocess.run(command, shell=True, check=True, env={**os.environ, "PATH": path})


def _check_envs(tasks: Iterable[Task]) -> None:
    """Resolve each distinct declared env once; raise one ValueError listing the bad ones."""
    declarers: dict[str, list[str]] = {}
    for task in tasks:
        if task.env is not None:
            declarers.setdefault(task.env, []).append(task.name)
    failures: list[str] = []
    for env, names in declarers.items():
        try:
            resolve_env_prefix(env)
        except RuntimeError as error:
            tasks_named = ", ".join(f"Task '{name}'" for name in names)
            failures.append(f"{tasks_named}: {error}")
    if failures:
        raise ValueError(
            "Declared envs do not resolve, so no task was run:\n" + "\n".join(failures)
        )


def to_pytask_task(task: Task) -> pytask.TaskWithoutPath:
    """Convert a shared-representation task into a pytask task.

    The pytask task depends on ``task.inputs``, on ``task.code_id`` under ``CODE_ID_KEY``,
    and on a command-string ``run`` under ``COMMAND_KEY``. It produces ``task.outputs``
    and keeps ``task`` in its attributes under ``TASK_KEY``. Running it calls a callable
    ``run`` with each loaded input and output by name, or runs a command-string ``run``
    through :func:`run_command` under the task's ``env``.

    Parameters
    ----------
    task
        Task to convert.

    Returns
    -------
        The equivalent pytask task.

    Raises
    ------
    ValueError
        If an input or output is named ``CODE_ID_KEY`` or ``COMMAND_KEY``, an output is
        named ``"return"``, an input and an output share a name, or a callable ``run``
        has an output name that is not a valid parameter name.
    """
    for side, names in (("input", task.inputs), ("output", task.outputs)):
        for key in (CODE_ID_KEY, COMMAND_KEY):
            if key in names:
                raise ValueError(f"Task '{task.name}': {side} name {key!r} is reserved.")
    if "return" in task.outputs:
        raise ValueError(f"Task '{task.name}': output name 'return' is reserved by pytask.")
    shared = sorted(set(task.inputs) & set(task.outputs))
    if shared:
        raise ValueError(f"Task '{task.name}': names {shared} are both inputs and outputs.")

    def _run(**kwargs: Any) -> None:
        """Run ``task`` with its loaded inputs and outputs."""
        __tracebackhide__ = True
        kwargs.pop(CODE_ID_KEY, None)
        if isinstance(task.run, str):
            run_command(task.run, task.env)
        else:
            task.run(**kwargs)

    if callable(task.run):
        for key in task.outputs:
            if not key.isidentifier() or keyword.iskeyword(key):
                raise ValueError(
                    f"Task '{task.name}': output name {key!r} is not a valid parameter name."
                )
        parameters = [
            inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY) for key in task.outputs
        ]
        setattr(_run, "__signature__", inspect.Signature(parameters))
    assert task.code_id is not None  # Task.__post_init__ guarantees a code_id node.
    depends_on: dict[str, pytask.PNode] = {**task.inputs, CODE_ID_KEY: task.code_id}
    if isinstance(task.run, str):
        depends_on[COMMAND_KEY] = pytask.PythonNode(
            name=f"{task.name}::{COMMAND_KEY}",
            value=task.run,
            hash=True,
            node_info=pytask.NodeInfo(
                arg_name=COMMAND_KEY,
                path=(),
                task_path=None,
                task_name=task.name,
                value=task.run,
            ),
        )
    return pytask.TaskWithoutPath(
        name=task.name,
        function=_run,
        depends_on=depends_on,
        produces=dict(task.outputs),
        attributes={TASK_KEY: task},
    )


def build(tasks: Iterable[Task], root: Path, **kwargs: Any) -> pytask.Session:
    """Build ``tasks`` with pytask, keeping its state under ``root``.

    Parameters
    ----------
    tasks
        Tasks to build.
    root
        Directory for pytask's configuration, state database, and lockfile; created if
        missing.
    kwargs
        Extra keyword arguments passed to ``pytask.build``.

    Returns
    -------
        The pytask session. Build failures are not raised; check its ``exit_code``.

    Raises
    ------
    ValueError
        If two tasks share a name, a task cannot be converted, or a declared env does not
        resolve. Envs are checked before any task runs. The error has one line per env that
        does not resolve, naming that env once, the tasks that declare it, and why.
    """
    tasks = list(tasks)
    check_unique_task_names(tasks)
    _check_envs(tasks)
    pytask_tasks = [to_pytask_task(task) for task in tasks]
    root.mkdir(parents=True, exist_ok=True)
    # pytasks uses whatever .git repo is upstream of root by default,
    # but will use the true root dir if you pass a pytask.toml config file.
    config = root / "pytask.toml"
    if not config.exists():
        config.write_text("[tool.pytask.ini_options]\n")
    return pytask.build(tasks=pytask_tasks, config=config, task_files=(), **kwargs)


def outcomes(session: pytask.Session) -> dict[str, pytask.TaskOutcome]:
    """Return the outcome of each executed task, keyed by task name."""
    return {report.task.name: report.outcome for report in session.execution_reports}
