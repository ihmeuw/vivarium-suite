import re
import signal
import subprocess
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

import pytest
from pytask import ExitCode, PNode, PythonNode, TaskOutcome

import vivarium.dagger.backend.build as build_module
from vivarium.dagger.backend.build import (
    BRIDGE_PLUGIN,
    CODE_ID_KEY,
    COMMAND_KEY,
    TASK_KEY,
    TaskExited,
    build,
    describe_exit,
    failure_message,
    outcomes,
    to_pytask_task,
)
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task


class _VersionNode:
    """In-memory node whose state is its settable ``version``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[Any, Any] = {}
        self.version = "v1"

    @property
    def signature(self) -> str:
        return f"stub:{self.name}"

    def state(self) -> str | None:
        return self.version

    def load(self, is_product: bool = False) -> Any:
        return self.version

    def save(self, value: Any) -> None:
        raise NotImplementedError


def _write(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` and return the path."""
    path.write_text(text)
    return path


def _noop(**kwargs: Any) -> None:
    """Accept any keyword arguments and do nothing."""


def _task(
    name: str,
    run: Callable[..., Any] | str,
    inputs: Mapping[str, PNode] | None = None,
    outputs: Mapping[str, PNode] | None = None,
    code_id: PNode | None = None,
) -> Task:
    """Build a task with no resources or env, and its own code_id unless one is given."""
    return Task(
        name=name,
        run=run,
        inputs=inputs or {},
        outputs=outputs or {},
        resources={},
        env=None,
        code_id=code_id or _VersionNode(f"code:{name}"),
    )


def _copy(name: str, src: Path, dst: Path, code_id: PNode | None = None) -> Task:
    """Build a command task that copies ``src`` to ``dst``."""
    inputs, outputs = {"src": file_node(src)}, {"dst": file_node(dst)}
    return _task(name, f"cp {src} {dst}", inputs, outputs, code_id)


def _every(tasks: list[Task], outcome: TaskOutcome) -> dict[str, TaskOutcome]:
    """Map every task's name to ``outcome``."""
    return dict.fromkeys((task.name for task in tasks), outcome)


def test_to_pytask_task_maps_fields() -> None:
    """Map name, inputs plus code_id and command, outputs, and the task back-reference."""
    raw, clean, code_id = _VersionNode("raw"), _VersionNode("clean"), _VersionNode("code")
    task = _task("clean_data", "true", {"raw": raw}, {"out-file": clean}, code_id)

    pytask_task = to_pytask_task(task)

    assert pytask_task.name == "clean_data"
    assert pytask_task.depends_on.keys() == {"raw", CODE_ID_KEY, COMMAND_KEY}
    assert pytask_task.depends_on["raw"] is raw
    assert pytask_task.depends_on[CODE_ID_KEY] is code_id
    command = cast(PythonNode, pytask_task.depends_on[COMMAND_KEY])
    assert command.load() == "true"
    assert pytask_task.produces == {"out-file": clean}
    assert pytask_task.attributes[TASK_KEY] is task
    assert COMMAND_KEY not in to_pytask_task(_task("fn", _noop)).depends_on


@pytest.mark.parametrize(
    "run, inputs, outputs, name",
    [
        ("true", {CODE_ID_KEY: _VersionNode("code")}, {}, CODE_ID_KEY),
        ("true", {}, {CODE_ID_KEY: _VersionNode("out")}, CODE_ID_KEY),
        ("true", {COMMAND_KEY: _VersionNode("in")}, {}, COMMAND_KEY),
        ("true", {}, {COMMAND_KEY: _VersionNode("out")}, COMMAND_KEY),
        ("true", {}, {"return": _VersionNode("out")}, "return"),
        ("true", {"data": _VersionNode("in")}, {"data": _VersionNode("out")}, "data"),
        (_noop, {}, {"out-file": _VersionNode("out")}, "out-file"),
        (_noop, {}, {"class": _VersionNode("out")}, "class"),
    ],
    ids=[
        "input_code_id",
        "output_code_id",
        "input_command",
        "output_command",
        "output_return",
        "shared_key",
        "callable_dash",
        "callable_class",
    ],
)
def test_to_pytask_task_rejects_reserved_names(
    run: Callable[..., Any] | str,
    inputs: dict[str, PNode],
    outputs: dict[str, PNode],
    name: str,
) -> None:
    """Reject reserved or shared names, and a callable's non-parameter output names."""
    task = _task("clean_data", run, inputs, outputs)
    with pytest.raises(ValueError, match=rf"clean_data.*{re.escape(name)}"):
        to_pytask_task(task)


def test_pipeline_runs_then_skips_when_unchanged(tmp_path: Path) -> None:
    """Run a three-task callable-and-command pipeline, then skip every task on rebuild."""
    calls: list[str] = []

    def double(raw: Path, doubled: Path) -> None:
        calls.append("double")
        doubled.write_text(raw.read_text() * 2)

    def count(upper: Path, summary: Path) -> None:
        calls.append("count")
        summary.write_text(str(len(upper.read_text())))

    raw, log = _write(tmp_path / "raw", "ab"), tmp_path / "shout.log"
    doubled, upper, summary = tmp_path / "doubled", tmp_path / "upper", tmp_path / "summary"
    tasks = [
        _task("double", double, {"raw": file_node(raw)}, {"doubled": file_node(doubled)}),
        _task(
            "shout",
            f"tr a-z A-Z < {doubled} > {upper} && echo ran >> {log}",
            {"doubled": file_node(doubled)},
            {"upper": file_node(upper)},
        ),
        _task("count", count, {"upper": file_node(upper)}, {"summary": file_node(summary)}),
    ]

    assert outcomes(build(tasks, tmp_path)) == _every(tasks, TaskOutcome.SUCCESS)
    assert (upper.read_text(), summary.read_text()) == ("ABAB", "4")

    assert outcomes(build(tasks, tmp_path)) == _every(tasks, TaskOutcome.SKIP_UNCHANGED)
    assert calls == ["double", "count"]
    assert log.read_text() == "ran\n"


def test_editing_an_input_reruns_its_consumer_cone(tmp_path: Path) -> None:
    """Preview with dry_run, then re-run exactly the tasks downstream of an edited input."""
    a, b = _write(tmp_path / "a", "a"), _write(tmp_path / "b", "b")
    left, right, joined = tmp_path / "left", tmp_path / "right", tmp_path / "joined"
    tasks = [
        _copy("left", a, left),
        _copy("right", b, right),
        _task(
            "join",
            f"cat {left} {right} > {joined}",
            {"left": file_node(left), "right": file_node(right)},
            {"joined": file_node(joined)},
        ),
    ]
    assert outcomes(build(tasks, tmp_path)) == _every(tasks, TaskOutcome.SUCCESS)

    a.write_text("A")

    assert outcomes(build(tasks, tmp_path, dry_run=True)) == {
        "left": TaskOutcome.WOULD_BE_EXECUTED,
        "right": TaskOutcome.SKIP_UNCHANGED,
        "join": TaskOutcome.WOULD_BE_EXECUTED,
    }
    assert joined.read_text() == "ab"

    assert outcomes(build(tasks, tmp_path)) == {
        "left": TaskOutcome.SUCCESS,
        "right": TaskOutcome.SKIP_UNCHANGED,
        "join": TaskOutcome.SUCCESS,
    }
    assert joined.read_text() == "Ab"


def test_code_changes_rerun_only_their_task(tmp_path: Path) -> None:
    """Re-run only the task whose code_id state or command changed."""
    src = _write(tmp_path / "src", "x")
    code_a, code_b = _VersionNode("code:a"), _VersionNode("code:b")
    tasks = [
        _copy("a", src, tmp_path / "a", code_a),
        _copy("b", src, tmp_path / "b", code_b),
    ]
    assert outcomes(build(tasks, tmp_path)) == _every(tasks, TaskOutcome.SUCCESS)

    code_a.version = "v2"

    assert outcomes(build(tasks, tmp_path)) == {
        "a": TaskOutcome.SUCCESS,
        "b": TaskOutcome.SKIP_UNCHANGED,
    }

    b = tasks[1]
    tasks[1] = _task("b", f"cat {src} > {tmp_path / 'b'}", b.inputs, b.outputs, code_b)

    assert outcomes(build(tasks, tmp_path)) == {
        "a": TaskOutcome.SKIP_UNCHANGED,
        "b": TaskOutcome.SUCCESS,
    }


def test_failed_task_is_reported_and_blocks_descendants(tmp_path: Path) -> None:
    """Report a failing command as FAIL with its exception, and its descendants as skipped."""
    out, child, grandchild = tmp_path / "out", tmp_path / "child", tmp_path / "grandchild"
    tasks = [
        _task("fail", "exit 3", outputs={"out": file_node(out)}),
        _copy("child", out, child),
        _copy("grandchild", child, grandchild),
    ]

    session = build(tasks, tmp_path)

    assert outcomes(session) == {
        "fail": TaskOutcome.FAIL,
        "child": TaskOutcome.SKIP_PREVIOUS_FAILED,
        "grandchild": TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    [report] = [r for r in session.execution_reports if r.task.name == "fail"]
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.returncode == 3


def test_build_keeps_state_under_root_and_collects_no_task_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep pytask's state under a new root inside a git checkout; collect no task files."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    root = tmp_path / "state" / "root"
    tasks = [_copy("copy", _write(tmp_path / "in", "x"), tmp_path / "out")]

    assert outcomes(build(tasks, root)) == {"copy": TaskOutcome.SUCCESS}
    assert (root / ".pytask").is_dir()
    assert (root / "pytask.lock").is_file()
    assert not (tmp_path / ".pytask").exists()
    assert not (tmp_path / "pytask.lock").exists()

    config = '[tool.pytask.ini_options]\nshow_capture = "no"\n'
    (root / "pytask.toml").write_text(config)
    (root / "task_stray.py").write_text("def task_stray():\n    raise RuntimeError\n")

    assert outcomes(build(tasks, root)) == {"copy": TaskOutcome.SKIP_UNCHANGED}
    assert (root / "pytask.toml").read_text() == config


def test_default_code_id_reruns_on_every_build(tmp_path: Path) -> None:
    """A task that declares no code fingerprint is always stale, even when nothing changed."""
    src, out = _write(tmp_path / "src", "x"), tmp_path / "out"
    task = Task(
        name="copy",
        run=f"cp {src} {out}",
        inputs={"src": file_node(src)},
        outputs={"dst": file_node(out)},
        resources={},
        env=None,
    )  # code_id omitted, so it defaults to an always-stale node

    assert outcomes(build([task], tmp_path)) == {"copy": TaskOutcome.SUCCESS}

    # Nothing changed on disk, but with no fingerprint the code dimension is always stale.
    assert outcomes(build([task], tmp_path, dry_run=True)) == {
        "copy": TaskOutcome.WOULD_BE_EXECUTED
    }


def test_build_rejects_duplicate_task_names(tmp_path: Path) -> None:
    """Raise before running anything when two tasks share a name."""
    calls: list[str] = []
    root = tmp_path / "root"
    tasks = [_task(name, lambda: calls.append("ran")) for name in ("first", "dup", "dup")]

    with pytest.raises(ValueError, match="'dup'"):
        build(tasks, root)

    assert calls == []
    assert not root.exists()


def test_selected_runs_the_named_tasks_and_their_ancestors_and_skips_the_rest(
    tmp_path: Path,
) -> None:
    """build(selected=[name]) runs that task and its ancestors; every other task is SKIP."""
    src = _write(tmp_path / "src", "x")
    a, b, c, d = tmp_path / "a", tmp_path / "b", tmp_path / "c", tmp_path / "d"
    tasks = [_copy("a", src, a), _copy("b", a, b), _copy("c", b, c), _copy("d", src, d)]

    assert outcomes(build(tasks, tmp_path / "state", selected=["b"])) == {
        "a": TaskOutcome.SUCCESS,
        "b": TaskOutcome.SUCCESS,
        "c": TaskOutcome.SKIP,
        "d": TaskOutcome.SKIP,
    }
    assert b.read_text() == "x"
    assert not c.exists()
    assert not d.exists()


def test_an_empty_selection_runs_no_task(tmp_path: Path) -> None:
    """build(selected=[]) reports every task as SKIP and exits OK."""
    src = _write(tmp_path / "src", "x")
    a, b = tmp_path / "a", tmp_path / "b"
    tasks = [_copy("a", src, a), _copy("b", src, b)]

    session = build(tasks, tmp_path / "state", selected=[])

    assert session.exit_code == ExitCode.OK
    assert outcomes(session) == _every(tasks, TaskOutcome.SKIP)
    assert not a.exists()
    assert not b.exists()


def test_selected_rejects_a_name_that_is_not_a_task(tmp_path: Path) -> None:
    """build(selected=[...]) with a name that is not a task raises ValueError naming it."""
    tasks = [_copy("a", _write(tmp_path / "src", "x"), tmp_path / "a")]

    with pytest.raises(ValueError, match="no_such_task"):
        build(tasks, tmp_path / "state", selected=["a", "no_such_task"])


def test_a_selection_of_more_than_a_thousand_tasks_builds(tmp_path: Path) -> None:
    """Selecting over a thousand tasks builds without error, since one shared marker selects them all."""
    tasks = [_task(f"task_{index}", _noop) for index in range(1200)]
    chosen, rest = tasks[:1100], tasks[1100:]
    names = [task.name for task in chosen]

    session = build(tasks, tmp_path / "state", selected=names, dry_run=True)

    assert session.exit_code == ExitCode.OK
    expected = _every(chosen, TaskOutcome.WOULD_BE_EXECUTED)
    expected.update(_every(rest, TaskOutcome.SKIP))
    assert outcomes(session) == expected


def test_failure_message_names_the_exit_code_and_pytasks_error(tmp_path: Path) -> None:
    """failure_message of a session where two tasks produce one file names DAG_FAILED and pytask's message."""
    product = file_node(tmp_path / "out")
    tasks = [_task(name, "true", outputs={"out": product}) for name in ("a", "b")]

    session = build(tasks, tmp_path / "state")
    message = failure_message(session)

    assert session.exit_code == ExitCode.DAG_FAILED
    assert message.startswith(ExitCode.DAG_FAILED.name)
    assert "There are some tasks which produce the same output" in message


def test_failure_message_of_a_failed_step_is_the_exit_code_name(tmp_path: Path) -> None:
    """failure_message of a session that failed only because a step failed is the exit code's name."""
    tasks = [_task("fail", "exit 1", outputs={"out": file_node(tmp_path / "out")})]

    session = build(tasks, tmp_path / "state")

    assert session.exit_code == ExitCode.FAILED
    assert failure_message(session) == session.exit_code.name


# How a failed command's exit reads (MIC-7785)


@pytest.mark.parametrize(
    "returncode, expected",
    [
        (1, "the command exited with status 1"),
        (2, "the command exited with status 2"),
        (3, "the command exited with status 3"),
        (128, "the command exited with status 128"),
        (137, "the command exited with status 137 (signal SIGKILL)"),
        (143, "the command exited with status 143 (signal SIGTERM)"),
        (255, "the command exited with status 255"),
        (-9, "the command was stopped by signal SIGKILL"),
        (-15, "the command was stopped by signal SIGTERM"),
    ],
)
def test_describe_exit_wording(returncode: int, expected: str) -> None:
    """A code of 128+n names signal n after the status when Python has a name for it; a negative code names the signal that stopped the command."""
    assert describe_exit(returncode) == expected


def test_signal_127_has_no_name_so_status_255_is_the_status_alone() -> None:
    """Status 255 has no signal suffix because Python names no signal 127."""
    with pytest.raises(ValueError):
        signal.Signals(127)


def test_describe_exit_gives_the_number_of_a_signal_python_has_no_name_for() -> None:
    """describe_exit with a negative code that no signal Python knows has, such as -200, is 'the command was stopped by signal 200'."""
    with pytest.raises(ValueError):
        signal.Signals(200)

    assert describe_exit(-200) == "the command was stopped by signal 200"


def test_a_command_killed_by_a_signal_has_a_negative_return_code_that_describe_exit_names(
    tmp_path: Path,
) -> None:
    """A command task that kills its own shell with SIGKILL fails with a CalledProcessError whose return code describe_exit words as stopped by signal SIGKILL."""
    tasks = [_task("killed", "kill -9 $$", outputs={"out": file_node(tmp_path / "out")})]

    session = build(tasks, tmp_path / "state")

    assert outcomes(session) == {"killed": TaskOutcome.FAIL}
    [report] = session.execution_reports
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.returncode == -signal.SIGKILL
    assert describe_exit(error.returncode) == "the command was stopped by signal SIGKILL"


def test_a_shell_whose_child_is_killed_exits_137_and_describe_exit_names_sigkill(
    tmp_path: Path,
) -> None:
    """A command whose child shell is killed with SIGKILL fails with return code 137, which describe_exit words with SIGKILL."""
    command = "sh -c 'kill -9 $$'; exit $?"
    tasks = [_task("child_killed", command, outputs={"out": file_node(tmp_path / "out")})]

    session = build(tasks, tmp_path / "state")

    assert outcomes(session) == {"child_killed": TaskOutcome.FAIL}
    [report] = session.execution_reports
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.returncode == 128 + signal.SIGKILL
    assert "SIGKILL" in describe_exit(error.returncode)


# A task that exits (MIC-7785)


def _exit_three(**kwargs: Any) -> None:
    """End the process with code 3, as a task body that calls sys.exit does."""
    raise SystemExit(3)


def test_a_task_that_raises_system_exit_makes_build_raise_task_exited_naming_it(
    tmp_path: Path,
) -> None:
    """A callable task 'leave' that raises SystemExit(3) makes build raise TaskExited, a SystemExit whose task is 'leave' and code is 3, chained from the task's own SystemExit."""
    tasks = [_task("leave", _exit_three, outputs={"out": file_node(tmp_path / "out")})]

    with pytest.raises(TaskExited) as info:
        build(tasks, tmp_path / "state")

    exited = info.value
    assert isinstance(exited, SystemExit)
    assert exited.task == "leave"
    assert exited.code == 3
    cause = exited.__cause__
    assert type(cause) is SystemExit
    assert cause.code == 3


def test_bridge_plugin_names_the_module_of_the_bridge_entry_point() -> None:
    """BRIDGE_PLUGIN is the value of vivarium-dagger's ``pytask`` entry point for the bridge."""
    assert BRIDGE_PLUGIN in {point.value for point in entry_points(group="pytask")}


@pytest.mark.usefixtures("without_bridge_plugin")
def test_build_with_jobmon_but_no_bridge_plugin_raises_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """build(jobmon=True) without the bridge's plugin raises ValueError before the root exists."""
    src = _write(tmp_path / "src", "x")
    a = tmp_path / "a"

    with pytest.raises(ValueError, match="its pytask plugin is not registered") as excinfo:
        build([_copy("a", src, a)], tmp_path / "state", jobmon=True)

    assert BRIDGE_PLUGIN in str(excinfo.value)
    assert "pip install -e" in str(excinfo.value)
    assert not (tmp_path / "state").exists()
    assert not a.exists()


@pytest.mark.usefixtures("without_bridge_plugin")
@pytest.mark.parametrize("mode", ["dry_run", "explain"])
def test_a_local_only_build_with_jobmon_is_not_refused_without_the_bridge_plugin(
    tmp_path: Path, mode: str
) -> None:
    """With dry_run or explain the bridge keeps steps local anyway, so jobmon=True is not refused."""
    src = _write(tmp_path / "src", "x")
    a = tmp_path / "a"
    flags: dict[str, Any] = {"jobmon": True, mode: True}

    session = build([_copy("a", src, a)], tmp_path / "state", **flags)

    assert session.exit_code == ExitCode.OK
    assert not a.exists()


@pytest.mark.parametrize("jobmon", [{}, {"jobmon": False}], ids=["unset", "false"])
def test_build_without_jobmon_does_not_look_for_the_bridge_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobmon: dict[str, Any]
) -> None:
    """A build that does not ask for Jobmon never reads the entry points and runs locally."""

    def fail(**kwargs: Any) -> list[Any]:
        raise AssertionError("entry_points was read")

    monkeypatch.setattr(build_module, "entry_points", fail)
    src = _write(tmp_path / "src", "x")
    a = tmp_path / "a"

    assert outcomes(build([_copy("a", src, a)], tmp_path / "state", **jobmon)) == {
        "a": TaskOutcome.SUCCESS
    }
    assert a.read_text() == "x"
