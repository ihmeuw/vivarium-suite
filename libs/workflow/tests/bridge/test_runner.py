"""Tests for the node-side runner (``bridge.runner``)."""

import inspect
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
from pytask import TaskOutcome

from tests.bridge import step_targets
from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.task import Task
from vivarium.workflow.bridge.payload import (
    PATH_TAG,
    CommandPayload,
    JSONValue,
    PythonPayload,
    payload_for,
)
from vivarium.workflow.bridge.runner import (
    OUTPUT_TAIL_BYTES,
    RESULT_SUFFIX,
    RunResult,
    capture_paths,
    main,
    read_result,
    runner_command,
    write_result,
)
from vivarium.workflow.frontend.python_step import target_reference
from vivarium.workflow.harness import build_fixture

_WORKFLOW_DIR = Path(__file__).resolve().parents[2]
"""The ``libs/workflow`` directory, from which a subprocess imports ``tests.bridge``."""

_RUNNER_MODULE = "vivarium.workflow.bridge.runner"
"""The module that ``python -m`` runs on a node."""

_RESULT_NAME = f"step{RESULT_SUFFIX}"
"""File name of the result that most tests have the runner write."""

_TIMEOUT = 300
"""Seconds after which a runner subprocess is taken to hang."""

_RESULT_KEYS = {
    "task",
    "success",
    "exc",
    "exc_type",
    "exc_message",
    "stdout",
    "stderr",
    "duration",
}
"""The keys of a RunResult."""

_IMPORT_RUNNER = """
import sys

import vivarium.workflow.bridge.runner

loaded = sorted(name for name in sys.modules if name.partition(".")[0] == "jobmon")
assert not loaded, loaded
"""
"""Script that imports the runner and fails if that imported any part of jobmon."""

_SUCCESS: RunResult = {
    "task": "mid.parquet",
    "success": True,
    "exc": None,
    "exc_type": None,
    "exc_message": None,
    "stdout": "wrote mid.parquet\n",
    "stderr": "",
    "duration": 1.25,
}
"""A result for a step that succeeded."""

_FAILURE: RunResult = {
    "task": None,
    "success": False,
    "exc": "Traceback (most recent call last):\nValueError: bad � input\n",
    "exc_type": "ValueError",
    "exc_message": "bad � input",
    "stdout": "",
    "stderr": "partial � output\n",
    "duration": 0.5,
}
"""A result for a payload that could not be read, holding replacement characters."""


def _mkdir(path: Path) -> Path:
    """Create ``path`` and its parents if missing, and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tag(path: Path) -> dict[str, JSONValue]:
    """Return ``path`` as the payload codec writes it."""
    return {PATH_TAG: str(path)}


def _python_payload(task: str, cwd: Path, target: str) -> PythonPayload:
    """Return a recipe that runs ``target`` in ``cwd`` and saves its frame under ``cwd``."""
    return {
        "kind": "python",
        "task": task,
        "cwd": str(cwd),
        "target": target,
        "suffix": ".parquet",
        "kwargs": {"product": _tag(cwd / "out" / f"{task}.parquet")},
    }


def _command_payload(
    task: str, cwd: Path, command: str, env_path: str | None = None
) -> CommandPayload:
    """Return a recipe that runs ``command`` in ``cwd`` with ``env_path`` first on PATH."""
    return {
        "kind": "command",
        "task": task,
        "cwd": str(cwd),
        "command": command,
        "env_path": env_path,
    }


def _write_payload(path: Path, payload: Mapping[str, object]) -> Path:
    """Write ``payload`` to ``path`` as JSON and return the path."""
    path.write_text(json.dumps(payload))
    return path


def _subprocess_env() -> dict[str, str]:
    """Return this process's environment with ``_WORKFLOW_DIR`` first on PYTHONPATH.

    PYTHONUNBUFFERED is removed, so it cannot hide a runner that does not line-buffer
    Python's streams.
    """
    env = dict(os.environ)
    env.pop("PYTHONUNBUFFERED", None)
    paths = [str(_WORKFLOW_DIR), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    return env


def _run_runner(
    payload: Path, result: Path, cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run :func:`runner_command` for ``payload`` in a subprocess started in ``cwd``."""
    return subprocess.run(
        shlex.split(runner_command(payload, result)),
        cwd=_mkdir(cwd),
        env=_subprocess_env() if env is None else env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=_TIMEOUT,
    )


def _read(path: Path) -> RunResult:
    """Read the result at ``path``, checking it is plain JSON with each RunResult key."""
    result = read_result(path)
    assert set(result) == _RESULT_KEYS
    assert json.loads(path.read_text()) == result
    return result


def _run_step(
    tmp_path: Path, payload: Mapping[str, object]
) -> tuple[subprocess.CompletedProcess[str], RunResult]:
    """Run ``payload`` in a runner subprocess and return the process and its result."""
    payload_path = _write_payload(tmp_path / "step.payload.json", payload)
    result_path = tmp_path / _RESULT_NAME
    process = _run_runner(payload_path, result_path, tmp_path / "elsewhere")
    assert result_path.exists(), process.stderr
    return process, _read(result_path)


def _numbered(text: str) -> list[str]:
    """Return the lines of ``text`` that ``step_targets.write_to_every_stream`` wrote."""
    return [line for line in text.splitlines() if line.startswith("capture-")]


def _identity(fd: int) -> tuple[int, int]:
    """Return the device and inode of the file that ``fd`` points at."""
    status = os.fstat(fd)
    return status.st_dev, status.st_ino


def test_runner_runs_the_harness_chain_with_products_equal_to_a_local_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """End to end: payload_for each harness task, run runner_command for each in topological order in a subprocess started from another directory; each exits 0 with success=True, and both products equal a local build()'s."""
    local = build_fixture(_mkdir(tmp_path / "local"))
    ran = dict.fromkeys(local.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(local.tasks, local.state)) == ran
    monkeypatch.chdir(_mkdir(tmp_path / "controller"))
    remote, work = build_fixture(_mkdir(tmp_path / "remote")), _mkdir(tmp_path / "work")
    runs: list[tuple[str, Path, Path]] = []
    for index, task in enumerate(remote.tasks):
        recipe = payload_for(task, pytask_kwargs(task))
        payload = _write_payload(work / f"{index}.payload.json", recipe)
        runs.append((task.name, payload, work / f"{index}{RESULT_SUFFIX}"))

    for name, payload, result_path in runs:
        process = _run_runner(payload, result_path, tmp_path / "elsewhere")
        assert process.returncode == 0, process.stderr
        result = _read(result_path)
        assert (result["task"], result["success"]) == (name, True), result["exc"]

    for step in local.steps:
        produced, expected = remote.registry.path_for(step), local.registry.path_for(step)
        pd.testing.assert_frame_equal(pd.read_parquet(produced), pd.read_parquet(expected))


def test_runner_runs_a_command_step_from_its_bin_path_in_the_payload_cwd(
    tmp_path: Path, probe_tool: str, make_tool_dir: Callable[[Path], Path]
) -> None:
    """A command payload whose env_path is a fake prefix's bin runs the tool found only there, `pwd` prints the payload's cwd, and the result has success=True and the tool's output in stdout."""
    bin_dir = make_tool_dir(tmp_path / "env" / "bin")
    step_cwd = _mkdir(tmp_path / "step_cwd").resolve()
    payload = _command_payload(
        "tool_step", step_cwd, f"{probe_tool} hello && pwd", str(bin_dir)
    )

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 0, process.stderr
    assert (result["task"], result["success"]) == ("tool_step", True)
    assert result["stdout"] == f"{probe_tool} says hello\n{step_cwd}\n"


def test_runner_runs_a_command_step_whose_env_has_no_bin_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_tool: str,
    fake_conda: Callable[..., Path],
    make_tool_dir: Callable[[Path], Path],
    pytask_kwargs: Callable[[Task], dict[str, Any]],
) -> None:
    """End to end: an env named by a fake conda, whose prefix has bin/<tool> but no bin/python, goes through payload_for and the runner subprocess, and the tool runs from that env."""
    monkeypatch.chdir(_mkdir(tmp_path / "controller"))
    prefix = tmp_path / "conda_envs" / "r_only"
    make_tool_dir(prefix / "bin")
    fake_conda(prefix)
    task = Task(
        name="r_step",
        run=f"{probe_tool} from-r-env",
        inputs={},
        outputs={"report": file_node(tmp_path / "report.html")},
        resources={},
        env="r_only",
    )
    recipe = payload_for(task, pytask_kwargs(task))
    payload = _write_payload(tmp_path / "r_step.payload.json", recipe)
    result_path = tmp_path / _RESULT_NAME
    node_env = _subprocess_env()
    del node_env["CONDA_EXE"]

    process = _run_runner(payload, result_path, tmp_path / "node", node_env)

    assert process.returncode == 0, process.stderr
    result = _read(result_path)
    assert (result["task"], result["success"]) == ("r_step", True)
    assert result["stdout"] == f"{probe_tool} says from-r-env\n"
    assert not (prefix / "bin" / "python").exists()


def test_runner_records_a_raising_target_as_a_failure(tmp_path: Path) -> None:
    """A python step whose target raises gives exit status 1 and a result with success=False, exc holding the traceback, exc_type and exc_message of the exception, and what the target wrote to stderr; the process's own stderr ends with the traceback."""
    target = target_reference(step_targets.raise_step_failure)

    process, result = _run_step(tmp_path, _python_payload("failing_step", tmp_path, target))

    assert process.returncode == 1
    assert (result["task"], result["success"]) == ("failing_step", False)
    assert result["exc_type"] == f"{step_targets.__name__}.StepFailure"
    assert result["exc_message"] == step_targets.FAILURE_MESSAGE
    exc = result["exc"]
    assert exc is not None
    assert "Traceback (most recent call last):" in exc
    assert "raise_step_failure" in exc
    assert exc.rstrip().endswith(f"StepFailure: {step_targets.FAILURE_MESSAGE}")
    assert step_targets.STDERR_BEFORE_FAILURE in result["stderr"]
    assert process.stderr.rstrip().endswith(exc.rstrip())


def test_runner_traceback_leaves_out_the_runner_and_hidden_frames(tmp_path: Path) -> None:
    """The exc of a raising python step starts at the target's frame: it names the target's file and line, and holds no frame from runner.py or from PythonStepRun.__call__."""
    source, first = inspect.getsourcelines(step_targets.raise_step_failure)
    offset = next(i for i, text in enumerate(source) if text.lstrip().startswith("raise "))
    target = target_reference(step_targets.raise_step_failure)

    _, result = _run_step(tmp_path, _python_payload("failing_step", tmp_path, target))

    exc = result["exc"]
    assert exc is not None
    frames = re.findall(r'File "([^"]+)", line (\d+), in (\S+)', exc)
    assert frames, exc
    filename, line, function = frames[0]
    assert Path(filename).name == "step_targets.py", exc
    assert int(line) == first + offset, exc
    assert function == "raise_step_failure", exc
    hidden = {"runner.py", "python_step.py"}
    assert not [name for name, _, _ in frames if Path(name).name in hidden], exc


def test_runner_records_a_nonzero_command_exit_as_a_failure(tmp_path: Path) -> None:
    """A command payload running `echo oops >&2; exit 3` gives exit status 1 and a result with success=False, an exc and exc_message naming exit status 3, and 'oops' in stderr."""
    payload = _command_payload("failing_command", tmp_path, "echo oops >&2; exit 3")

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 1
    assert (result["task"], result["success"]) == ("failing_command", False)
    exc, message = result["exc"], result["exc_message"]
    assert exc is not None and "status 3" in exc, exc
    assert message is not None and "status 3" in message, message
    assert "oops" in result["stderr"]


def test_runner_captures_output_at_the_file_descriptor_level(tmp_path: Path) -> None:
    """Python prints to stdout and stderr, os.write to fd 2, and a child process writing to both streams all reach the result's stdout and stderr, in the order written within each stream."""
    target = target_reference(step_targets.write_to_every_stream)

    process, result = _run_step(tmp_path, _python_payload("noisy_step", tmp_path, target))

    assert process.returncode == 0, process.stderr
    assert result["success"] is True, result["exc"]
    assert _numbered(result["stdout"]) == ["capture-out-1", "capture-out-2", "capture-out-3"]
    assert _numbered(result["stderr"]) == [
        "capture-err-1",
        "capture-err-2",
        "capture-err-3",
        "capture-err-4",
    ]


def test_runner_keeps_the_tail_of_long_output_and_the_full_streams_in_capture_files(
    tmp_path: Path,
) -> None:
    """Output longer than OUTPUT_TAIL_BYTES leaves exactly its last OUTPUT_TAIL_BYTES in the result, and the whole stream in capture_paths(result)."""
    data = b"".join(b"%07d\n" % number for number in range(30_000))
    source = tmp_path / "long.txt"
    source.write_bytes(data)
    quoted = shlex.quote(str(source))
    payload = _command_payload("long_step", tmp_path, f"cat {quoted}; cat {quoted} >&2")

    process, result = _run_step(tmp_path, payload)

    assert len(data) > OUTPUT_TAIL_BYTES
    assert process.returncode == 0, process.stderr
    tail = data[-OUTPUT_TAIL_BYTES:].decode()
    assert result["stdout"] == tail
    assert result["stderr"] == tail
    stdout, stderr = capture_paths(tmp_path / _RESULT_NAME)
    assert stdout.read_bytes() == data
    assert stderr.read_bytes() == data


def test_runner_decodes_bytes_that_are_not_utf8_with_replacement_characters(
    tmp_path: Path,
) -> None:
    """A step that writes invalid UTF-8 bytes still gets a result, with U+FFFD in place of the bad bytes."""
    source = tmp_path / "bytes.bin"
    source.write_bytes(b"before \xff\xfe after\n")
    quoted = shlex.quote(str(source))
    payload = _command_payload("bytes_step", tmp_path, f"cat {quoted}; cat {quoted} >&2")

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 0, process.stderr
    assert result["success"] is True, result["exc"]
    assert result["stdout"] == "before �� after\n"
    assert result["stderr"] == "before �� after\n"


@pytest.mark.parametrize(
    "text, error",
    [(None, "FileNotFoundError"), ("{not json", "JSONDecodeError")],
    ids=["missing", "malformed"],
)
def test_runner_records_a_missing_or_malformed_payload_with_no_task_name(
    tmp_path: Path, text: str | None, error: str
) -> None:
    """A payload file that does not exist, or holds text that is not JSON, gives exit status 1 and a result with task None and success=False naming the error."""
    payload = tmp_path / "step.payload.json"
    if text is not None:
        payload.write_text(text)
    result_path = tmp_path / _RESULT_NAME

    process = _run_runner(payload, result_path, tmp_path / "elsewhere")

    assert result_path.exists(), process.stderr
    result = _read(result_path)
    assert process.returncode == 1
    assert (result["task"], result["success"]) == (None, False)
    exc_type = result["exc_type"]
    assert exc_type is not None and exc_type.endswith(error), result["exc"]


def test_runner_records_an_unknown_payload_kind_as_a_failure_naming_it(
    tmp_path: Path,
) -> None:
    """A payload whose kind is neither 'python' nor 'command' gives exit status 1, success=False, the payload's task name, and an exc_message naming the kind."""
    payload = {"kind": "notebook", "task": "mystery_step", "cwd": str(tmp_path)}

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 1
    assert (result["task"], result["success"]) == ("mystery_step", False)
    message = result["exc_message"]
    assert message is not None and "notebook" in message, result["exc"]


def test_runner_records_a_target_module_missing_on_the_node_as_a_failure(
    tmp_path: Path,
) -> None:
    """A python payload whose target names a module that cannot be imported gives exit status 1, success=False, and exc_type ModuleNotFoundError."""
    payload = _python_payload("orphan_step", tmp_path, "no_such_module_xyz_vw:make_table")

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 1
    assert (result["task"], result["success"]) == ("orphan_step", False)
    assert result["exc_type"] == "ModuleNotFoundError", result["exc"]


@pytest.mark.parametrize(
    "target, exc_type",
    [
        (step_targets.exit_with_status_zero, "SystemExit"),
        (step_targets.raise_keyboard_interrupt, "KeyboardInterrupt"),
    ],
    ids=["sys_exit_0", "keyboard_interrupt"],
)
def test_runner_records_system_exit_and_keyboard_interrupt_from_a_step_as_failures(
    tmp_path: Path, target: Callable[..., Any], exc_type: str
) -> None:
    """A target that calls sys.exit(0), or raises KeyboardInterrupt, gives exit status 1 and success=False with exc_type SystemExit or KeyboardInterrupt."""
    payload = _python_payload("stopping_step", tmp_path, target_reference(target))

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 1
    assert (result["task"], result["success"]) == ("stopping_step", False)
    assert result["exc_type"] == exc_type, result["exc"]


def test_runner_reports_on_stderr_when_the_result_directory_does_not_exist(
    tmp_path: Path,
) -> None:
    """A result path in a missing directory gives exit status 1, no result file, and a message on the process's stderr."""
    recipe = _command_payload("quiet_step", tmp_path, "true")
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = tmp_path / "missing_dir" / _RESULT_NAME

    process = _run_runner(payload, result_path, tmp_path / "elsewhere")

    assert process.returncode == 1
    assert not result_path.exists()
    assert "missing_dir" in process.stderr


def test_runner_exits_with_status_two_on_a_usage_error(tmp_path: Path) -> None:
    """Running the runner module with one argument instead of two exits with status 2."""
    process = subprocess.run(
        [sys.executable, "-m", _RUNNER_MODULE, str(tmp_path / "step.payload.json")],
        cwd=_mkdir(tmp_path / "elsewhere"),
        env=_subprocess_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )

    assert process.returncode == 2, process.stderr


def test_runner_module_runs_with_python_m_without_jobmon_or_a_runpy_warning(
    tmp_path: Path,
) -> None:
    """`python -W error -m vivarium.workflow.bridge.runner` runs a payload with no RuntimeWarning, and 'jobmon' is not in sys.modules after importing the runner."""
    recipe = _command_payload("echo_step", tmp_path, "echo ran")
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = tmp_path / _RESULT_NAME
    arguments = [str(payload), str(result_path)]

    process = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", _RUNNER_MODULE, *arguments],
        cwd=_mkdir(tmp_path / "elsewhere"),
        env=_subprocess_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    imported = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_RUNNER],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )

    assert process.returncode == 0, process.stderr
    assert "RuntimeWarning" not in process.stderr
    assert _read(result_path)["stdout"] == "ran\n"
    assert imported.returncode == 0, imported.stderr


def test_main_called_in_process_restores_descriptors_and_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """main() called in this process under pytest's capture returns 0, captures the step's output in the result, and leaves fds 1 and 2, sys.stdout's buffering, and the working directory as they were; it does the same when sys.stdout and sys.stderr write to fds 1 and 2 directly, where main sets and then restores their line buffering."""
    monkeypatch.chdir(_mkdir(tmp_path / "start"))
    target = target_reference(step_targets.write_to_streams_and_descriptors)
    step_cwd = _mkdir(tmp_path / "step_cwd")
    recipe = _python_payload("in_process_step", step_cwd, target)
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = tmp_path / _RESULT_NAME
    cwd, stdout, stderr = os.getcwd(), sys.stdout, sys.stderr
    buffering = (sys.stdout.line_buffering, sys.stderr.line_buffering)
    descriptors = (_identity(1), _identity(2))

    status = main([str(payload), str(result_path)])

    os.write(1, b"fd 1 after main\n")
    os.write(2, b"fd 2 after main\n")
    captured = capfd.readouterr()
    result = _read(result_path)
    assert status == 0, result["exc"]
    assert step_targets.FD1_OUTPUT in result["stdout"]
    assert step_targets.FD2_OUTPUT in result["stderr"]
    assert step_targets.STDOUT_PRINT in result["stdout"]
    assert step_targets.STDERR_PRINT in result["stderr"]
    assert os.getcwd() == cwd
    assert (_identity(1), _identity(2)) == descriptors
    assert sys.stdout is stdout
    assert sys.stderr is stderr
    assert (sys.stdout.line_buffering, sys.stderr.line_buffering) == buffering
    assert "fd 1 after main" in captured.out
    assert "fd 2 after main" in captured.err
    assert step_targets.FD1_OUTPUT not in captured.out
    assert step_targets.FD2_OUTPUT not in captured.err
    assert step_targets.STDOUT_PRINT not in captured.out
    assert step_targets.STDERR_PRINT not in captured.err

    fd_recipe = _python_payload("fd_stream_step", step_cwd, target)
    fd_payload = _write_payload(tmp_path / "fd_stream.payload.json", fd_recipe)
    fd_result_path = tmp_path / f"fd_stream{RESULT_SUFFIX}"
    # Undo the patch in the test body. The monkeypatch fixture's own undo runs after capfd
    # has closed its streams, and would put those closed streams back on sys.
    with ExitStack() as stack:
        fd_stdout = stack.enter_context(open(1, "w", encoding="utf-8", closefd=False))
        fd_stderr = stack.enter_context(open(2, "w", encoding="utf-8", closefd=False))
        patch = stack.enter_context(monkeypatch.context())
        patch.setattr(sys, "stdout", fd_stdout)
        patch.setattr(sys, "stderr", fd_stderr)

        fd_status = main([str(fd_payload), str(fd_result_path)])

        fd_streams = (sys.stdout, sys.stderr)
        fd_buffering = (fd_stdout.line_buffering, fd_stderr.line_buffering)
        fd_descriptors = (_identity(1), _identity(2))
        fd_stdout.flush()
        fd_stderr.flush()
        fd_captured = capfd.readouterr()
    fd_result = _read(fd_result_path)
    assert fd_status == 0, fd_result["exc"]
    assert step_targets.STDOUT_PRINT in fd_result["stdout"]
    assert step_targets.FD1_OUTPUT in fd_result["stdout"]
    assert step_targets.STDERR_PRINT in fd_result["stderr"]
    assert step_targets.FD2_OUTPUT in fd_result["stderr"]
    assert fd_streams[0] is fd_stdout
    assert fd_streams[1] is fd_stderr
    assert fd_buffering == (False, False)
    assert fd_descriptors == descriptors
    assert step_targets.STDOUT_PRINT not in fd_captured.out
    assert step_targets.STDERR_PRINT not in fd_captured.err


def test_a_second_run_replaces_the_first_result_and_its_capture_files(
    tmp_path: Path,
) -> None:
    """Running a failing payload and then a succeeding one onto the same result path leaves only the second result, capture files holding only the second run's output, and no *.tmp file."""
    first = _command_payload("first_step", tmp_path, "echo first; echo first >&2; exit 1")
    second = _command_payload("second_step", tmp_path, "echo second; echo second >&2")
    result_path = tmp_path / _RESULT_NAME
    statuses: list[int] = []
    for name, recipe in (("first", first), ("second", second)):
        payload = _write_payload(tmp_path / f"{name}.payload.json", recipe)
        statuses.append(_run_runner(payload, result_path, tmp_path / "elsewhere").returncode)

    result = _read(result_path)
    stdout, stderr = capture_paths(result_path)
    assert statuses == [1, 0]
    assert (result["task"], result["success"]) == ("second_step", True)
    assert (result["stdout"], result["stderr"]) == ("second\n", "second\n")
    assert stdout.read_text() == "second\n"
    assert stderr.read_text() == "second\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_result_leaves_no_file_when_writing_fails(tmp_path: Path) -> None:
    """With JSON encoding made to fail part way, write_result raises, no file exists at the result path, an earlier result there is unchanged, and no temporary file is left."""
    broken: dict[str, object] = {**_SUCCESS, "stdout": "x" * 100_000, "duration": object()}
    fresh, earlier = _mkdir(tmp_path / "fresh"), _mkdir(tmp_path / "earlier")
    write_result(_FAILURE, earlier / _RESULT_NAME)
    before = (earlier / _RESULT_NAME).read_bytes()

    for directory in (fresh, earlier):
        with pytest.raises(TypeError):
            write_result(cast(RunResult, broken), directory / _RESULT_NAME)

    assert list(fresh.iterdir()) == []
    assert [path.name for path in earlier.iterdir()] == [_RESULT_NAME]
    assert (earlier / _RESULT_NAME).read_bytes() == before


def test_write_result_writes_through_a_per_process_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_result moves '<path>.<pid>.tmp' from the result's directory onto the result path, and leaves no temporary file after it succeeds."""
    path = tmp_path / _RESULT_NAME
    moves: list[tuple[Path, Path, Any]] = []
    replace = os.replace

    def _recording_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        """Record each move and the JSON it carries, then make it."""
        moved = Path(os.fspath(src))
        moves.append((moved, Path(os.fspath(dst)), json.loads(moved.read_text())))
        replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", _recording_replace)

    write_result(_SUCCESS, path)

    temporary = Path(f"{path}.{os.getpid()}.tmp")
    assert [move for move in moves if move[1] == path] == [(temporary, path, _SUCCESS)]
    assert read_result(path) == _SUCCESS
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("result", [_SUCCESS, _FAILURE], ids=["success", "failure"])
def test_read_result_returns_what_write_result_wrote(
    tmp_path: Path, result: RunResult
) -> None:
    """read_result of a path that write_result wrote returns an equal RunResult."""
    path = tmp_path / _RESULT_NAME

    write_result(result, path)

    assert read_result(path) == result


def test_runner_command_uses_this_interpreter_and_quotes_paths(tmp_path: Path) -> None:
    """runner_command starts with sys.executable and '-m vivarium.workflow.bridge.runner', and a payload and result path containing spaces survive shlex.split unchanged."""
    directory = tmp_path / "with space" / "it's here"
    payload, result = directory / "a payload.json", directory / f"a result{RESULT_SUFFIX}"

    command = runner_command(payload, result)

    assert shlex.split(command) == [
        sys.executable,
        "-m",
        _RUNNER_MODULE,
        str(payload),
        str(result),
    ]


def test_capture_paths_sit_beside_the_result(tmp_path: Path) -> None:
    """capture_paths('<dir>/x.result.json') is ('<dir>/x.result.stdout', '<dir>/x.result.stderr'), neither ends in RESULT_SUFFIX."""
    stdout, stderr = capture_paths(tmp_path / "x.result.json")

    assert (stdout, stderr) == (tmp_path / "x.result.stdout", tmp_path / "x.result.stderr")
    assert not stdout.name.endswith(RESULT_SUFFIX)
    assert not stderr.name.endswith(RESULT_SUFFIX)


@pytest.mark.parametrize("kind", ["python", "command"])
def test_runner_echoes_the_captured_stderr_and_then_the_traceback_on_failure(
    tmp_path: Path, kind: str
) -> None:
    """On a failure the process's own stderr holds what the step wrote to stderr, followed by the traceback, so Jobmon's stderr tail shows both."""
    if kind == "python":
        target = target_reference(step_targets.raise_step_failure)
        payload: Mapping[str, object] = _python_payload("failing_step", tmp_path, target)
        written = step_targets.STDERR_BEFORE_FAILURE
    else:
        written = "the command wrote this to stderr"
        command = f"echo '{written}' >&2; exit 3"
        payload = _command_payload("failing_step", tmp_path, command)

    process, result = _run_step(tmp_path, payload)

    exc = result["exc"]
    assert process.returncode == 1
    assert exc is not None
    assert written in result["stderr"]
    echoed = process.stderr.rstrip()
    assert echoed.endswith(exc.rstrip()), echoed
    assert written in echoed.removesuffix(exc.rstrip()), echoed


def test_a_killed_step_leaves_no_result_and_keeps_its_output_in_the_capture_files(
    tmp_path: Path,
) -> None:
    """A command step that prints a line and then kills the runner process (SIGKILL) leaves no result file and no temporary file, and the line is in the stdout capture file."""
    recipe = _command_payload("killed_step", tmp_path, "echo before-kill; kill -9 $PPID")
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = tmp_path / _RESULT_NAME

    process = _run_runner(payload, result_path, tmp_path / "elsewhere")

    stdout, _ = capture_paths(result_path)
    assert process.returncode != 0
    assert not result_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    assert stdout.read_text() == "before-kill\n"


def test_a_successful_result_has_no_exception_fields_and_a_duration(tmp_path: Path) -> None:
    """A step that succeeds gives exc, exc_type and exc_message None and a non-negative float duration no longer than the subprocess's wall time."""
    recipe = _command_payload("timed_step", tmp_path, "echo done")
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = tmp_path / _RESULT_NAME

    started = time.monotonic()
    process = _run_runner(payload, result_path, tmp_path / "elsewhere")
    elapsed = time.monotonic() - started

    assert process.returncode == 0, process.stderr
    result = _read(result_path)
    assert result["success"] is True
    assert (result["exc"], result["exc_type"], result["exc_message"]) == (None, None, None)
    duration = result["duration"]
    assert isinstance(duration, float)
    assert 0 <= duration <= elapsed


def test_runner_runs_a_python_step_in_the_payload_cwd(tmp_path: Path) -> None:
    """A python payload whose input and product path tags are relative, run by a runner started in another directory, reads its input from and writes its product under the payload's cwd, with success=True."""
    step_cwd = _mkdir(tmp_path / "step_cwd")
    pd.DataFrame({"n": [1, 2, 3]}).to_parquet(step_cwd / "raw.parquet")
    payload = _python_payload("relative_step", step_cwd, "vivarium.workflow.harness:_double")
    payload["kwargs"] = {
        "raw": _tag(Path("raw.parquet")),
        "product": _tag(Path("out", "mid.parquet")),
    }

    process, result = _run_step(tmp_path, payload)

    assert process.returncode == 0, process.stderr
    assert (result["task"], result["success"]) == ("relative_step", True), result["exc"]
    assert pd.read_parquet(step_cwd / "out" / "mid.parquet")["n"].tolist() == [2, 4, 6]
    assert not (tmp_path / "elsewhere" / "out").exists()


def test_runner_exits_with_status_one_when_a_succeeding_step_result_cannot_be_written(
    tmp_path: Path,
) -> None:
    """A command step that succeeds but whose result path is a directory gives exit status 1 after the step has run, a message on the process's stderr saying the result could not be written, and no temporary file."""
    recipe = _command_payload("echo_step", tmp_path, "echo ran")
    payload = _write_payload(tmp_path / "step.payload.json", recipe)
    result_path = _mkdir(tmp_path / _RESULT_NAME)

    process = _run_runner(payload, result_path, tmp_path / "elsewhere")

    stdout, _ = capture_paths(result_path)
    assert process.returncode == 1
    assert stdout.read_text() == "ran\n"
    assert "could not write the result" in process.stderr, process.stderr
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_killed_rerun_leaves_no_earlier_result_behind(tmp_path: Path) -> None:
    """A run that succeeds and then a run onto the same result path that is killed (SIGKILL) leave no result file, so the result never describes a different run than the capture files."""
    first = _command_payload("first_step", tmp_path, "echo first")
    killed = _command_payload("killed_step", tmp_path, "echo second; kill -9 $PPID")
    first_payload = _write_payload(tmp_path / "first.payload.json", first)
    killed_payload = _write_payload(tmp_path / "killed.payload.json", killed)
    result_path = tmp_path / _RESULT_NAME
    completed = _run_runner(first_payload, result_path, tmp_path / "elsewhere")
    assert completed.returncode == 0, completed.stderr
    assert _read(result_path)["success"] is True

    process = _run_runner(killed_payload, result_path, tmp_path / "elsewhere")

    stdout, _ = capture_paths(result_path)
    assert process.returncode != 0
    assert not result_path.exists()
    assert stdout.read_text() == "second\n"


@pytest.mark.parametrize(
    "reference, exc_type",
    [
        ("no_such_module_xyz_vw:make_table", "ModuleNotFoundError"),
        ("vivarium.workflow.harness:no_such_function", "AttributeError"),
    ],
    ids=["missing_module", "missing_attribute"],
)
def test_a_target_that_cannot_be_imported_on_the_node_is_named_in_the_traceback(
    tmp_path: Path, reference: str, exc_type: str
) -> None:
    """A python payload whose target cannot be imported (a missing module, or a module without the named attribute) gives an exc that names the step and the target reference, while exc_type stays ModuleNotFoundError or AttributeError."""
    task = "unimportable_target_probe"

    process, result = _run_step(tmp_path, _python_payload(task, tmp_path, reference))

    exc = result["exc"]
    assert process.returncode == 1
    assert (result["task"], result["success"]) == (task, False)
    assert result["exc_type"] == exc_type, exc
    assert exc is not None
    assert task in exc, exc
    assert reference in exc, exc
