"""Build shared-representation tasks with pytask."""

import inspect
import keyword
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytask
from pytask import NodeInfo, PNode, PythonNode, Session, TaskOutcome, TaskWithoutPath

from vivarium.cluster_tools.core.backend.task import Task, check_unique_task_names

CODE_ID_KEY = "__code_id__"
"""Reserved ``depends_on`` key under which a task's ``code_id`` node is attached."""

COMMAND_KEY = "__command__"
"""Reserved ``depends_on`` key under which a command-string ``run`` is attached."""

TASK_KEY = "task"
"""Key under which a pytask task's attributes hold its shared-representation task."""


def to_pytask_task(task: Task) -> TaskWithoutPath:
    """Convert a shared-representation task into a pytask task.

    The pytask task depends on ``task.inputs``, on ``task.code_id`` under ``CODE_ID_KEY``,
    and on a command-string ``run`` under ``COMMAND_KEY``. It produces ``task.outputs``
    and keeps ``task`` in its attributes under ``TASK_KEY``. Running it calls a callable
    ``run`` with each loaded input and output by name, or runs a command-string ``run``
    in a shell.

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
        kwargs.pop(CODE_ID_KEY, None)
        if isinstance(task.run, str):
            subprocess.run(task.run, shell=True, check=True)
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
    depends_on: dict[str, PNode] = {**task.inputs, CODE_ID_KEY: task.code_id}
    if isinstance(task.run, str):
        depends_on[COMMAND_KEY] = PythonNode(
            name=f"{task.name}::{COMMAND_KEY}",
            value=task.run,
            hash=True,
            node_info=NodeInfo(
                arg_name=COMMAND_KEY,
                path=(),
                task_path=None,
                task_name=task.name,
                value=task.run,
            ),
        )
    return TaskWithoutPath(
        name=task.name,
        function=_run,
        depends_on=depends_on,
        produces=dict(task.outputs),
        attributes={TASK_KEY: task},
    )


def build(tasks: Iterable[Task], root: Path, **kwargs: Any) -> Session:
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
        If two tasks share a name or a task cannot be converted.
    """
    tasks = list(tasks)
    check_unique_task_names(tasks)
    pytask_tasks = [to_pytask_task(task) for task in tasks]
    root.mkdir(parents=True, exist_ok=True)
    # pytasks uses whatever .git repo is upstream of root by default,
    # but will use the true root dir if you pass a pytask.toml config file.
    config = root / "pytask.toml"
    if not config.exists():
        config.write_text("[tool.pytask.ini_options]\n")
    return pytask.build(tasks=pytask_tasks, config=config, task_files=(), **kwargs)


def outcomes(session: Session) -> dict[str, TaskOutcome]:
    """Return the outcome of each executed task, keyed by task name."""
    return {report.task.name: report.outcome for report in session.execution_reports}
