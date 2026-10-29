"""A shared test harness for staleness scenarios.

The harness supplies a small ``python_step`` pipeline, a one-change perturbation
context manager, and plan/run helpers that the per-feature staleness scenarios
assert against. It is importable and runs without the cluster extra -- it drives
the backend ``build``/``preview`` directly.

Every step defaults to an always-stale code identity, so it re-runs on every build.
:func:`stable_code_id` gives a step a fixed code identity instead, so scenarios can
exercise skipping, forcing, and resuming before steps can declare a code fingerprint.

:class:`FakeJobmon` stands in for Jobmon, so a build with ``jobmon=True`` runs its steps
through the bridge's node runner with no Jobmon server.

The selective-staleness assertions (perturb one input, exactly its cone re-runs) are not
here yet; they arrive with the code fingerprint.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping, Sequence
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
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.workflow.frontend.python_step import python_step

# TODO(MIC-7520): add selective-staleness scenario helpers once a step can declare a
# code fingerprint.

_ADAPTER_FUNCTIONS = ("check_environment", "bind", "run")
"""The functions of the Jobmon adapter that :class:`FakeJobmon` replaces."""


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


@dataclass
class FakeJobmon:
    """Stand in for Jobmon, so a build with ``jobmon=True`` needs no Jobmon server.

    Inside a ``with`` block, the bridge's Jobmon adapter
    (:mod:`~vivarium.workflow.bridge._jobmon_adapter`) has its ``check_environment``,
    ``bind`` and ``run`` replaced by this object's methods of the same names. A build
    with ``jobmon=True`` then runs each plan entry's command in a child process, one at a
    time in plan order, so each step runs in the real node runner and in a process other
    than the build's. The children get this process's environment variables, so a test
    whose steps import test modules puts them on ``PYTHONPATH``. On exit the adapter's
    own functions are restored, also when the block raises.

    For example::

        with FakeJobmon() as jobmon:
            build(fx.tasks, fx.state, jobmon=True)
        assert [entry.name for entry in jobmon.plans[0]] == list(fx.steps)
    """

    error_after: int | None = None
    """How many entries, in plan order, :meth:`run` handles before it stops as the real
    adapter does when Jobmon's ``Workflow.run`` raises. ``None`` runs the whole plan."""
    plans: list[list[PlanEntry]] = field(default_factory=list, init=False)
    """Every plan bound inside the block, in the order bound."""
    options: list[dict[str, Any]] = field(default_factory=list, init=False)
    """The keyword arguments of each bind, in the order bound, with the keys ``cluster``,
    ``default_resources``, ``max_attempts``, ``resource_scales`` and ``workflow_name``."""
    runs: list[JobmonRun] = field(default_factory=list, init=False)
    """Every run that :meth:`bind` returned inside the block, in the order bound, so a
    test can read a run's status, task statuses and error after the build."""
    _replaced: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    """The adapter's own functions while this object's replace them, by name."""

    def __enter__(self) -> FakeJobmon:
        """Replace the adapter's functions with this object's."""
        for name in _ADAPTER_FUNCTIONS:
            self._replaced[name] = getattr(_jobmon_adapter, name)
            setattr(_jobmon_adapter, name, getattr(self, name))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Restore the adapter's own functions."""
        for name, function in self._replaced.items():
            setattr(_jobmon_adapter, name, function)
        self._replaced.clear()

    def check_environment(self, cluster: str) -> None:
        """Accept any cluster, since the fake needs neither Jobmon nor Slurm."""

    def bind(
        self,
        plan: Sequence[PlanEntry],
        *,
        cluster: str,
        default_resources: Mapping[str, Any],
        max_attempts: int,
        resource_scales: Mapping[str, float] | None,
        workflow_name: str,
    ) -> JobmonRun:
        """Record a plan, its options and its run, and return the run with no workflow id.

        Parameters
        ----------
        plan
            The entries, each after every entry it waits for.
        cluster
            Recorded in :attr:`options`.
        default_resources
            Recorded in :attr:`options`.
        max_attempts
            Recorded in :attr:`options`.
        resource_scales
            Recorded in :attr:`options`.
        workflow_name
            Recorded in :attr:`options`.

        Returns
        -------
            A run of ``plan`` whose ``workflow_id`` is ``None``, which is also appended to
            :attr:`runs`.

        Raises
        ------
        KeyError
            If an entry names an upstream that is not an earlier entry of ``plan``, as the
            real adapter does. Nothing is then recorded.
        """
        entries = tuple(plan)
        _jobmon_adapter._check_plan_order(entries)
        self.plans.append(list(entries))
        self.options.append(
            {
                "cluster": cluster,
                "default_resources": default_resources,
                "max_attempts": max_attempts,
                "resource_scales": resource_scales,
                "workflow_name": workflow_name,
            }
        )
        jobmon_run = JobmonRun(plan=entries, workflow_id=None)
        self.runs.append(jobmon_run)
        return jobmon_run

    def run(self, jobmon_run: JobmonRun) -> None:
        """Run each entry's command with ``/bin/sh`` in plan order, and set the statuses.

        An entry whose command exits with status 0 gets the task status ``"D"``, and one
        that exits otherwise gets ``"F"``. An entry with an upstream whose status is not
        ``"D"`` is not run and gets ``"G"``. The run's status is ``"D"`` when every entry
        is ``"D"``, and ``"E"`` otherwise, and its error stays ``None``.

        With :attr:`error_after` set, only that many entries, in plan order, are run or
        withheld in this way, and no later entry runs. The run then ends as the real
        adapter's does when Jobmon's ``Workflow.run`` raises: every entry gets the task
        status ``None``, because Jobmon gives no task a final status then, the run's
        status stays ``None``, and its error is a :class:`RuntimeError` whose message
        says that the fake stopped after that many entries. The method still returns
        normally.

        Parameters
        ----------
        jobmon_run
            A run that :meth:`bind` returned.
        """
        statuses: dict[str, str | None] = {}
        for index, entry in enumerate(jobmon_run.plan):
            if self.error_after is not None and index >= self.error_after:
                statuses[entry.signature] = None
                continue
            if any(statuses.get(upstream) != "D" for upstream in entry.upstreams):
                statuses[entry.signature] = "G"
                continue
            finished = subprocess.run(["/bin/sh", "-c", entry.command], check=False)
            statuses[entry.signature] = "D" if finished.returncode == 0 else "F"
        if self.error_after is not None:
            jobmon_run.task_statuses = {signature: None for signature in statuses}
            jobmon_run.error = RuntimeError(
                f"FakeJobmon stopped the workflow after {self.error_after} entries, as "
                "its error_after asks."
            )
            return
        jobmon_run.task_statuses = statuses
        all_done = all(status == "D" for status in statuses.values())
        jobmon_run.status = "D" if all_done else "E"
