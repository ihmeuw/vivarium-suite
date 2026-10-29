"""Opt-in tests of the bridge against the production Jobmon server.

The tests run only where jobmon is installed and ``VIVARIUM_WORKFLOW_JOBMON_LIVE`` is set
to ``1``; everywhere else, including CI, they are skipped. They run on Jobmon's
multiprocess plugin, so they need no Slurm, but they write tool, workflow and task rows to
the production Jobmon database under the tool ``vivarium.workflow.bridge``, as the dagger
and psimulate end-to-end tests do. Besides running a two-step chain through Jobmon, they
check that a three-step chain is recorded and a second build binds nothing, and that
Jobmon's progress lines reach standard error.
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pytask import ExitCode, TaskOutcome

from vivarium.workflow import harness
from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.workflow.frontend.python_step import python_step
from vivarium.workflow.harness import build_fixture, stable_code_id

LIVE_ENV_VAR = "VIVARIUM_WORKFLOW_JOBMON_LIVE"
"""The environment variable that must be ``1`` for this module's tests to run."""

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jobmon") is None or os.environ.get(LIVE_ENV_VAR) != "1",
    reason=f"needs jobmon installed and {LIVE_ENV_VAR}=1",
)

_MULTIPROCESS_RESOURCES = {"queue": "null.q", "cores": 1, "memory": 1, "runtime": 600}
"""Compute resources for each step on Jobmon's multiprocess plugin. The queue name is the
one that the bridge's proof of concept used."""

_FINISHED = re.compile(r"Workflow 100(\.0+)?% complete \(2/2 tasks\)")
"""Jobmon's last progress line for the harness fixture's two steps. Jobmon writes the
percentage as a float, as in ``Workflow 100.0% complete (2/2 tasks)``."""


def test_a_two_step_chain_runs_through_jobmon_on_the_multiprocess_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end against the production server: the harness fixture built with jobmon=True and jobmon_cluster='multiprocess', with no fake, exits OK and writes both products, and the bound workflow ends with status 'D' for the workflow and for each task."""
    fx = build_fixture(tmp_path)
    runs: list[JobmonRun] = []
    adapter_run = _jobmon_adapter.run

    def run_and_keep(jobmon_run: JobmonRun) -> None:
        """Run the workflow with the adapter's own run, then keep it for the assertions."""
        adapter_run(jobmon_run)
        runs.append(jobmon_run)

    monkeypatch.setattr(_jobmon_adapter, "run", run_and_keep)

    session = build(
        fx.tasks,
        fx.state,
        jobmon=True,
        jobmon_cluster="multiprocess",
        jobmon_resources=_MULTIPROCESS_RESOURCES,
    )

    assert session.exit_code == ExitCode.OK
    assert all(fx.registry.path_for(step).exists() for step in fx.steps)
    [jobmon_run] = runs
    assert jobmon_run.workflow_id is not None
    assert jobmon_run.status == "D"
    signatures = {entry.signature for entry in jobmon_run.plan}
    assert jobmon_run.task_statuses == dict.fromkeys(signatures, "D")


def test_a_three_step_chain_is_recorded_and_a_second_build_submits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end against the production server: a three-step chain with stable code identities, built with jobmon=True on the multiprocess plugin, ends with the run's status 'D' and error None and reports three SUCCESS; a second build of the same tasks reports SKIP_UNCHANGED for all three and binds no workflow."""
    fx = build_fixture(tmp_path)
    # The harness's targets import on a node without the test suite on PYTHONPATH.
    last = python_step(
        harness._offset,
        inputs={"mid": "final.parquet"},
        produces="last.parquet",
        registry=fx.registry,
    )
    chain = [*fx.tasks, last]
    tasks = [replace(task, code_id=stable_code_id(f"{task.name}::code")) for task in chain]
    names = [task.name for task in tasks]
    bound: list[JobmonRun] = []
    adapter_bind = _jobmon_adapter.bind

    def bind_and_keep(plan: Sequence[PlanEntry], **options: Any) -> JobmonRun:
        """Bind with the adapter's own bind, then keep the run for the assertions."""
        jobmon_run = adapter_bind(plan, **options)
        bound.append(jobmon_run)
        return jobmon_run

    monkeypatch.setattr(_jobmon_adapter, "bind", bind_and_keep)
    options: dict[str, Any] = {
        "jobmon": True,
        "jobmon_cluster": "multiprocess",
        "jobmon_resources": _MULTIPROCESS_RESOURCES,
    }

    first = build(tasks, fx.state, **options)
    second = build(tasks, fx.state, **options)

    [jobmon_run] = bound
    assert jobmon_run.status == "D"
    assert jobmon_run.error is None
    assert outcomes(first) == dict.fromkeys(names, TaskOutcome.SUCCESS)
    assert outcomes(second) == dict.fromkeys(names, TaskOutcome.SKIP_UNCHANGED)


def test_jobmon_progress_lines_reach_stderr_during_a_live_build(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Against the production server, a build of the harness fixture with jobmon=True on the multiprocess plugin writes Jobmon's last progress line, 'Workflow 100.0% complete (2/2 tasks)', to standard error; the check accepts the percentage with any number of zeros after the decimal point, or none."""
    fx = build_fixture(tmp_path)

    session = build(
        fx.tasks,
        fx.state,
        jobmon=True,
        jobmon_cluster="multiprocess",
        jobmon_resources=_MULTIPROCESS_RESOURCES,
    )

    assert session.exit_code == ExitCode.OK
    err = capfd.readouterr().err
    assert _FINISHED.search(err) is not None, err
