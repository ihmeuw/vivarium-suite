"""A shared test harness for staleness scenarios.

The harness supplies a small ``python_step`` pipeline, a one-change perturbation
context manager, and plan/run helpers that the per-feature staleness scenarios
assert against. It is importable and runs without the cluster extra -- it drives
the backend ``build``/``preview`` directly.

Two parts the ticket names are deferred and are not here: the synchronous
fake-Jobmon adapter arrives with the bridge, and the selective-staleness
assertions (perturb one input, exactly its cone re-runs) arrive with the code
fingerprint (MIC-7520), since with the always-stale default every step re-runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from pytask import TaskOutcome

from vivarium.cluster_tools.core.backend.task import Task
from vivarium.cluster_tools.core.backend.wiring import ArtifactRegistry


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


def build_fixture(tmp_path: Path) -> Fixture:
    """Build a small two-step ``python_step`` pipeline under ``tmp_path``.

    The pipeline reads a root-input table, doubles it into an intermediate
    product, then offsets that into a final product: a producer/consumer chain
    wired through one registry, enough for a scenario to perturb one input and
    inspect the plan. The returned :class:`Fixture` carries the tasks, the
    registry, the pytask state directory, the perturbable input paths, and the
    step names.
    """
    raise NotImplementedError


class Perturbed:
    """Apply one change to a file for the duration of a ``with`` block.

    On entry ``data`` is appended to ``path``; on exit the file's original bytes
    are restored, including when the block raises, so scenarios are
    order-independent and running the suite twice yields identical results.
    """

    def __init__(self, path: Path, data: bytes) -> None:
        raise NotImplementedError

    def __enter__(self) -> "Perturbed":
        raise NotImplementedError

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError


def would_run(fixture: Fixture) -> set[str]:
    """Return the step names a dry-run preview of ``fixture`` reports as would-run."""
    raise NotImplementedError


def run(fixture: Fixture) -> dict[str, TaskOutcome]:
    """Build and run ``fixture``, returning each step's outcome by name."""
    raise NotImplementedError
