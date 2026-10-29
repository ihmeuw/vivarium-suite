"""Turn a pipeline step into a JSON recipe that a compute node can run.

The controller calls :func:`payload_for` for each step it sends to a node. A recipe is
plain JSON: a python step crosses as its target's import reference, its product suffix,
and its arguments, and a command step crosses as its command string and the ``bin``
directories of its env, resolved on the controller. No pickled code or data crosses, so
an argument must be JSON or a path, and a step that cannot be named by reference is
refused before anything is sent. :mod:`~vivarium.workflow.bridge.runner` runs a recipe.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any, Literal, TypeAlias, TypedDict

from vivarium.workflow.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.workflow.backend.env import resolve_env_bin_path, resolve_env_prefix
from vivarium.workflow.backend.task import Task
from vivarium.workflow.frontend.python_step import PythonStepRun, target_reference

JSONValue: TypeAlias = (
    "str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]"
)
"""A value that :func:`json.dumps` writes and :func:`json.loads` reads back unchanged."""

PATH_TAG = "__path__"
"""Key of the one-entry JSON object that :func:`encode` writes for a path."""


class PythonPayload(TypedDict):
    """Recipe that rebuilds a python step's run object and calls it with ``kwargs``."""

    kind: Literal["python"]
    """Always ``"python"``."""
    task: str
    """The step's name, copied into the run's result."""
    cwd: str
    """The controller's working directory when the recipe was made; the step runs there."""
    target: str
    """The import reference ``"package.module:qualname"`` of the step's target."""
    suffix: str
    """The product suffix, which names the save format (``.parquet`` or ``.csv``)."""
    kwargs: dict[str, JSONValue]
    """The step's arguments, each encoded by :func:`encode`."""


class CommandPayload(TypedDict):
    """Recipe that runs a command step's shell command with its env first on ``PATH``."""

    kind: Literal["command"]
    """Always ``"command"``."""
    task: str
    """The step's name, copied into the run's result."""
    cwd: str
    """The controller's working directory when the recipe was made; the command runs there."""
    command: str
    """The shell command to run."""
    env_path: str | None
    """Colon-joined ``bin`` directories of the step's env, resolved on the controller, to
    put first on ``PATH``. ``None`` means the step declared no env and runs with the
    node's ``PATH``."""


Payload: TypeAlias = PythonPayload | CommandPayload
"""A recipe for either kind of step."""


def encode(value: object) -> JSONValue:
    """Return ``value`` as JSON data, with each path written as a tagged object.

    A path (any :class:`~pathlib.PurePath`) becomes ``{PATH_TAG: str(path)}``, so a relative
    path stays relative. Strings, numbers, booleans and ``None`` pass through, and lists,
    tuples and dicts are encoded item by item. A tuple becomes a list.

    Parameters
    ----------
    value
        The value to encode.

    Returns
    -------
        Data that :func:`json.dumps` accepts and :func:`decode` turns back into ``value``.

    Raises
    ------
    TypeError
        If ``value`` holds anything else (a set, a dataclass, a NumPy array), a dict key
        that is not a string, or a dict with ``PATH_TAG`` as a key. The message names the
        type or the key.
    """
    if isinstance(value, PurePath):
        return {PATH_TAG: str(value)}
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        encoded: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"Cannot encode the dict key {key!r} of type {type(key).__name__}: "
                    "dict keys must be strings."
                )
            if key == PATH_TAG:
                raise TypeError(
                    f"Cannot encode a dict with the key {PATH_TAG!r}, which "
                    "is reserved for encoded paths."
                )
            encoded[key] = encode(item)
        return encoded
    raise TypeError(
        f"Cannot encode a value of type {type(value).__name__!r}: encode accepts only "
        "str, int, float, bool, None, paths, and lists, tuples and dicts of these."
    )


def decode(value: JSONValue) -> Any:
    """Return the value that :func:`encode` encoded as ``value``.

    A dict whose only key is ``PATH_TAG`` becomes a :class:`~pathlib.Path`; lists and other
    dicts are decoded item by item. A tuple that was encoded comes back as a list.

    Parameters
    ----------
    value
        Data that :func:`encode` returned, possibly after a JSON round trip.

    Returns
    -------
        The decoded value.
    """
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1 and PATH_TAG in value:
            path = value[PATH_TAG]
            if not isinstance(path, str):
                raise TypeError(
                    f"Cannot decode {value!r} as a path: "
                    f"its {PATH_TAG!r} value is not a string."
                )
            return Path(path)
        return {key: decode(item) for key, item in value.items()}
    return value


def payload_for(task: Task, kwargs: Mapping[str, Any]) -> Payload:
    """Return the recipe that runs ``task`` on a compute node.

    A task whose ``run`` is a
    :class:`~vivarium.workflow.frontend.python_step.PythonStepRun` gives a
    :class:`PythonPayload` that names the target by
    :func:`~vivarium.workflow.frontend.python_step.target_reference` and carries the encoded
    ``kwargs``. A task whose ``run`` is a command string gives a :class:`CommandPayload`
    whose ``env_path`` is the task's env resolved here, so the node needs no conda and can
    run an env that has no ``bin/python``. Both record the current working directory.

    Parameters
    ----------
    task
        The task to send to a node.
    kwargs
        The arguments pytask passes to the task's function: each input's and output's
        loaded value by name. The reserved ``__code_id__`` and ``__command__`` entries are
        left out of the recipe, and a command task's recipe uses none of them.

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
    refused = f"Task '{task.name}': cannot run on a compute node"
    run = task.run
    if isinstance(run, str):
        env_path: str | None = None
        if task.env is not None:
            try:
                env_path = resolve_env_bin_path(resolve_env_prefix(task.env))
            except RuntimeError as error:
                raise ValueError(
                    f"{refused} because its env does not resolve. {error}"
                ) from error
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
        suffix=run.suffix,
        kwargs=encoded,
    )
