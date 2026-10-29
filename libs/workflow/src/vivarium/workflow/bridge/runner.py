"""Run one step's recipe on a compute node and write its result as JSON.

The node runs ``python -m vivarium.workflow.bridge.runner <payload.json> <result.json>``
(:func:`runner_command` builds this line), which exits 0 when the step succeeds and 1 when
it fails. A python recipe is rebuilt into the same
:class:`~vivarium.workflow.frontend.python_step.PythonStepRun` that a local build calls,
and a command recipe runs through the same backend helper as a local build, so a node runs
exactly the code a local build runs.

Everything written to file descriptors 1 and 2 while the step runs, including the output
of child processes and of C code, goes to two capture files beside the result
(:func:`capture_paths`). The result holds the tail of each. A step that is killed, for
example by Slurm for running out of memory or time, writes no result, but its capture
files keep the output up to the kill.
"""

import argparse
import io
import json
import os
import shlex
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, TextIO, TypedDict, cast

from vivarium.workflow.backend.build import run_command_with_bin_path
from vivarium.workflow.bridge.payload import Payload, PythonPayload, decode
from vivarium.workflow.frontend.python_step import PythonStepRun, resolve_reference

RESULT_SUFFIX = ".result.json"
"""Suffix of a result file's name, which tells it apart from the payload, capture and
temporary files in the same directory."""

OUTPUT_TAIL_BYTES = 64 * 1024
"""Most bytes of each captured stream that a result keeps, counted from the end."""

_MODULE = "vivarium.workflow.bridge.runner"
"""This module's import name; ``__name__`` is ``"__main__"`` when it runs with ``-m``."""


class RunResult(TypedDict):
    """The outcome of running one recipe, as written to the result file."""

    task: str | None
    """The step's name from the payload, or ``None`` if the payload could not be read."""
    success: bool
    """Whether the step ran without raising and, for a command, exited with status 0."""
    exc: str | None
    """The formatted traceback of the failure, or ``None`` on success. The runner's own
    frames and any frame that sets ``__tracebackhide__`` are left out, so a python step's
    traceback starts in its target."""
    exc_type: str | None
    """The failure's exception class as ``"module.QualName"``, or only the name for a
    built-in exception, or ``None`` on success."""
    exc_message: str | None
    """The text of the failure's exception, or ``None`` on success."""
    stdout: str
    """The last :data:`OUTPUT_TAIL_BYTES` bytes written to file descriptor 1, decoded as
    UTF-8 with undecodable bytes replaced."""
    stderr: str
    """The same tail for file descriptor 2."""
    duration: float
    """Wall-clock seconds from reading the payload to the end of the step."""


def runner_command(payload_path: Path, result_path: Path) -> str:
    """Return the shell command that runs the recipe at ``payload_path`` on a node.

    Parameters
    ----------
    payload_path
        The JSON file holding the recipe.
    result_path
        Where the run writes its result; its name should end in :data:`RESULT_SUFFIX`.

    Returns
    -------
        ``<this interpreter> -m vivarium.workflow.bridge.runner <payload> <result>``, with
        each part quoted for a POSIX shell.
    """
    return shlex.join([sys.executable, "-m", _MODULE, str(payload_path), str(result_path)])


def capture_paths(result_path: Path) -> tuple[Path, Path]:
    """Return the files that hold the full standard output and error of a run.

    Parameters
    ----------
    result_path
        The run's result file.

    Returns
    -------
        ``result_path`` with its last suffix replaced by ``.stdout`` and by ``.stderr``,
        in that order.
    """
    return result_path.with_suffix(".stdout"), result_path.with_suffix(".stderr")


def write_result(result: RunResult, path: Path) -> None:
    """Write ``result`` to ``path`` as JSON, so a reader never sees a partial file.

    The JSON is written to ``<path>.<pid>.tmp`` in the same directory and then moved onto
    ``path`` with :func:`os.replace`, replacing any earlier result. Each process uses its
    own temporary file, so two attempts writing the same result do not collide.

    Parameters
    ----------
    result
        The result to write.
    path
        The result file.

    Raises
    ------
    OSError
        If the file cannot be written. Any earlier file at ``path`` is left as it was,
        and the temporary file is removed.
    """
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(result, file)
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


def read_result(path: Path) -> RunResult:
    """Read the result that :func:`write_result` wrote to ``path``.

    Parameters
    ----------
    path
        The result file.

    Returns
    -------
        The result.
    """
    return cast(RunResult, json.loads(path.read_text(encoding="utf-8")))


def main(argv: Sequence[str] | None = None) -> int:
    """Run one recipe, write its result, and return the exit status.

    While the step runs, file descriptors 1 and 2 point at :func:`capture_paths` of the
    result (each run truncates them), and Python's own streams are line-buffered so their
    output keeps its order with a child process's. The runner changes to the payload's
    ``cwd`` and runs a python recipe as
    ``PythonStepRun(resolve_reference(target), suffix)(**decode(kwargs))`` or a command
    recipe through :func:`~vivarium.workflow.backend.build.run_command_with_bin_path`. Any
    exception is a failure, including :class:`SystemExit` or :class:`KeyboardInterrupt`
    from the step, an unreadable payload, and an unknown ``kind``. On a failure the runner
    also writes the captured standard error tail and then the traceback to its own standard
    error, because Jobmon keeps only the end of that stream. A target that cannot be
    imported on the node fails with a note naming the step and the target reference. Any
    earlier result at the result path is removed before the step runs, so a result always
    belongs to the run whose capture files sit beside it. The descriptors, the stream
    buffering and the working directory are restored before it returns, so it can also be
    called in-process.

    Parameters
    ----------
    argv
        ``[<payload.json>, <result.json>]``; ``None`` reads ``sys.argv[1:]``.

    Returns
    -------
        0 if the step succeeded and its result was written; 1 if the step failed, an
        earlier result could not be removed, or the capture files or the result could not
        be written, in which case the reason goes to standard error. A usage error exits
        with status 2.
    """
    parser = argparse.ArgumentParser(
        prog=f"python -m {_MODULE}",
        description="Run one step's recipe and write its result.",
    )
    parser.add_argument("payload", type=Path, help="the JSON recipe to run")
    parser.add_argument("result", type=Path, help="the JSON file to write the result to")
    args = parser.parse_args(argv)
    payload_path: Path = args.payload
    result_path: Path = args.result
    try:
        _remove_earlier_result(result_path)
    except OSError as remove_error:
        _report(f"could not remove the earlier result at {result_path}", remove_error)
        return 1
    try:
        stdout_path, stderr_path = capture_paths(result_path)
        with _captured(stdout_path, stderr_path):
            task, error, duration = _run_payload(payload_path)
    except Exception as capture_error:
        _report(f"could not capture the step's output beside {result_path}", capture_error)
        return 1
    exc = None if error is None else _format_exception(error)
    stderr_tail = ""
    write_error: Exception | None = None
    try:
        stderr_tail = _tail(stderr_path)
        result = RunResult(
            task=task,
            success=error is None,
            exc=exc,
            exc_type=None if error is None else _type_name(type(error)),
            exc_message=None if error is None else str(error),
            stdout=_tail(stdout_path),
            stderr=stderr_tail,
            duration=duration,
        )
        write_result(result, result_path)
    except Exception as caught:
        write_error = caught
    if exc is not None:
        _echo_failure(stderr_tail, exc)
    if write_error is not None:
        _report(f"could not write the result to {result_path}", write_error)
        return 1
    return 0 if exc is None else 1


def _remove_earlier_result(path: Path) -> None:
    """Remove the result file that an earlier run left at ``path``, if there is one."""
    # A directory is not an earlier result; leave it for write_result to report.
    if not path.is_dir():
        path.unlink(missing_ok=True)


def _run_payload(payload_path: Path) -> tuple[str | None, BaseException | None, float]:
    """Run the recipe at ``payload_path``; return its task, error and duration."""
    start = time.monotonic()
    task: str | None = None
    error: BaseException | None = None
    try:
        payload = _read_payload(payload_path)
        task = payload["task"]
        _run_step(payload)
    except BaseException as caught:
        error = caught
    return task, error, time.monotonic() - start


def _read_payload(path: Path) -> Payload:
    """Read the recipe at ``path`` and check that it names its task."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("task"), str):
        raise ValueError(f"The payload {path} is not a JSON object with a 'task' string.")
    return cast(Payload, payload)


def _run_step(payload: Payload) -> None:
    """Run the step that ``payload`` describes from the payload's working directory."""
    if payload["kind"] not in ("python", "command"):
        raise ValueError(
            f"The payload's kind {payload['kind']!r} is not 'python' or 'command'."
        )
    previous = os.getcwd()
    os.chdir(payload["cwd"])
    try:
        if payload["kind"] == "python":
            step = PythonStepRun(_resolve_target(payload), payload["suffix"])
            step(**decode(payload["kwargs"]))
        elif payload["kind"] == "command":
            run_command_with_bin_path(payload["command"], payload["env_path"])
    finally:
        os.chdir(previous)


def _resolve_target(payload: PythonPayload) -> Callable[..., Any]:
    """Import the payload's target, adding a note that names the step if the import fails."""
    try:
        return resolve_reference(payload["target"])
    except (ImportError, AttributeError) as error:
        first_entry = sys.path[0] if sys.path else None
        error.add_note(
            f"The step {payload['task']!r} could not import its target "
            f"{payload['target']!r} on this node. The target's module must be importable "
            "in the node's environment from the node's own sys.path, whose first entry is "
            f"{first_entry!r}."
        )
        raise


@contextmanager
def _captured(stdout_path: Path, stderr_path: Path) -> Iterator[None]:
    """Point fds 1 and 2 and Python's own streams at the two files while the block runs."""
    with open(stdout_path, "wb") as stdout_file, open(stderr_path, "wb") as stderr_file:
        with ExitStack() as restore:
            _redirect("stdout", 1, stdout_file.fileno(), restore)
            _redirect("stderr", 2, stderr_file.fileno(), restore)
            yield


def _redirect(name: str, fd: int, target: int, restore: ExitStack) -> None:
    """Point ``fd`` and ``sys.<name>`` at ``target`` until ``restore`` closes."""
    stream: TextIO | None = getattr(sys, name)
    if stream is not None:
        stream.flush()
    saved = os.dup(fd)
    restore.callback(os.close, saved)
    restore.callback(os.dup2, saved, fd)
    os.dup2(target, fd)
    if stream is not None and _fileno(stream) == fd:
        if isinstance(stream, io.TextIOWrapper):
            restore.callback(stream.reconfigure, line_buffering=stream.line_buffering)
            stream.reconfigure(line_buffering=True)
        restore.callback(stream.flush)
    else:
        # pytest's capture and notebooks replace sys.stdout and sys.stderr with streams
        # that do not write to fds 1 and 2, so the step's prints would miss the files.
        line_buffered = open(fd, "w", buffering=1, encoding="utf-8", closefd=False)
        restore.callback(setattr, sys, name, stream)
        restore.callback(line_buffered.close)
        setattr(sys, name, line_buffered)


def _fileno(stream: TextIO) -> int | None:
    """Return the file descriptor that ``stream`` writes to, or None if it has none."""
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def _tail(path: Path) -> str:
    """Return the last ``OUTPUT_TAIL_BYTES`` bytes of ``path``, decoded as UTF-8."""
    with open(path, "rb") as file:
        size = file.seek(0, os.SEEK_END)
        file.seek(max(0, size - OUTPUT_TAIL_BYTES))
        return file.read().decode("utf-8", errors="replace")


def _format_exception(error: BaseException) -> str:
    """Format ``error`` and the exceptions chained to it without their hidden frames."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = _visible(current.__traceback__)
        for chained in (current.__cause__, current.__context__):
            if chained is not None:
                pending.append(chained)
    return "".join(traceback.format_exception(error))


def _visible(tb: TracebackType | None) -> TracebackType | None:
    """Return ``tb`` without the frames that :func:`_hidden` selects."""
    kept: list[TracebackType] = []
    while tb is not None:
        if not _hidden(tb.tb_frame):
            kept.append(tb)
        tb = tb.tb_next
    visible: TracebackType | None = None
    for entry in reversed(kept):
        visible = TracebackType(visible, entry.tb_frame, entry.tb_lasti, entry.tb_lineno)
    return visible


def _hidden(frame: FrameType) -> bool:
    """Check whether ``frame`` is the runner's own or sets ``__tracebackhide__``."""
    return frame.f_globals is globals() or bool(frame.f_locals.get("__tracebackhide__"))


def _type_name(cls: type[BaseException]) -> str:
    """Return ``cls`` as ``"module.QualName"``, or only its name for a built-in."""
    if cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def _echo_failure(stderr_tail: str, exc: str) -> None:
    """Write the captured standard error tail and then the traceback to the real stderr."""
    if stderr_tail and not stderr_tail.endswith("\n"):
        stderr_tail += "\n"
    sys.stderr.write(stderr_tail + exc)
    sys.stderr.flush()


def _report(problem: str, error: Exception) -> None:
    """Write ``problem`` and the error behind it to this process's standard error."""
    print(
        f"{_MODULE}: {problem} ({type(error).__name__}: {error}).",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
