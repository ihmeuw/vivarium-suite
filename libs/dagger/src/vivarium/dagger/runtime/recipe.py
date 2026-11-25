"""The JSON recipe that a pipeline step crosses to a compute node as.

A recipe is plain JSON: a :class:`PythonPayload` for a python step or a
:class:`CommandPayload` for a command step. :func:`encode` and :func:`decode` carry a
python step's arguments, and the paths among them, across JSON.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any, Literal, TypeAlias, TypedDict

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
    sys_path: list[str]
    """The import root of the target's module, the directory that its dotted name
    imports from, as the only entry; empty when the module has no file. The node runner
    appends it to ``sys.path`` while the step runs, so a step function defined in a
    pipeline definition file that was loaded by path imports on the node without
    ``PYTHONPATH``."""
    suffix: str | dict[str, str]
    """The output suffix, which names the save format (``.parquet`` or ``.csv``), or, for
    a step whose ``outputs`` is a mapping, each output name mapped to its suffix."""
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
