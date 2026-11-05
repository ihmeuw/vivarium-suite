"""Helpers shared by the bridge's settlement, interrupt and state tests.

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
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge.execute import RemoteTaskFailed
from vivarium.dagger.bridge.state import RUNS_DIR, STATE_FILE
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

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
"""An ANSI escape sequence, which pytask's console writes when it styles its output."""


def registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry whose products go under ``tmp_path / "out"``."""
    return ArtifactRegistry(output_root=tmp_path / "out", run_name="run1")


def stable(tasks: Sequence[Task]) -> list[Task]:
    """Return ``tasks``, each with a stable code identity."""
    return [replace(task, code_id=stable_code_id(f"{task.name}::code")) for task in tasks]


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


def remote_failure(report: ExecutionReport) -> RemoteTaskFailed:
    """Return the RemoteTaskFailed that a FAIL report holds."""
    assert report.outcome == TaskOutcome.FAIL
    assert report.exc_info is not None
    error = report.exc_info[1]
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
