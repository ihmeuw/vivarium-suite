"""Tests for the shared staleness test harness (``harness``)."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from pytask import TaskOutcome

from vivarium.workflow.harness import (
    Fixture,
    Perturbed,
    build_fixture,
    run,
    stable_code_id,
    would_run,
)


def _with_code_version(fx: Fixture, version: str) -> Fixture:
    """Return ``fx`` with every task given a stable code identity at ``version``."""
    tasks = [
        replace(task, code_id=stable_code_id(f"{task.name}::code", version))
        for task in fx.tasks
    ]
    return replace(fx, tasks=tasks)


def test_build_fixture_runs_and_writes_its_products(tmp_path: Path) -> None:
    """The fixture builds a runnable pipeline; running it succeeds and writes products."""
    fx = build_fixture(tmp_path)

    assert run(fx) == {name: TaskOutcome.SUCCESS for name in fx.steps}

    for step in fx.steps:
        product = fx.registry.path_for(step)
        assert product.exists()  # the step wrote its product
        frame = pd.read_parquet(product)
        assert isinstance(frame, pd.DataFrame)
        assert not frame.empty  # the product reads back as a non-empty frame

    final = pd.read_parquet(fx.registry.path_for("final.parquet"))
    assert final["n"].tolist() == [12, 14, 16]  # the chain is wired: mid feeds final


def test_would_run_reports_the_step_names(tmp_path: Path) -> None:
    """would_run returns a set of the fixture's step names drawn from a preview."""
    fx = build_fixture(tmp_path)

    assert would_run(fx) == set(fx.steps)


def test_perturbed_changes_a_file_then_restores_it(tmp_path: Path) -> None:
    """Perturbed changes the file's bytes inside the block and restores them on exit."""
    path = tmp_path / "target.bin"
    path.write_bytes(b"original contents")
    original = path.read_bytes()

    with Perturbed(path, b"\n# perturb\n"):
        assert path.read_bytes() == original + b"\n# perturb\n"  # appended in block

    assert path.read_bytes() == original  # the original bytes are restored on exit


def test_perturbed_restores_even_when_the_block_raises(tmp_path: Path) -> None:
    """Perturbed restores the original bytes even if the with-block raises."""
    path = tmp_path / "target.bin"
    path.write_bytes(b"original contents")
    original = path.read_bytes()

    with pytest.raises(RuntimeError):
        with Perturbed(path, b"x"):
            raise RuntimeError("boom")

    assert path.read_bytes() == original  # restored despite the block raising


def test_stable_code_id_lets_an_unchanged_step_skip(tmp_path: Path) -> None:
    """A step whose code_id is stable_code_id runs on the first build and is SKIP_UNCHANGED on the second."""
    fx = _with_code_version(build_fixture(tmp_path), "1")

    assert run(fx) == dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert run(fx) == dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)


def test_changing_the_stable_code_id_version_reruns_the_step(tmp_path: Path) -> None:
    """Rebuilding with the same step under a new stable_code_id version runs it again."""
    fx = build_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    skipped = dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)

    assert run(_with_code_version(fx, "1")) == ran
    assert run(_with_code_version(fx, "2")) == ran
    assert run(_with_code_version(fx, "2")) == skipped


def test_stable_code_id_state_is_identical_in_another_process() -> None:
    """stable_code_id with the same name and version reports the same state in a fresh interpreter, so a lockfile written by one process matches in another."""
    name = "step::code"
    version = "2"
    code = (
        "from vivarium.workflow.harness import stable_code_id\n"
        f"print(stable_code_id({name!r}, {version!r}).state(), end='')"
    )

    local = stable_code_id(name, version).state()
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert local is not None
    assert result.stdout == local
