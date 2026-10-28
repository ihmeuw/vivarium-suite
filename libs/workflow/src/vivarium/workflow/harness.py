"""A shared test harness for staleness scenarios.

The harness supplies a small ``python_step`` pipeline, a one-change perturbation
context manager, and plan/run helpers that the per-feature staleness scenarios
assert against. It is importable and runs without the cluster extra -- it drives
the backend ``build``/``preview`` directly.

Two parts the ticket names are deferred and are not here: the synchronous
fake-Jobmon adapter arrives with the bridge, and the selective-staleness
assertions (perturb one input, exactly its cone re-runs) arrive with the code
fingerprint, since with the always-stale default every step re-runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import pandas as pd
from pytask import TaskOutcome

from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend.python_step import python_step

# TODO(MIC-7520): add selective-staleness scenario helpers once a step can declare a
# code fingerprint; the synchronous fake-Jobmon adapter lands with the bridge.


@dataclass
class Fixture:
    """A built stub pipeline plus the handles scenarios perturb and inspect."""

    tasks: list[Task]
    """The compiled pipeline, ready for ``build`` or ``preview``."""
    registry: ArtifactRegistry
    """The registry the steps were wired through."""
    state: Path
    """Directory holding pytask's state for this fixture."""
    inputs: dict[str, Path]
    """Root-input key to file path -- the perturbation targets."""
    steps: tuple[str, ...]
    """The pipeline's step names, in dependency order."""


def _double(raw: Path) -> pd.DataFrame:
    """Read the root-input table and double its ``n`` column."""
    frame = pd.read_parquet(raw)
    return pd.DataFrame({"n": frame["n"] * 2})


def _offset(mid: Path) -> pd.DataFrame:
    """Read the intermediate table and offset its ``n`` column by ten."""
    frame = pd.read_parquet(mid)
    return pd.DataFrame({"n": frame["n"] + 10})


def build_fixture(tmp_path: Path) -> Fixture:
    """Build a small two-step ``python_step`` pipeline under ``tmp_path``.

    The pipeline reads a root-input table, doubles it into an intermediate
    product, then offsets that into a final product: a producer/consumer chain
    wired through one registry, enough for a scenario to perturb one input and
    inspect the plan. The returned :class:`Fixture` carries the tasks, the
    registry, the pytask state directory, the perturbable input paths, and the
    step names.
    """
    raw_path = tmp_path / "raw.parquet"
    pd.DataFrame({"n": [1, 2, 3]}).to_parquet(raw_path)

    registry = ArtifactRegistry(
        output_root=tmp_path / "out",
        run_name="run1",
        root_inputs={"raw": raw_path},
    )
    double = python_step(
        _double, inputs={"raw": "raw"}, produces="mid.parquet", registry=registry
    )
    offset = python_step(
        _offset, inputs={"mid": "mid.parquet"}, produces="final.parquet", registry=registry
    )

    state = tmp_path / "state"
    return Fixture(
        tasks=[double, offset],
        registry=registry,
        state=state,
        inputs={"raw": raw_path},
        steps=("mid.parquet", "final.parquet"),
    )


class Perturbed:
    """Apply one change to a file for the duration of a ``with`` block.

    On entry ``data`` is appended to ``path``; on exit the file's original bytes
    are restored, including when the block raises, so scenarios are
    order-independent and running the suite twice yields identical results.
    """

    def __init__(self, path: Path, data: bytes) -> None:
        self._path = Path(path)
        self._data = data
        self._original: bytes = b""

    def __enter__(self) -> "Perturbed":
        self._original = self._path.read_bytes()
        self._path.write_bytes(self._original + self._data)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._path.write_bytes(self._original)


def would_run(fixture: Fixture) -> set[str]:
    """Return the step names a dry-run preview of ``fixture`` reports as would-run."""
    plan = preview(fixture.tasks, fixture.state)
    return {step.name for step in plan if step.would_run}


def run(fixture: Fixture) -> dict[str, TaskOutcome]:
    """Build and run ``fixture``, returning each step's outcome by name."""
    return outcomes(build(fixture.tasks, fixture.state))
