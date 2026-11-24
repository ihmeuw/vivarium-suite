"""Opt-in tests of the bridge against the production Jobmon server.

The tests run only where jobmon is installed and ``VIVARIUM_DAGGER_JOBMON_LIVE`` is set
to ``1``; everywhere else, including CI, they are skipped. They run on Jobmon's
multiprocess plugin, so they need no Slurm, but they write tool, workflow and task rows to
the production Jobmon database under the tool ``vivarium.dagger.bridge``, as the dagger
and psimulate end-to-end tests do. Besides running a two-step chain through Jobmon, they
check that a three-step chain is recorded and a second build binds nothing, that
Jobmon's progress lines reach standard error, and that a step is recorded while the
workflow is still running.
"""

import importlib.util
import os
import re
import shlex
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pytask import ExitCode, TaskOutcome

from tests.bridge.helpers import journal_holds, lockfile_entries, wait_until
from vivarium.dagger import harness
from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge import _jobmon_adapter, execute
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import build_fixture, with_stable_code_id

LIVE_ENV_VAR = "VIVARIUM_DAGGER_JOBMON_LIVE"
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

_LIVE_RECORD_INTERVAL = 1.0
"""The recording interval, in seconds, of the build whose first step must be recorded
while its workflow runs."""

_JOURNAL_WAIT_SECONDS = 180.0
"""The most seconds that a test waits for a step's lockfile journal entry while the live
workflow runs."""

_GATE_WAIT_SECONDS = 300
"""The most seconds that the gated step of :func:`_gated_chain` waits for its gate file.
It is longer than :data:`_JOURNAL_WAIT_SECONDS`, so the test writes the gate file first."""


def _gated_chain(directory: Path) -> tuple[list[Task], Path]:
    """Return a chain of two command steps, and the gate file that the second one waits for.

    The second step copies the first step's output once the gate file exists, and fails
    if the gate file does not appear within :data:`_GATE_WAIT_SECONDS` seconds. Command
    steps import nothing on a node, so the node needs no test module on its PYTHONPATH.
    """
    first_output = directory / "first.txt"
    second_output = directory / "second.txt"
    gate = directory / "gate"
    source, target, gate_file = (
        shlex.quote(str(path)) for path in (first_output, second_output, gate)
    )
    first = Task(
        name="writes_first",
        run=f"echo first > {source}",
        inputs={},
        outputs={"first": file_node(first_output)},
        resources={},
        env=None,
    )
    second = Task(
        name="waits_for_the_gate",
        run=(
            f"i=0; while [ ! -e {gate_file} ] && [ $i -lt {_GATE_WAIT_SECONDS} ]; "
            f"do sleep 1; i=$((i+1)); done; [ -e {gate_file} ] && cp {source} {target}"
        ),
        inputs={"first": file_node(first_output)},
        outputs={"second": file_node(second_output)},
        resources={},
        env=None,
    )
    return [first, second], gate


def test_a_two_step_chain_runs_through_jobmon_on_the_multiprocess_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end against the production server: the harness fixture built with jobmon=True and jobmon_cluster='multiprocess', with no fake, exits OK and writes both outputs, and the bound workflow ends with status 'D' for the workflow and for each task."""
    fx = build_fixture(tmp_path)
    runs: list[JobmonRun] = []
    adapter_run = _jobmon_adapter.run

    def run_and_keep(jobmon_run: JobmonRun, **options: Any) -> None:
        """Run the workflow with the adapter's own run, then keep it for the assertions."""
        adapter_run(jobmon_run, **options)
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
        outputs="last.parquet",
        registry=fx.registry,
    )
    chain = [*fx.tasks, last]
    tasks = [with_stable_code_id(task) for task in chain]
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


def test_a_step_is_recorded_before_the_live_workflow_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end against the production server on the multiprocess plugin: in a two-step chain whose second step waits for a file that the test writes only after it has seen the first step's lockfile journal entry, that entry appears while the workflow is still running, and the build then exits OK with both steps recorded."""
    tasks, gate = _gated_chain(tmp_path)
    first, second = (task.name for task in tasks)
    root = tmp_path / "state"
    run_returned = threading.Event()
    build_ended = threading.Event()
    seen: list[tuple[bool, bool]] = []
    adapter_run = _jobmon_adapter.run

    def run_and_note(jobmon_run: JobmonRun, **options: Any) -> None:
        """Run the workflow with the adapter's own run, then note that it has returned."""
        try:
            adapter_run(jobmon_run, **options)
        finally:
            run_returned.set()

    def open_the_gate_once_recorded() -> None:
        """Wait for the first step's journal entry, note what there is, and write the gate."""
        wait_until(
            lambda: build_ended.is_set() or journal_holds(root, [first]),
            limit=_JOURNAL_WAIT_SECONDS,
        )
        seen.append((journal_holds(root, [first]), run_returned.is_set()))
        # Written also when the entry did not appear, so that the workflow can end.
        gate.touch()

    monkeypatch.setattr(_jobmon_adapter, "run", run_and_note)
    monkeypatch.setattr(execute, "_RECORD_INTERVAL_SECONDS", _LIVE_RECORD_INTERVAL)
    # The adapter's run blocks this thread, so a second thread watches the journal.
    watcher = threading.Thread(target=open_the_gate_once_recorded)
    watcher.start()
    try:
        session = build(
            tasks,
            root,
            jobmon=True,
            jobmon_cluster="multiprocess",
            jobmon_resources=_MULTIPROCESS_RESOURCES,
        )
    finally:
        build_ended.set()
        watcher.join(timeout=_JOURNAL_WAIT_SECONDS)

    [(in_the_journal, after_the_run)] = seen
    assert in_the_journal, "The first step had no journal entry before the gate was written."
    assert not after_the_run
    assert session.exit_code == ExitCode.OK
    assert outcomes(session) == dict.fromkeys([first, second], TaskOutcome.SUCCESS)
    assert set(lockfile_entries(root)) == {first, second}
