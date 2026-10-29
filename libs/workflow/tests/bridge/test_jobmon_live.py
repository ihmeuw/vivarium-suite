"""Opt-in test of the bridge against the production Jobmon server.

The test runs only where jobmon is installed and ``VIVARIUM_WORKFLOW_JOBMON_LIVE`` is set
to ``1``; everywhere else, including CI, it is skipped. It runs on Jobmon's multiprocess
plugin, so it needs no Slurm, but it writes tool, workflow and task rows to the production
Jobmon database under the tool ``vivarium.workflow.bridge``, as the dagger and psimulate
end-to-end tests do.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from pytask import ExitCode

from vivarium.workflow.backend.build import build
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import JobmonRun
from vivarium.workflow.harness import build_fixture

LIVE_ENV_VAR = "VIVARIUM_WORKFLOW_JOBMON_LIVE"
"""The environment variable that must be ``1`` for this module's test to run."""

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jobmon") is None or os.environ.get(LIVE_ENV_VAR) != "1",
    reason=f"needs jobmon installed and {LIVE_ENV_VAR}=1",
)

_MULTIPROCESS_RESOURCES = {"queue": "null.q", "cores": 1, "memory": 1, "runtime": 600}
"""Compute resources for each step on Jobmon's multiprocess plugin. The queue name is the
one that the bridge's proof of concept used."""


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
