"""A shared test harness for staleness scenarios.

The harness supplies a small ``python_step`` pipeline, a one-change perturbation
context manager, and plan/run helpers that the per-feature staleness scenarios
assert against. It is importable and runs without the cluster extra -- it drives
the backend ``build``/``preview`` directly.

Every step defaults to an always-stale code identity, so it re-runs on every build.
:func:`stable_code_id` gives a step a fixed code identity instead, so scenarios can
exercise skipping, forcing, and resuming before steps can declare a code fingerprint.

Two parts are not here yet: the synchronous fake-Jobmon adapter arrives with
the bridge, and the selective-staleness assertions (perturb one input, exactly its
cone re-runs) arrive with the code fingerprint.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import pandas as pd
from pytask import PNode, TaskOutcome

from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend.python_step import python_step

# TODO(MIC-7520): add selective-staleness scenario helpers once a step can declare a
# code fingerprint; the synchronous fake-Jobmon adapter lands with the bridge.


@dataclass
class _StableCodeNode:
    """A read-only code identity whose state is a hash of its version."""

    name: str
    version: str
    attributes: dict[Any, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        """Return a stable hash of the node name."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def state(self) -> str | None:
        """Return a hash of the version, identical across builds and processes."""
        return hashlib.sha256(self.version.encode()).hexdigest()

    def load(self, is_product: bool = False) -> _StableCodeNode:
        """Return the node itself."""
        return self

    def save(self, value: Any) -> None:
        """Reject saves: this is a dependency, not a product."""
        raise NotImplementedError("A code identity is a dependency and cannot be saved.")


def stable_code_id(name: str, version: str = "1") -> PNode:
    """Return a code identity whose state is fixed by ``version``.

    A task given this ``code_id`` is skipped on a rebuild when nothing else changed, which
    the always-stale default hides. Scenarios use it to exercise skip, force, and resume;
    changing ``version`` stands in for a code edit.

    Parameters
    ----------
    name
        The node's name, unique across the pipeline (for example ``"<step>::code"``):
        pytask keys nodes by name, so tasks that share one share a single code identity.
    version
        The code version the node reports.

    Returns
    -------
        A read-only dependency node whose state changes only when ``version`` does.
    """
    return _StableCodeNode(name=name, version=version)


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
