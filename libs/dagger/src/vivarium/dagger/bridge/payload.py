"""Turn a pipeline step into a JSON recipe that a compute node can run.

The controller calls :func:`payload_for` for each step it sends to a node. A recipe is
plain JSON: a python step crosses as its target's import reference, the directory its
target's module imports from, its output suffix or suffixes, and its arguments, and a
command step crosses as its command string and the ``bin`` directories of its env,
resolved on the controller. No pickled code or data crosses, so
an argument must be JSON or a path, and a step that cannot be named by reference is
refused before anything is sent. :mod:`~vivarium.dagger.runtime.recipe` defines a recipe,
and :mod:`~vivarium.dagger.bridge.runner` runs it.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from vivarium.dagger.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.dagger.backend.env import resolve_env_bin_path, resolve_env_prefix
from vivarium.dagger.backend.task import Task
from vivarium.dagger.frontend.python_step import PythonStepRun, target_reference
from vivarium.dagger.runtime.recipe import (
    PATH_TAG,
    CommandPayload,
    JSONValue,
    Payload,
    PythonPayload,
    decode,
    encode,
)


def payload_for(
    task: Task,
    kwargs: Mapping[str, Any],
    *,
    env_paths: MutableMapping[str, str] | None = None,
) -> Payload:
    """Return the recipe that runs ``task`` on a compute node.

    A task whose ``run`` is a
    :class:`~vivarium.dagger.runtime.python_run.PythonStepRun` gives a
    :class:`~vivarium.dagger.runtime.recipe.PythonPayload` that names the target by
    :func:`~vivarium.dagger.frontend.python_step.target_reference` and carries the encoded
    ``kwargs``. Its ``sys_path`` holds the import root of the target's module, worked out
    from the module's ``__file__`` and dotted ``__name__``: the directory that holds the
    module's file, then one directory up for each dot in the name, and one more for a
    package's ``__init__.py``, which sits inside the package's own directory. The
    ``__file__`` is made absolute against the current working directory but symbolic
    links are not resolved, so the node sees the path that the controller imported from.
    A module with no ``__file__`` gives an empty ``sys_path``. A task whose ``run`` is a
    command string gives a :class:`~vivarium.dagger.runtime.recipe.CommandPayload` whose
    ``env_path`` is the task's env resolved here, so the node needs no conda and can run
    an env that has no ``bin/python``. Both record the current working directory.

    Parameters
    ----------
    task
        The task to send to a node.
    kwargs
        The arguments pytask passes to the task's function: each input's and output's
        loaded value by name. The reserved ``__code_id__`` and ``__command__`` entries are
        left out of the recipe, and a command task's recipe uses none of them.
    env_paths
        Resolved ``bin`` paths of envs, keyed by the env as the task declares it. A
        task's env found here is not resolved again, and an env that this call resolves
        is added, so one mapping shared by the calls for a whole plan resolves each env
        once (resolving an env by name runs ``conda env list``). ``None`` resolves the
        env on every call.

    Returns
    -------
        The recipe, ready for :func:`json.dumps`.

    Raises
    ------
    ValueError
        If ``task`` cannot run on a node: its ``run`` is a callable other than a
        ``PythonStepRun``, its target cannot be imported by reference (defined in
        ``__main__`` or a notebook, a lambda, a nested function, a ``functools.partial``),
        an argument cannot be encoded, or its env does not resolve. The message names the
        step, the argument when there is one, and the reason.
    """
    refused = f"Step {task.name!r}: cannot run on a compute node"
    run = task.run
    if isinstance(run, str):
        env_path: str | None = None
        if task.env is not None and env_paths is not None and task.env in env_paths:
            env_path = env_paths[task.env]
        elif task.env is not None:
            try:
                bin_path = resolve_env_bin_path(resolve_env_prefix(task.env))
            except RuntimeError as error:
                raise ValueError(
                    f"{refused} because its env does not resolve. {error}"
                ) from error
            if env_paths is not None:
                env_paths[task.env] = bin_path
            env_path = bin_path
        return CommandPayload(
            kind="command",
            task=task.name,
            cwd=os.getcwd(),
            command=run,
            env_path=env_path,
        )
    if not isinstance(run, PythonStepRun):
        label = getattr(run, "__qualname__", repr(run))
        raise ValueError(
            f"{refused} because its run, {label}, was not built by python_step. Only a "
            "python_step run or a command string can be sent to a node."
        )
    try:
        target = target_reference(run.target)
    except ValueError as error:
        raise ValueError(f"{refused}. {error}") from error
    encoded: dict[str, JSONValue] = {}
    for name, value in kwargs.items():
        if name in (CODE_ID_KEY, COMMAND_KEY):
            continue
        try:
            encoded[name] = encode(value)
        except TypeError as error:
            raise ValueError(
                f"{refused} because its argument {name!r} cannot be encoded. {error}"
            ) from error
    return PythonPayload(
        kind="python",
        task=task.name,
        cwd=os.getcwd(),
        target=target,
        sys_path=_import_roots(target),
        suffix=run.suffix if isinstance(run.suffix, str) else dict(run.suffix),
        kwargs=encoded,
    )


def _import_roots(target: str) -> list[str]:
    """Return the import root of the module that the reference ``target`` names, as a list."""
    name = target.partition(":")[0]
    # target_reference has just imported the module, so this takes it from sys.modules.
    module = importlib.import_module(name)
    file = getattr(module, "__file__", None)
    if not isinstance(file, str):
        return []
    root = Path(file).absolute().parent
    # A package's __init__.py sits in the directory that its own last name component names.
    levels = name.count(".") + (1 if hasattr(module, "__path__") else 0)
    for _ in range(levels):
        root = root.parent
    return [str(root)]
