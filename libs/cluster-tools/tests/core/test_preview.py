"""Acceptance scenarios for the run preview (``core.backend.preview``).

Each scenario converges a small file pipeline, perturbs one thing, and asserts
the exact set of steps the dry-run reports as would-run.
"""

from pathlib import Path
from typing import Any

import pytask
import pytest

from vivarium.cluster_tools.core.backend.build import build
from vivarium.cluster_tools.core.backend.nodes import file_node
from vivarium.cluster_tools.core.backend.preview import StepPreview, preview
from vivarium.cluster_tools.core.backend.task import Task


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
    root = tmp_path / ".workflow"
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
    build_reasons = " ".join(_by_name(plan)["build"].reasons)
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


def test_preview_writes_no_product(
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
        preview(tasks, tmp_path / ".workflow")
