"""Helpers shared by the bridge's tests.

The pipelines here run their steps through ``tests.bridge.step_targets``, so a test that
builds them with ``jobmon=True`` needs the ``node_pythonpath`` fixture.
"""

import json
import re
import tomllib
from collections.abc import Collection, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
from pytask import ExecutionReport, Session, TaskOutcome

from tests.bridge import step_targets
from vivarium.dagger.backend.build import build
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge.execute import RemoteTaskFailed
from vivarium.dagger.bridge.state import RUNS_DIR, STATE_FILE, RunState, read_run_state
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon, stable_code_id

LOCKFILE = "pytask.lock"
"""The name of pytask's lockfile in a build's root."""

JOURNAL = "pytask.lock.journal"
"""The name of the journal in a build's root that pytask appends each lockfile entry to
while the build runs."""

WORKFLOW_ID = 4242
"""The workflow id that the tests give ``FakeJobmon``."""

CHAIN = ("a.parquet", "b.parquet", "c.parquet")
"""The steps of the chain in :func:`chain_and_independent`, in plan order."""

INDEPENDENT = "d.parquet"
"""The step of :func:`chain_and_independent` that no other step depends on."""

A, B, C = CHAIN
"""The steps of the chain a -> b -> c."""

D = INDEPENDENT
"""The step that no other step depends on."""

PLAN_ORDER = (A, D, B, C)
"""The order in which the bridge plans a -> b -> c plus d: by wave, then by name."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
"""An ANSI escape sequence, which pytask's console writes when it styles its output."""


def registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry whose products go under ``tmp_path / "out"``."""
    return ArtifactRegistry(output_root=tmp_path / "out", run_name="run1")


def make_dir(path: Path) -> Path:
    """Create ``path`` and its parents if missing, and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def stable(tasks: Sequence[Task], changed: Collection[str] = ()) -> list[Task]:
    """Return ``tasks`` with stable code identities, changed for the steps in ``changed``."""
    return [
        replace(
            task,
            code_id=stable_code_id(
                f"{task.name}::code", "2" if task.name in changed else "1"
            ),
        )
        for task in tasks
    ]


def chain_and_independent(
    artifacts: ArtifactRegistry,
    *,
    failing: Collection[str] = (),
    killed: Collection[str] = (),
) -> list[Task]:
    """Return the chain ``a -> b -> c`` and the independent step ``d``, in that order.

    Each step records its node's process id in its product, after the ids its upstream
    recorded. A step named in ``failing`` raises ``StepFailure`` on its node instead, and
    one named in ``killed`` ends its runner without a result.
    """
    upstream_of = {"b.parquet": "a.parquet", "c.parquet": "b.parquet"}
    tasks: list[Task] = []
    for name in (*CHAIN, INDEPENDENT):
        if name in failing:
            target = step_targets.raise_step_failure_reading
        elif name in killed:
            target = step_targets.exit_without_a_result
        else:
            target = step_targets.record_pid
        inputs = {"upstream": upstream_of[name]} if name in upstream_of else {}
        tasks.append(python_step(target, inputs=inputs, produces=name, registry=artifacts))
    return tasks


def recorded_pids(artifacts: ArtifactRegistry, names: Collection[str]) -> set[int]:
    """Return every process id recorded in the products of ``names`` that exist."""
    pids: set[int] = set()
    for name in names:
        path = artifacts.path_for(name)
        if path.exists():
            pids.update(int(pid) for pid in pd.read_parquet(path)["pid"])
    return pids


def report_for(session: Session, name: str) -> ExecutionReport:
    """Return the session's one report for the step called ``name``."""
    [report] = [report for report in session.execution_reports if report.task.name == name]
    return report


def report_counts(session: Session) -> dict[str, int]:
    """Return how many reports the session holds for each step, by name."""
    counts: dict[str, int] = {}
    for report in session.execution_reports:
        counts[report.task.name] = counts.get(report.task.name, 0) + 1
    return counts


def failure_of(report: ExecutionReport) -> BaseException:
    """Return the exception that a FAIL report holds."""
    assert report.outcome == TaskOutcome.FAIL
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert error is not None
    return error


def remote_failure(report: ExecutionReport) -> RemoteTaskFailed:
    """Return the RemoteTaskFailed that a FAIL report holds."""
    error = failure_of(report)
    assert isinstance(error, RemoteTaskFailed), repr(error)
    return error


def lockfile_entries(root: Path) -> dict[str, dict[str, Any]]:
    """Return the entries of ``root``'s pytask.lock by task id, or none if it is missing."""
    path = root / LOCKFILE
    if not path.exists():
        return {}
    lockfile = tomllib.loads(path.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in lockfile.get("task", [])}


def journal_ids(root: Path) -> set[str]:
    """Return the task ids in ``root``'s lockfile journal, or none if there is none."""
    path = root / JOURNAL
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["id"] for line in lines if line.strip()}


def bound_names(jobmon: FakeJobmon) -> list[list[str]]:
    """Return the step names of each plan that ``jobmon`` bound, in plan order."""
    return [[entry.name for entry in plan] for plan in jobmon.plans]


def state_files(root: Path) -> list[Path]:
    """Return the state files of every run under ``root``, sorted by path."""
    return sorted((root / RUNS_DIR).glob(f"*/{STATE_FILE}"))


def squashed(text: str) -> str:
    """Return ``text`` without ANSI escapes or whitespace, so that wrapped lines join up."""
    return "".join(_ANSI.sub("", text).split())


def build_root(tmp_path: Path) -> Path:
    """Return the root of the builds under ``tmp_path``."""
    return tmp_path / "state"


def chain_build(
    tmp_path: Path,
    jobmon: FakeJobmon,
    *,
    failing: Collection[str] = (),
    killed: Collection[str] = (),
    stable_ids: bool = False,
    **options: Any,
) -> Session:
    """Build a -> b -> c plus d under ``tmp_path`` with jobmon=True through ``jobmon``.

    The products go under ``tmp_path / "out"`` and the root is :func:`build_root`. A step
    named in ``failing`` raises on its node, and one named in ``killed`` ends its runner
    without a result. ``stable_ids`` gives every step a stable code identity.
    """
    tasks = chain_and_independent(registry(tmp_path), failing=failing, killed=killed)
    if stable_ids:
        tasks = stable(tasks)
    with jobmon:
        return build(tasks, build_root(tmp_path), jobmon=True, **options)


def only_state(root: Path) -> RunState:
    """Return the state of the one run under ``root``."""
    [path] = state_files(root)
    return read_run_state(path)
