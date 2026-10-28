"""Tests for the shared staleness test harness (``harness``)."""

from pathlib import Path

import pandas as pd
import pytest
from pytask import TaskOutcome

from vivarium.workflow.harness import Perturbed, build_fixture, run, would_run


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
