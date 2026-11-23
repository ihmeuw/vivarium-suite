"""Acceptance scenarios for the run preview (``backend.preview``).

Each scenario converges a small file pipeline, perturbs one thing, and asserts
the exact set of steps the dry-run reports as would-run.
"""

from pathlib import Path
from typing import Any

import pytask
import pytest

from tests.backend.order_names import EIGHT, TEN
from vivarium.dagger.backend.build import build, failure_message
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.preview import PlanFailed, Reason, StepPreview, preview
from vivarium.dagger.backend.task import Task


class _VersionNode:
    """In-memory node whose state is a fixed ``version`` (a stable code id)."""

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


def _copy_task(name: str, src: Path, dst: Path) -> Task:
    """A command task that copies ``src`` to ``dst``, tracking both as file nodes."""
    return Task(
        name=name,
        run=f"cp {src} {dst}",
        inputs={"src": file_node(src)},
        outputs={"dst": file_node(dst)},
        resources={},
        env=None,
        code_id=_VersionNode(f"code:{name}"),
    )


def _by_name(plan: list[StepPreview]) -> dict[str, StepPreview]:
    return {step.name: step for step in plan}


def _would_run(plan: list[StepPreview]) -> set[str]:
    return {step.name for step in plan if step.would_run}


@pytest.fixture
def converged(tmp_path: Path) -> tuple[list[Task], Path, Path, Path]:
    """A built, converged two-step pipeline: build (raw -> artifact) -> pafs."""
    raw = tmp_path / "raw.csv"
    raw.write_text("one\n")
    artifact = tmp_path / "artifact.csv"
    pafs = tmp_path / "pafs.csv"
    tasks = [_copy_task("build", raw, artifact), _copy_task("pafs", artifact, pafs)]
    root = tmp_path / "state"
    session = build(tasks, root)
    assert session.exit_code == pytask.ExitCode.OK
    return tasks, root, raw, artifact


def test_converged_pipeline_previews_all_skip(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, _raw, _artifact = converged
    plan = preview(tasks, root)
    assert _would_run(plan) == set()


def test_editing_one_input_marks_its_consumer_cone(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, raw, _artifact = converged
    raw.write_text("two\n")
    plan = preview(tasks, root)
    assert _would_run(plan) == {"build", "pafs"}
    build_reasons = " ".join(str(reason) for reason in _by_name(plan)["build"].reasons)
    assert "raw" in build_reasons


def test_identical_resave_previews_all_skip(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, raw, _artifact = converged
    raw.write_text("one\n")  # byte-identical content, new mtime
    plan = preview(tasks, root)
    assert _would_run(plan) == set()


def test_missing_output_marks_its_producer(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, _raw, artifact = converged
    artifact.unlink()
    plan = preview(tasks, root)
    assert _would_run(plan) == {"build", "pafs"}


def test_preview_writes_no_output(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, raw, artifact = converged
    raw.write_text("two\n")  # make the pipeline stale
    before = artifact.read_text()
    preview(tasks, root)
    assert artifact.read_text() == before  # dry-run rebuilt nothing


def test_preview_returns_in_memory_step_objects(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    tasks, root, _raw, _artifact = converged
    plan = preview(tasks, root)
    assert plan and all(isinstance(step, StepPreview) for step in plan)
    assert {step.name for step in plan} == {"build", "pafs"}


def test_preview_raises_when_the_build_fails(tmp_path: Path) -> None:
    """A failed build (here a dependency cycle) raises instead of a false all-skip plan."""
    x, y = tmp_path / "x.csv", tmp_path / "y.csv"
    x.write_text("x\n")
    y.write_text("y\n")
    tasks = [_copy_task("a", y, x), _copy_task("b", x, y)]  # a: y->x, b: x->y, a cycle

    with pytest.raises(RuntimeError):
        preview(tasks, tmp_path / "state")


def test_reasons_carry_the_node_and_kind_pytask_compared(
    converged: tuple[list[Task], Path, Path, Path],
) -> None:
    """After an input edit, the consumer's reasons are Reason objects naming the input's path with kind 'changed'."""
    tasks, root, raw, _artifact = converged
    raw.write_text("two\n")

    reasons = _by_name(preview(tasks, root))["build"].reasons

    assert reasons and all(isinstance(reason, Reason) for reason in reasons)
    assert Reason(node=raw.resolve().as_posix(), kind="changed") in reasons


@pytest.mark.parametrize(
    "node, kind, phrase",
    [
        ("/data/raw.csv", "changed", "/data/raw.csv changed"),
        ("/data/raw.csv", "missing", "/data/raw.csv is missing"),
        ("/data/raw.csv", "not_in_db", "/data/raw.csv has no recorded state"),
        ("build", "first_run", "first run"),
        ("", "forced", "forced"),
        ("build", "cascade", "build would run first"),
        ("build", "renamed", "build: renamed"),
    ],
)
def test_reason_reads_as_a_short_phrase(node: str, kind: str, phrase: str) -> None:
    """str(Reason) gives the documented phrase for each kind, not a dataclass repr."""
    assert str(Reason(node=node, kind=kind)) == phrase


def test_preview_error_carries_pytasks_message(tmp_path: Path) -> None:
    """When two tasks produce one file, preview's RuntimeError carries pytask's message."""
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    src.write_text("x\n")
    tasks = [_copy_task("a", src, out), _copy_task("b", src, out)]

    with pytest.raises(RuntimeError) as info:
        preview(tasks, tmp_path / "state")

    message = str(info.value)
    assert "DAG_FAILED" in message
    assert "There are some tasks which produce the same output" in message


def test_preview_raises_plan_failed_carrying_pytasks_exit_code(tmp_path: Path) -> None:
    """When two tasks produce one file, preview raises PlanFailed, which is a RuntimeError, whose exit_code is ExitCode.DAG_FAILED and whose message still names DAG_FAILED."""
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    src.write_text("x\n")
    tasks = [_copy_task("a", src, out), _copy_task("b", src, out)]

    with pytest.raises(PlanFailed) as info:
        preview(tasks, tmp_path / "state")

    error = info.value
    assert isinstance(error, RuntimeError)
    assert error.exit_code == pytask.ExitCode.DAG_FAILED
    assert "DAG_FAILED" in str(error)


# --- Messages (MIC-7848) ---


def test_plan_failed_message_starts_with_a_capital(tmp_path: Path) -> None:
    """A preview whose build fails raises PlanFailed whose message starts 'Preview could not build the plan:'."""
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    src.write_text("x\n")
    tasks = [_copy_task("a", src, out), _copy_task("b", src, out)]

    with pytest.raises(PlanFailed) as info:
        preview(tasks, tmp_path / "state")

    message = str(info.value)
    assert message.startswith("Preview could not build the plan:"), message


def test_failure_message_adds_a_line_for_each_failed_step(tmp_path: Path) -> None:
    """failure_message for a dry-run session in which a step failed with an exception adds a line "Step '<name>': <ExceptionType>: <message>" for that step after the exit code's name."""
    missing = tmp_path / "missing.csv"
    tasks = [_copy_task("copy", missing, tmp_path / "copy.csv")]

    session = build(tasks, tmp_path / "state", dry_run=True, explain=True)

    [report] = [
        report
        for report in session.execution_reports
        if report.outcome == pytask.TaskOutcome.FAIL
    ]
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert error is not None
    message = failure_message(session)
    line = f"Step 'copy': {type(error).__name__}: {error}"
    assert message.startswith(session.exit_code.name), message
    assert f"\n{line}" in message, message


# The order of the tasks given


def _make_task(name: str, dst: Path) -> Task:
    """A command task that writes ``dst`` and reads no file."""
    return Task(
        name=name,
        run=f"echo {name} > {dst}",
        inputs={},
        outputs={"dst": file_node(dst)},
        resources={},
        env=None,
        code_id=_VersionNode(f"code:{name}"),
    )


def _ten_tasks(folder: Path) -> list[Task]:
    """The eight independent tasks of ``EIGHT``, then after_mike and after_zeta, which read mike's and zeta's outputs."""
    tasks = [_make_task(name, folder / name) for name in EIGHT]
    for source in ("mike", "zeta"):
        name = f"after_{source}"
        tasks.append(_copy_task(name, folder / source, folder / name))
    return tasks


def test_preview_refuses_a_consumer_given_before_its_producer(tmp_path: Path) -> None:
    """preview([consumer, producer], root), where 'consumer' reads the file that 'producer' writes, raises ValueError, not PlanFailed, whose message names 'consumer' and then 'producer', and root is not created."""
    root = tmp_path / "state"
    made = tmp_path / "made"
    tasks = [_copy_task("consumer", made, tmp_path / "copied"), _make_task("producer", made)]

    with pytest.raises(ValueError, match="'consumer'.*'producer'") as info:
        preview(tasks, root)

    assert not isinstance(info.value, PlanFailed)
    assert not root.exists()


def test_preview_lists_steps_in_the_order_of_the_tasks_given(tmp_path: Path) -> None:
    """preview of the independent command tasks zeta, alpha, mike, bravo, yankee, charlie, xray, delta, followed by after_mike (reads mike's output) and after_zeta (reads zeta's output), given in that order, returns one StepPreview per task with the ten names in that order."""
    plan = preview(_ten_tasks(tmp_path), tmp_path / "state")

    assert all(isinstance(step, StepPreview) for step in plan)
    assert [step.name for step in plan] == list(TEN)


def test_preview_with_selected_steps_keeps_task_order_with_the_skips_in_place(
    tmp_path: Path,
) -> None:
    """preview of the tasks zeta, alpha, mike, bravo, yankee, charlie, xray, delta, after_mike, after_zeta, given in that order (after_mike reads mike's output and after_zeta reads zeta's), with selected={'after_mike'} returns the ten names in that order; mike and after_mike are WOULD_BE_EXECUTED and every other entry is SKIP, in its place."""
    plan = preview(_ten_tasks(tmp_path), tmp_path / "state", selected={"after_mike"})

    run, skip = pytask.TaskOutcome.WOULD_BE_EXECUTED, pytask.TaskOutcome.SKIP
    assert [(step.name, step.outcome) for step in plan] == [
        (name, run if name in ("mike", "after_mike") else skip) for name in TEN
    ]


def test_preview_with_an_empty_selection_keeps_task_order(tmp_path: Path) -> None:
    """preview of the tasks zeta, alpha, mike, bravo, yankee, charlie, xray, delta, after_mike, after_zeta, given in that order, with selected=set() returns the ten names in that order, and every outcome is SKIP."""
    plan = preview(_ten_tasks(tmp_path), tmp_path / "state", selected=set())

    assert [step.name for step in plan] == list(TEN)
    assert {step.outcome for step in plan} == {pytask.TaskOutcome.SKIP}


def test_plan_failed_lists_failed_steps_in_the_order_of_the_tasks_given(
    tmp_path: Path,
) -> None:
    """preview of the eight tasks zeta_copy, alpha_copy, mike_copy, bravo_copy, yankee_copy, charlie_copy, xray_copy, delta_copy, given in that order, each of which copies its own source file that does not exist, raises PlanFailed whose message lines that start with 'Step ' name the eight tasks in that order."""
    names = [f"{name}_copy" for name in EIGHT]
    tasks = [
        _copy_task(name, tmp_path / f"{name}.missing", tmp_path / name) for name in names
    ]

    with pytest.raises(PlanFailed) as info:
        preview(tasks, tmp_path / "state")

    lines = [line for line in str(info.value).splitlines() if line.startswith("Step ")]
    assert [line.split("'")[1] for line in lines] == names, lines
