"""Tests for the shared staleness test harness (``harness``)."""

import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import TaskOutcome

from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import PlanEntry
from vivarium.workflow.harness import (
    FakeJobmon,
    Fixture,
    Perturbed,
    build_fixture,
    run,
    stable_code_id,
    would_run,
)

_BIND_OPTIONS: dict[str, Any] = {
    "cluster": "slurm",
    "default_resources": {},
    "max_attempts": 2,
    "resource_scales": None,
    "workflow_name": "fake_workflow",
}
"""Options for a bind whose values a test does not inspect."""


def _with_code_version(fx: Fixture, version: str) -> Fixture:
    """Return ``fx`` with every task given a stable code identity at ``version``."""
    tasks = [
        replace(task, code_id=stable_code_id(f"{task.name}::code", version))
        for task in fx.tasks
    ]
    return replace(fx, tasks=tasks)


def _entry(name: str, command: str, *upstreams: str) -> PlanEntry:
    """Return a plan entry for step ``name`` that runs ``command`` after ``upstreams``."""
    return PlanEntry(
        signature=f"sig-{name}",
        name=name,
        command=command,
        upstreams=tuple(f"sig-{upstream}" for upstream in upstreams),
        resources={},
    )


def _appends(name: str, log: Path) -> str:
    """Return a shell command that appends ``name`` to ``log`` as a line."""
    return f"echo {name} >> {shlex.quote(str(log))}"


def _adapter_functions() -> tuple[object, object, object]:
    """Return the adapter's check_environment, bind and run, as the bridge looks them up."""
    return _jobmon_adapter.check_environment, _jobmon_adapter.bind, _jobmon_adapter.run


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


def test_stable_code_id_lets_an_unchanged_step_skip(tmp_path: Path) -> None:
    """A step whose code_id is stable_code_id runs on the first build and is SKIP_UNCHANGED on the second."""
    fx = _with_code_version(build_fixture(tmp_path), "1")

    assert run(fx) == dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    assert run(fx) == dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)


def test_changing_the_stable_code_id_version_reruns_the_step(tmp_path: Path) -> None:
    """Rebuilding with the same step under a new stable_code_id version runs it again."""
    fx = build_fixture(tmp_path)
    ran = dict.fromkeys(fx.steps, TaskOutcome.SUCCESS)
    skipped = dict.fromkeys(fx.steps, TaskOutcome.SKIP_UNCHANGED)

    assert run(_with_code_version(fx, "1")) == ran
    assert run(_with_code_version(fx, "2")) == ran
    assert run(_with_code_version(fx, "2")) == skipped


def test_stable_code_id_state_is_identical_in_another_process() -> None:
    """stable_code_id with the same name and version reports the same state in a fresh interpreter, so a lockfile written by one process matches in another."""
    name = "step::code"
    version = "2"
    code = (
        "from vivarium.workflow.harness import stable_code_id\n"
        f"print(stable_code_id({name!r}, {version!r}).state(), end='')"
    )

    local = stable_code_id(name, version).state()
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert local is not None
    assert result.stdout == local


# FakeJobmon


def test_fake_jobmon_replaces_the_adapter_functions_only_inside_its_block() -> None:
    """Inside the block the adapter's check_environment, bind and run are the fake's; after the block, also when the block raises, they are the adapter's own again."""
    own = _adapter_functions()

    with FakeJobmon() as jobmon:
        inside = _adapter_functions()
    after_block = _adapter_functions()
    with pytest.raises(RuntimeError, match="boom"):
        with FakeJobmon():
            raise RuntimeError("boom")
    after_raise = _adapter_functions()

    assert inside == (jobmon.check_environment, jobmon.bind, jobmon.run)
    assert after_block == own
    assert after_raise == own


def test_fake_jobmon_check_environment_accepts_any_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the block the adapter's check_environment returns None for 'slurm' and for 'multiprocess', with no jobmon and no scontrol."""
    monkeypatch.setenv("PATH", str(tmp_path))

    with FakeJobmon():
        _jobmon_adapter.check_environment("slurm")
        _jobmon_adapter.check_environment("multiprocess")


def test_fake_jobmon_records_each_bound_plan_and_its_options() -> None:
    """Two binds inside one block leave two plans in bind order and two options dicts holding cluster, default_resources, max_attempts, resource_scales and workflow_name as passed."""
    first = [_entry("a", "true")]
    second = [_entry("b", "true"), _entry("c", "true", "b")]
    second_options: dict[str, Any] = {
        "cluster": "multiprocess",
        "default_resources": {"queue": "null.q"},
        "max_attempts": 4,
        "resource_scales": {"memory": 0.25},
        "workflow_name": "second_workflow",
    }

    with FakeJobmon() as jobmon:
        _jobmon_adapter.bind(first, **_BIND_OPTIONS)
        _jobmon_adapter.bind(second, **second_options)

    assert jobmon.plans == [first, second]
    assert jobmon.options == [_BIND_OPTIONS, second_options]


def test_fake_jobmon_bind_returns_a_run_of_the_plan_with_no_workflow_id() -> None:
    """bind returns a JobmonRun whose plan is the bound entries, whose workflow_id is None, and whose status is None."""
    plan = [_entry("a", "true"), _entry("b", "true", "a")]

    with FakeJobmon():
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)

    assert jobmon_run.plan == tuple(plan)
    assert jobmon_run.workflow_id is None
    assert jobmon_run.status is None


def test_fake_jobmon_bind_refuses_an_upstream_that_is_not_an_earlier_entry() -> None:
    """bind raises KeyError when an entry names as upstream a signature that only a later entry has, and records no plan."""
    plan = [_entry("b", "true", "a"), _entry("a", "true")]

    with FakeJobmon() as jobmon:
        with pytest.raises(KeyError):
            _jobmon_adapter.bind(plan, **_BIND_OPTIONS)

    assert jobmon.plans == []


def test_fake_jobmon_runs_each_command_in_plan_order(tmp_path: Path) -> None:
    """run runs each entry's shell command once, in plan order (each appends its name to one file), and sets every task status and the run's status to 'D'."""
    log = tmp_path / "ran.log"
    plan = [
        _entry("zeta", _appends("zeta", log)),
        _entry("alpha", _appends("alpha", log), "zeta"),
        _entry("mid", _appends("mid", log)),
    ]

    with FakeJobmon():
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)
        _jobmon_adapter.run(jobmon_run)

    assert log.read_text().splitlines() == ["zeta", "alpha", "mid"]
    assert jobmon_run.task_statuses == {"sig-zeta": "D", "sig-alpha": "D", "sig-mid": "D"}
    assert jobmon_run.status == "D"


def test_fake_jobmon_withholds_an_entry_whose_upstream_failed(tmp_path: Path) -> None:
    """When an entry's command exits 1, its task status is 'F', the entry that waits for it is not run and is 'G', an independent entry still runs and is 'D', and the run's status is 'E'."""
    log = tmp_path / "ran.log"
    plan = [
        _entry("fails", "exit 1"),
        _entry("after", _appends("after", log), "fails"),
        _entry("independent", _appends("independent", log)),
    ]

    with FakeJobmon():
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)
        _jobmon_adapter.run(jobmon_run)

    expected = {"sig-fails": "F", "sig-after": "G", "sig-independent": "D"}
    assert log.read_text().splitlines() == ["independent"]
    assert jobmon_run.task_statuses == expected
    assert jobmon_run.status == "E"


def test_fake_jobmon_runs_commands_in_child_processes(tmp_path: Path) -> None:
    """An entry whose command writes its shell's own process id ($$) shows a process other than the test's own."""
    pid_file = tmp_path / "pid.txt"
    plan = [_entry("pid", f"echo $$ > {shlex.quote(str(pid_file))}")]

    with FakeJobmon():
        _jobmon_adapter.run(_jobmon_adapter.bind(plan, **_BIND_OPTIONS))

    assert int(pid_file.read_text()) != os.getpid()


def test_fake_jobmon_keeps_each_run_that_bind_returns() -> None:
    """Two binds inside one block leave in runs, in bind order, the same two JobmonRun objects that bind returned."""
    with FakeJobmon() as jobmon:
        first = _jobmon_adapter.bind([_entry("a", "true")], **_BIND_OPTIONS)
        second = _jobmon_adapter.bind([_entry("b", "true")], **_BIND_OPTIONS)

    assert len(jobmon.runs) == 2
    assert jobmon.runs[0] is first
    assert jobmon.runs[1] is second


def test_fake_jobmon_run_leaves_the_runs_error_none_without_error_after(
    tmp_path: Path,
) -> None:
    """Without error_after, run of a plan whose commands all exit 0 sets the run's status to 'D' and leaves its error None."""
    log = tmp_path / "ran.log"
    plan = [_entry("a", _appends("a", log)), _entry("b", _appends("b", log), "a")]

    with FakeJobmon():
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)
        _jobmon_adapter.run(jobmon_run)

    assert log.read_text().splitlines() == ["a", "b"]
    assert jobmon_run.status == "D"
    assert jobmon_run.error is None


def test_fake_jobmon_error_after_stops_the_run_with_a_runtime_error(tmp_path: Path) -> None:
    """With error_after=1, run of a three-entry plan runs only the first entry's command, sets every task status to None as the real adapter does after a run error, leaves the run's status None, sets its error to a RuntimeError, and returns normally."""
    log = tmp_path / "ran.log"
    plan = [_entry(name, _appends(name, log)) for name in ("first", "second", "third")]

    with FakeJobmon(error_after=1):
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)
        _jobmon_adapter.run(jobmon_run)

    expected = dict.fromkeys(["sig-first", "sig-second", "sig-third"], None)
    assert log.read_text().splitlines() == ["first"]
    assert jobmon_run.task_statuses == expected
    assert jobmon_run.status is None
    assert isinstance(jobmon_run.error, RuntimeError), repr(jobmon_run.error)


def test_fake_jobmon_error_after_counts_an_entry_that_it_withholds(tmp_path: Path) -> None:
    """With error_after=2, when the first entry exits 1 and the second waits for it, the withheld second entry counts toward the two, so the third entry's command does not run, and every task status is None."""
    log = tmp_path / "ran.log"
    plan = [
        _entry("fails", "exit 1"),
        _entry("after", _appends("after", log), "fails"),
        _entry("third", _appends("third", log)),
    ]

    with FakeJobmon(error_after=2):
        jobmon_run = _jobmon_adapter.bind(plan, **_BIND_OPTIONS)
        _jobmon_adapter.run(jobmon_run)

    expected = dict.fromkeys(["sig-fails", "sig-after", "sig-third"], None)
    assert not log.exists()
    assert jobmon_run.task_statuses == expected
    assert isinstance(jobmon_run.error, RuntimeError), repr(jobmon_run.error)
