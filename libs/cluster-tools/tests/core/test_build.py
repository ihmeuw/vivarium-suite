import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pytask import PNode, TaskOutcome

from vivarium.cluster_tools.core.backend.build import (
    CODE_ID_KEY,
    COMMAND_KEY,
    TASK_KEY,
    build,
    outcomes,
    to_pytask_task,
)
from vivarium.cluster_tools.core.backend.nodes import file_node
from vivarium.cluster_tools.core.backend.task import Task


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
    assert pytask_task.depends_on[COMMAND_KEY].load() == "true"
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


def test_build_rejects_duplicate_task_names(tmp_path: Path) -> None:
    """Raise before running anything when two tasks share a name."""
    calls: list[str] = []
    root = tmp_path / "root"
    tasks = [_task(name, lambda: calls.append("ran")) for name in ("first", "dup", "dup")]

    with pytest.raises(ValueError, match="'dup'"):
        build(tasks, root)

    assert calls == []
    assert not root.exists()
