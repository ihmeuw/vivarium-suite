"""Record what each bridge run left behind, so a stopped run can be found and explained.

A build that runs its steps through Jobmon (see :mod:`~vivarium.dagger.bridge.plugin`)
writes one :class:`RunState` to ``<root>/.pytask/jobmon/<run>/state.json``. It writes the
file with the status ``"running"`` as soon as the Jobmon workflow is bound, and again with
the status ``"finished"`` or ``"interrupted"`` once every step of the run has a report. A
build process that is killed with SIGKILL, or that dies in some other way that runs no
Python code, leaves the file at ``"running"``, so the file still gives the run's Jobmon
workflow id.

The file is for people and tools that explain a run. Nothing reads it to decide which
steps to run: the next build plans from pytask's lockfile, so a step that the stopped run
recorded as successful is skipped when its inputs and code are unchanged, and every other
step runs again. The bridge reads the latest state only to warn, before it submits
anything, that the previous run did not finish, or that Jobmon stopped it with an error,
and that its compute-node jobs may still be running.

This module imports only the standard library, so code that reads a run's state does not
import pytask's bridge hooks or Jobmon.
"""

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

RunStatus = Literal["running", "finished", "interrupted"]
"""The status of a bridge run in its state file."""

RUNS_DIR = Path(".pytask", "jobmon")
"""The directory, relative to a build's root, that holds one directory per bridge run."""

STATE_FILE = "state.json"
"""The name of the run-state file in a run directory, ``<root>/.pytask/jobmon/<run>/``."""

RUN_STATE_KEY = "bridge_run"
"""The key in pytask's configuration, ``session.config``, under which a bridge build
leaves the final :class:`RunState` of its run."""

_KEYS = ("status", "workflow_id", "workflow_status", "error", "pending")
"""The keys of the JSON object in a state file, in the order written."""


@dataclass(frozen=True)
class RunState:
    """The state of one bridge run, as its state file holds it."""

    workdir: Path
    """The run directory, which holds the run's recipes, result files and state file."""
    status: RunStatus
    """``"running"`` from the moment the workflow is bound until every step of the run
    has a report; then ``"finished"`` if the Jobmon run returned, also with an error, or
    ``"interrupted"`` if SIGTERM or a ``KeyboardInterrupt`` stopped it."""
    workflow_id: int | None
    """The Jobmon workflow id, or ``None`` if the run stopped before the workflow was
    bound or Jobmon gave no id."""
    workflow_status: str | None
    """The final status code of the Jobmon workflow run, such as ``"D"`` or ``"E"``, or
    ``None`` while the run is running and when Jobmon gave no status, as after an
    interrupt or when the Jobmon run raised."""
    error: str | None
    """The type and message of the error that stopped the Jobmon run, as
    ``"RuntimeError: <message>"``, or ``None`` when no error stopped it."""
    pending: tuple[str, ...]
    """The names of the run's submitted steps that were not recorded as successful, in
    plan order. While the run is running, this is every submitted step."""


def write_run_state(state: RunState) -> None:
    """Write ``state`` to ``state.workdir / STATE_FILE`` so a reader never sees a partial file.

    The state is written as a JSON object with the keys ``status``, ``workflow_id``,
    ``workflow_status``, ``error`` and ``pending`` (a list). The workdir is not written,
    because it is the directory that holds the file. The JSON goes to a temporary file in
    the same directory, which is then moved onto the state file with :func:`os.replace`,
    so the file is either the earlier state or the new one, never a mix.

    Parameters
    ----------
    state
        The state to write. Its workdir must exist.
    """
    content = {
        "status": state.status,
        "workflow_id": state.workflow_id,
        "workflow_status": state.workflow_status,
        "error": state.error,
        "pending": list(state.pending),
    }
    path = state.workdir / STATE_FILE
    temporary = path.with_name(f"{STATE_FILE}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(content), encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


def read_run_state(path: Path) -> RunState:
    """Read the run state that :func:`write_run_state` wrote to ``path``.

    Parameters
    ----------
    path
        A state file, ``<run directory>/state.json``.

    Returns
    -------
        The state, whose workdir is the directory that holds ``path``.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        If the file is not a JSON object with each key that :func:`write_run_state`
        writes, with a value of the right type, and a status that is a
        :data:`RunStatus`. The message names the file.
    """
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        # This covers both text that is not JSON and bytes that are not UTF-8.
        raise ValueError(f"The run state file {path} is not JSON ({error}).") from error
    if not isinstance(data, dict):
        raise ValueError(f"The run state file {path} does not hold a JSON object.")
    problem = _problem(data)
    if problem is not None:
        raise ValueError(f"The run state file {path} is not a run state: {problem}.")
    return RunState(
        workdir=path.parent,
        status=cast(RunStatus, data["status"]),
        workflow_id=data["workflow_id"],
        workflow_status=data["workflow_status"],
        error=data["error"],
        pending=tuple(data["pending"]),
    )


def latest_run_state(root: Path) -> RunState | None:
    """Return the state of the most recent bridge run under a build's root.

    The most recent run is the one whose state file was modified last, among the run
    directories in ``root / RUNS_DIR``. A run directory without a state file is not a
    candidate: its build failed, or Jobmon refused the workflow, before the workflow was
    bound, or that build is still binding. A build interrupted while binding does leave a
    state file, with the status ``"interrupted"`` and no workflow id.

    Parameters
    ----------
    root
        The build's root.

    Returns
    -------
        The latest run's state, or ``None`` if no run directory under ``root`` has a state
        file.

    Raises
    ------
    OSError
        If the runs directory or a state file cannot be listed or read.
    ValueError
        If the latest state file is not a run state, as :func:`read_run_state` raises.
    """
    try:
        directories = list((root / RUNS_DIR).iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return None
    latest: tuple[int, str, Path] | None = None
    for directory in directories:
        path = directory / STATE_FILE
        try:
            modified = path.stat().st_mtime_ns
        except (FileNotFoundError, NotADirectoryError):
            continue
        # The directory name breaks a tie, so the choice does not depend on listing order.
        candidate = (modified, directory.name, path)
        if latest is None or candidate[:2] > latest[:2]:
            latest = candidate
    return None if latest is None else read_run_state(latest[2])


def _problem(data: dict[str, Any]) -> str | None:
    """Return what makes ``data`` not a run state, or None if it is one."""
    missing = [key for key in _KEYS if key not in data]
    if missing:
        return f"it has no {', '.join(repr(key) for key in missing)}"
    if data["status"] not in get_args(RunStatus):
        return f"its status {data['status']!r} is not one of {get_args(RunStatus)}"
    workflow_id = data["workflow_id"]
    if workflow_id is not None and (
        isinstance(workflow_id, bool) or not isinstance(workflow_id, int)
    ):
        return f"its workflow_id {workflow_id!r} is not a whole number or null"
    for key in ("workflow_status", "error"):
        if data[key] is not None and not isinstance(data[key], str):
            return f"its {key} {data[key]!r} is not a string or null"
    pending = data["pending"]
    if not isinstance(pending, list) or not all(isinstance(name, str) for name in pending):
        return f"its pending {pending!r} is not a list of strings"
    return None
