"""Tests for ``dagger run`` and ``dagger force`` on a pipeline definition through Jobmon.

Each test runs the ``dagger`` command in this process with click's ``CliRunner`` and the
fake Jobmon adapter, so every step runs in the real node runner in a child process
(MIC-7752).
"""

import os
import re
import signal
import subprocess
import sys
import traceback
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.backend.lock_helpers import account_name, held_lock
from tests.bridge.helpers import LOCKFILE, bound_names, make_dir, squashed
from vivarium.dagger.backend.lock import RUN_LOCK_NAME
from vivarium.dagger.bridge import _jobmon_adapter
from vivarium.dagger.bridge.plugin import DEFAULT_MAX_ATTEMPTS
from vivarium.dagger.bridge.state import RUNS_DIR
from vivarium.dagger.cli import dagger
from vivarium.dagger.constants import DEFAULT_RESOURCES
from vivarium.dagger.harness import FakeJobmon

_DEFINITION = """\
import os
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, python_step
from vivarium.dagger.harness import with_stable_code_id

HERE = Path(__file__).resolve().parent


def log(label):
    with open(HERE / "calls.log", "a") as file:
        print(label, os.getpid(), file=file)


def make_a():
    log("a")
    return pd.DataFrame({"n": [1, 2, 3]})


def make_b(source):
    log("b")
    return pd.read_parquet(source) + 1


def explode_on_node(source):
    log("b")
    raise ValueError("bad input")


def make_c(source):
    log("c")
    return pd.read_parquet(source) + 1


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    first, second, _ = NAMES
    targets = [make_a, explode_on_node if FAILING else make_b, make_c]
    sources = [{}, {"source": f"{first}.parquet"}, {"source": f"{second}.parquet"}]
    tasks = [
        python_step(
            target,
            inputs=inputs,
            produces=f"{name}.parquet",
            registry=registry,
            resources=STEP_RESOURCES.get(name),
            name=name,
        )
        for name, target, inputs in zip(NAMES, targets, sources)
    ]
    stable = [with_stable_code_id(task) for task in tasks]
    return Pipeline(stable, HERE / "state", registry, resources=PIPELINE_RESOURCES)
"""
"""The start of a definition of the chain NAMES[0] -> NAMES[1] -> NAMES[2] with stable code
identities. Each step body logs its position ('a', 'b' or 'c') and its process id. The
middle step raises ValueError('bad input') when FAILING is true. The settings that
:func:`_definition` appends give NAMES, FAILING, STEP_RESOURCES and PIPELINE_RESOURCES."""

_NAMES = ("a", "b", "c")
"""The step names of most definitions here."""

_RAISE_LINE = 'raise ValueError("bad input")'
"""The source line of the failing step that only a traceback shows."""

_WITH_HELPER = """\
import os
from pathlib import Path

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, python_step

import HELPER as helper

HERE = Path(__file__).resolve().parent


def make_table():
    seen = f"{os.getpid()}\\n{os.environ.get('PYTHONPATH', '')}"
    (HERE / "node.txt").write_text(seen)
    return helper.table()


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    step = python_step(
        make_table, inputs={}, produces="table.parquet", registry=registry, name="table"
    )
    return Pipeline([step], HERE / "state", registry)
"""
"""A definition of one step whose function, defined in the file, calls a helper module
named HELPER that sits beside the file, and writes its process id and PYTHONPATH to
node.txt."""

_HELPER = """\
import pandas as pd


def table():
    return pd.DataFrame({"n": [1, 2, 3]})
"""
"""The helper module that :data:`_WITH_HELPER` imports."""

_SUPPORTED_JOBMON = {
    "jobmon_client": "3.7.9",
    "jobmon_core": "3.7.19",
    "jobmon_slurm": "1.15.0",
}
"""Installed versions of the Jobmon distributions that the bridge supports."""

_CLUSTER_EXTRA = "vivarium-dagger[cluster]"
"""The extra that the message for a missing Jobmon says to install."""

_STEP_RESOURCES = re.compile(r"(?<![\w(])resources=")
"""A step's own ``resources=``, which neither ``Pipeline(resources=`` nor
``jobmon_resources=`` matches."""

_FRESH_RUN = """
import sys

from click.testing import CliRunner

from vivarium.dagger.cli import dagger
from vivarium.dagger.harness import FakeJobmon

with FakeJobmon() as jobmon:
    result = CliRunner().invoke(dagger, ["run", "-c", sys.argv[1], "-P", "proj_test"])
assert result.exit_code == 0, result.output
assert jobmon.plans, "no plan was bound"
loaded = sorted(name for name in sys.modules if name.partition(".")[0] == "jobmon")
print("jobmon modules:", loaded)
"""
"""A script that runs ``dagger run`` on the definition its first argument names through
FakeJobmon, then prints the jobmon modules it imported."""

_CHILD_TIMEOUT = 300
"""The most seconds that a child Python process of these tests may take."""


def _loaded_from(module: object, directory: Path) -> bool:
    """Return whether ``module``'s file is under ``directory`` or is a relative path."""
    file = getattr(module, "__file__", None)
    if not isinstance(file, str):
        return False
    path = Path(file)
    return not path.is_absolute() or path.resolve().is_relative_to(directory.resolve())


@pytest.fixture(autouse=True)
def _forget_loaded_definitions(tmp_path: Path) -> Iterator[None]:
    """Restore ``sys.path`` and drop the modules a test loaded from its definition files."""
    path = list(sys.path)
    modules = set(sys.modules)
    yield
    sys.path[:] = path
    # A compiled extension that a step imported lazily cannot be imported a second time,
    # so only the modules loaded from the test's own files are dropped.
    for name in set(sys.modules) - modules:
        if _loaded_from(sys.modules.get(name), tmp_path):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _sigterm_kept() -> Iterator[None]:
    """Restore SIGTERM's handler after each test, whatever the test left installed."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def _write_definition(directory: Path, source: str) -> Path:
    """Write ``source`` to a new file in ``directory`` whose stem no other test uses."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"def_{uuid.uuid4().hex[:12]}.py"
    path.write_text(source)
    return path


def _definition(
    directory: Path,
    *,
    names: tuple[str, str, str] = _NAMES,
    failing: bool = False,
    step_resources: Mapping[str, dict[str, Any]] | None = None,
    pipeline_resources: Mapping[str, Any] | None = None,
) -> Path:
    """Write a definition of :data:`_DEFINITION`'s chain into ``directory``; return its path.

    ``step_resources`` gives a step's own resources by step name, and
    ``pipeline_resources`` the pipeline's.
    """
    settings = (
        f"\n\nNAMES = {names!r}\n"
        f"FAILING = {failing!r}\n"
        f"STEP_RESOURCES = {dict(step_resources or {})!r}\n"
        f"PIPELINE_RESOURCES = {dict(pipeline_resources or {})!r}\n"
    )
    return _write_definition(directory, _DEFINITION + settings)


def _dagger(*args: str) -> Result:
    """Run ``dagger`` with ``args`` in this process."""
    return CliRunner().invoke(dagger, list(args))


def _describe(result: Result) -> str:
    """Return ``result``'s output and any exception it caught, for an assert message."""
    text = result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += "".join(traceback.format_exception(result.exception))
    return text


def _calls(directory: Path) -> list[tuple[str, int]]:
    """Return each logged step body as (position, process id), in call order."""
    log = directory / "calls.log"
    if not log.exists():
        return []
    calls: list[tuple[str, int]] = []
    for line in log.read_text().splitlines():
        label, pid = line.split()
        calls.append((label, int(pid)))
    return calls


def _labels(directory: Path) -> list[str]:
    """Return the position of each logged step body, in call order."""
    return [label for label, _ in _calls(directory)]


def _product(directory: Path, step: str) -> Path:
    """Return the path of ``step``'s product for a definition in ``directory``."""
    return directory / "out" / "run1" / f"{step}.parquet"


def _root(directory: Path) -> Path:
    """Return the pipeline root of a definition in ``directory``."""
    return directory / "state"


def _state_written(root: Path) -> list[str]:
    """Return which of pytask.lock, run.lock and .pytask exist under ``root``."""
    return [name for name in (LOCKFILE, RUN_LOCK_NAME, ".pytask") if (root / name).exists()]


def _run_directories(root: Path) -> list[Path]:
    """Return the bridge's run directories under ``root``."""
    runs = root / RUNS_DIR
    return sorted(runs.iterdir()) if runs.is_dir() else []


def _contents(root: Path) -> list[Path]:
    """Return every file and directory under ``root``, or none if it does not exist."""
    return sorted(root.rglob("*")) if root.exists() else []


def _report_jobmon(monkeypatch: pytest.MonkeyPatch, versions: Mapping[str, str]) -> None:
    """Make the adapter find exactly the Jobmon distributions in ``versions`` installed."""

    def installed_version(distribution: str) -> str | None:
        """Return the version that ``versions`` gives, or None for not installed."""
        return versions.get(distribution)

    monkeypatch.setattr(_jobmon_adapter, "_installed_version", installed_version)


# The default goes through the bridge


def test_dagger_run_without_local_runs_every_step_through_the_bridge(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """End to end: 'dagger run -c def.py -P proj_test' on a three-step chain whose functions are defined in the definition file exits 0, binds one plan with the three steps in order, runs each step body in a process other than the test's, and writes every product."""
    path = _definition(tmp_path)

    result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 0, _describe(result)
    assert bound_names(fake_jobmon) == [list(_NAMES)]
    calls = _calls(tmp_path)
    assert [label for label, _ in calls] == ["a", "b", "c"]
    assert os.getpid() not in {pid for _, pid in calls}
    assert all(_product(tmp_path, step).exists() for step in _NAMES)


def test_dagger_force_without_local_runs_through_the_bridge_and_exits_zero(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """After a first bridged run of a chain of stable steps, 'dagger force -c def.py -P proj_test b' exits 0 and binds a second plan that starts with 'b' and does not hold its upstream step 'a', whose body runs only once."""
    path = _definition(tmp_path)
    first = _dagger("run", "-c", str(path), "-P", "proj_test")
    assert first.exit_code == 0, _describe(first)

    result = _dagger("force", "-c", str(path), "-P", "proj_test", "b")

    assert result.exit_code == 0, _describe(result)
    plans = bound_names(fake_jobmon)
    assert len(plans) == 2, plans
    assert plans[1][0] == "b", plans
    assert "a" not in plans[1], plans
    labels = _labels(tmp_path)
    assert labels.count("a") == 1, labels
    assert labels.count("b") == 2, labels


def test_dagger_run_until_binds_only_the_named_step_and_its_ancestors(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger run -c def.py -P proj_test --until b' on the chain a -> b -> c binds a plan with exactly a and b, and c's product is not written."""
    path = _definition(tmp_path)

    result = _dagger("run", "-c", str(path), "-P", "proj_test", "--until", "b")

    assert result.exit_code == 0, _describe(result)
    assert bound_names(fake_jobmon) == [["a", "b"]]
    assert not _product(tmp_path, "c").exists()


def test_with_local_run_and_force_bind_nothing(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """Under FakeJobmon, 'dagger run -c def.py --local' and 'dagger force -c def.py --local a' both exit 0, run every step in the test's own process, and bind no plan."""
    path = _definition(tmp_path)

    ran = _dagger("run", "-c", str(path), "--local")
    forced = _dagger("force", "-c", str(path), "--local", "a")

    assert ran.exit_code == 0, _describe(ran)
    assert forced.exit_code == 0, _describe(forced)
    calls = _calls(tmp_path)
    assert [label for label, _ in calls[:3]] == ["a", "b", "c"]
    assert ("a", os.getpid()) in calls[3:], calls
    assert {pid for _, pid in calls} == {os.getpid()}
    assert fake_jobmon.plans == []


def test_a_bridged_run_records_its_steps_so_a_local_run_then_skips_them(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """After 'dagger run -c def.py -P proj_test' on stable steps, 'dagger run -c def.py --local' exits 0 and runs no step body."""
    path = _definition(tmp_path)
    bridged = _dagger("run", "-c", str(path), "-P", "proj_test")
    assert bridged.exit_code == 0, _describe(bridged)
    assert _labels(tmp_path) == ["a", "b", "c"]

    result = _dagger("run", "-c", str(path), "--local")

    assert result.exit_code == 0, _describe(result)
    assert _labels(tmp_path) == ["a", "b", "c"]


def test_a_step_from_a_definition_file_runs_on_a_node_without_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_jobmon: FakeJobmon
) -> None:
    """End to end: a definition file in a directory that is not on PYTHONPATH, whose step function is defined in the file and imports a helper module that sits beside it, runs through the real node runner in a child process whose PYTHONPATH does not contain that directory; 'dagger run' exits 0 and the product exists."""
    directory = tmp_path / "definitions"
    helper = f"helper_{uuid.uuid4().hex[:12]}"
    make_dir(directory)
    (directory / f"{helper}.py").write_text(_HELPER)
    path = _write_definition(directory, _WITH_HELPER.replace("HELPER", helper))
    # A node runner started in the definition's directory would find it as its cwd.
    monkeypatch.chdir(make_dir(tmp_path / "elsewhere"))
    pythonpath = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    assert str(directory) not in pythonpath
    assert str(directory.resolve()) not in pythonpath

    result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 0, _describe(result)
    assert bound_names(fake_jobmon) == [["table"]]
    assert _product(directory, "table").exists()
    pid, node_pythonpath = (directory / "node.txt").read_text().split("\n", 1)
    assert int(pid) != os.getpid()
    entries = node_pythonpath.split(os.pathsep)
    assert str(directory) not in entries
    assert str(directory.resolve()) not in entries


# Compute resources from the command line


def test_project_and_queue_options_reach_the_adapter_over_the_defaults(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger run -c def.py -P proj_x -q long.q' binds default_resources equal to DEFAULT_RESOURCES with project 'proj_x' and queue 'long.q'."""
    path = _definition(tmp_path)

    result = _dagger("run", "-c", str(path), "-P", "proj_x", "-q", "long.q")

    assert result.exit_code == 0, _describe(result)
    [options] = fake_jobmon.options
    expected = {**DEFAULT_RESOURCES, "project": "proj_x", "queue": "long.q"}
    assert dict(options["default_resources"]) == expected


def test_options_override_pipeline_resources_and_step_resources_override_both(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """For a definition whose Pipeline sets resources={'project': 'proj_a', 'queue': 'all.q', 'memory': 4} and whose step 'b' sets resources={'queue': 'i.q'}, 'dagger run -P proj_x -q long.q' binds project 'proj_x', queue 'long.q' and memory 4 as defaults, and b's plan entry has queue 'i.q'."""
    path = _definition(
        tmp_path,
        step_resources={"b": {"queue": "i.q"}},
        pipeline_resources={"project": "proj_a", "queue": "all.q", "memory": 4},
    )

    result = _dagger("run", "-c", str(path), "-P", "proj_x", "-q", "long.q")

    assert result.exit_code == 0, _describe(result)
    [options] = fake_jobmon.options
    defaults = dict(options["default_resources"])
    assert defaults["project"] == "proj_x"
    assert defaults["queue"] == "long.q"
    assert defaults["memory"] == 4
    [plan] = fake_jobmon.plans
    [entry] = [entry for entry in plan if entry.name == "b"]
    assert entry.resources["queue"] == "i.q"


def test_a_project_from_pipeline_resources_needs_no_option(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger run -c def.py' with no -P, on a definition whose Pipeline sets resources={'project': 'proj_a'}, exits 0 and binds project 'proj_a'."""
    path = _definition(tmp_path, pipeline_resources={"project": "proj_a"})

    result = _dagger("run", "-c", str(path))

    assert result.exit_code == 0, _describe(result)
    [options] = fake_jobmon.options
    assert options["default_resources"]["project"] == "proj_a"


def test_max_attempts_option_reaches_the_adapter(tmp_path: Path) -> None:
    """'dagger run -c def.py -P proj_x -m 4' binds with max_attempts 4, and without -m with the bridge's DEFAULT_MAX_ATTEMPTS."""
    given = _definition(tmp_path / "given")
    default = _definition(tmp_path / "default")

    with FakeJobmon() as with_option:
        result = _dagger("run", "-c", str(given), "-P", "proj_x", "-m", "4")
    with FakeJobmon() as without_option:
        plain = _dagger("run", "-c", str(default), "-P", "proj_x")

    assert result.exit_code == 0, _describe(result)
    assert plain.exit_code == 0, _describe(plain)
    assert [options["max_attempts"] for options in with_option.options] == [4]
    attempts = [options["max_attempts"] for options in without_option.options]
    assert attempts == [DEFAULT_MAX_ATTEMPTS]


def test_dagger_force_takes_the_same_jobmon_options(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """'dagger force -c def.py -P proj_x -q long.q -m 3 a' exits 0 and binds project 'proj_x', queue 'long.q' and max_attempts 3."""
    path = _definition(tmp_path)

    result = _dagger("force", "-c", str(path), "-P", "proj_x", "-q", "long.q", "-m", "3", "a")

    assert result.exit_code == 0, _describe(result)
    [options] = fake_jobmon.options
    assert options["default_resources"]["project"] == "proj_x"
    assert options["default_resources"]["queue"] == "long.q"
    assert options["max_attempts"] == 3


@pytest.mark.parametrize("verb, steps", [("run", []), ("force", ["first_step"])])
def test_no_project_anywhere_exits_two_naming_the_steps_and_the_three_ways(
    tmp_path: Path, fake_jobmon: FakeJobmon, verb: str, steps: list[str]
) -> None:
    """'dagger run -c def.py' and 'dagger force -c def.py a' with no project in any layer exit 2; stderr names each step and mentions -P, Pipeline(resources=...) and the step's resources=; nothing is bound, no step body runs, and no pytask.lock, run.lock or .pytask directory is written under the root."""
    # Step names that no other text in the message contains.
    names = ("first_step", "second_step", "third_step")
    path = _definition(tmp_path, names=names)

    result = _dagger(verb, "-c", str(path), *steps)

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    for name in names:
        assert name in errors, errors
    assert "-P" in errors, errors
    assert "Pipeline(resources=" in errors, errors
    assert _STEP_RESOURCES.search(errors), errors
    assert fake_jobmon.plans == []
    assert _calls(tmp_path) == []
    assert _state_written(_root(tmp_path)) == []


# Failures and stops


def test_a_step_that_fails_on_its_node_exits_one_with_its_name_type_and_message(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """A step whose function raises ValueError('bad input') on its node makes 'dagger run' exit 1; stderr has "Step '<name>' failed: ValueError: bad input" and lists the steps after it as not run."""
    path = _definition(tmp_path, failing=True)

    result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 1, _describe(result)
    assert "Step 'b' failed: ValueError: bad input" in result.stderr, result.stderr
    assert "Not run because an upstream step failed: 'c'." in result.stderr, result.stderr
    assert bound_names(fake_jobmon) == [list(_NAMES)]


def test_the_node_traceback_of_a_failed_step_is_shown_exactly_once(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """For a step that fails on its node, the command's combined output holds the node's traceback, starting in the step's own function, exactly once, and stderr does not repeat it."""
    path = _definition(tmp_path, failing=True)

    result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 1, _describe(result)
    # Squashed, so that a line that pytask's report wraps still matches.
    output = squashed(result.output)
    raised = squashed(_RAISE_LINE)
    assert output.count(raised) == 1, result.output
    assert raised not in squashed(result.stderr), result.stderr
    before = output[: output.index(raised)]
    frames = before.rpartition(squashed("Traceback (most recent call last):"))[2]
    assert frames.count('File"') == 1, result.output
    assert squashed(path.name) in frames, result.output
    assert frames.endswith("inexplode_on_node"), result.output


def test_a_held_run_lock_exits_one_naming_the_lock_file_and_holder(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """While another process holds <root>/run.lock, 'dagger run -c def.py -P proj_test' exits 1; stderr names the lock file and the holder; nothing is bound, no step body runs, and no run directory is created."""
    path = _definition(tmp_path)
    root = make_dir(_root(tmp_path))
    lock = root / RUN_LOCK_NAME

    with held_lock(lock) as child:
        result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 1, _describe(result)
    errors = squashed(result.stderr)
    assert squashed(str(lock)) in errors, result.stderr
    assert f"pid{child.pid}" in errors, result.stderr
    assert squashed(f"user {account_name()}") in errors, result.stderr
    assert fake_jobmon.plans == []
    assert _calls(tmp_path) == []
    assert _run_directories(root) == []


def test_dagger_force_under_a_held_lock_exits_one_and_keeps_pytask_lock(
    tmp_path: Path, fake_jobmon: FakeJobmon
) -> None:
    """After a recorded run, while another process holds the run lock, 'dagger force -c def.py -P proj_test a' exits 1 naming the lock file and leaves pytask.lock byte-identical."""
    path = _definition(tmp_path)
    first = _dagger("run", "-c", str(path), "-P", "proj_test")
    assert first.exit_code == 0, _describe(first)
    root = _root(tmp_path)
    lockfile = root / LOCKFILE
    before = lockfile.read_bytes()
    lock = root / RUN_LOCK_NAME

    with held_lock(lock):
        result = _dagger("force", "-c", str(path), "-P", "proj_test", "a")

    assert result.exit_code == 1, _describe(result)
    assert squashed(str(lock)) in squashed(result.stderr), result.stderr
    assert lockfile.read_bytes() == before
    assert len(fake_jobmon.plans) == 1


def test_sigterm_during_a_bridged_run_exits_130_naming_the_workflow_id(
    tmp_path: Path,
) -> None:
    """Under FakeJobmon(workflow_id=4242, interrupt_after=1), 'dagger run -c def.py -P proj_test' exits 130; stderr starts with 'Interrupted.', names workflow 4242, says its jobs may still be running, and says to run the same command again to continue."""
    path = _definition(tmp_path)

    with FakeJobmon(workflow_id=4242, interrupt_after=1):
        result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 130, _describe(result)
    errors = result.stderr
    assert errors.startswith("Interrupted."), errors
    assert "4242" in errors, errors
    assert "may still be running" in errors, errors
    assert "run the same command again to continue" in errors.lower(), errors


def test_the_same_command_after_an_interrupt_continues_the_run(tmp_path: Path) -> None:
    """After the interrupted run of a chain of stable steps a -> b -> c stopped after a, running 'dagger run -c def.py -P proj_test' again exits 0, binds only b and c, and runs a's body no more times."""
    path = _definition(tmp_path)
    with FakeJobmon(workflow_id=4242, interrupt_after=1):
        interrupted = _dagger("run", "-c", str(path), "-P", "proj_test")
    assert interrupted.exit_code == 130, _describe(interrupted)
    assert _labels(tmp_path) == ["a"]

    with FakeJobmon() as jobmon:
        result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 0, _describe(result)
    assert bound_names(jobmon) == [["b", "c"]]
    assert _labels(tmp_path).count("a") == 1


# Jobmon not available


@pytest.mark.parametrize("verb, steps", [("run", []), ("force", ["a"])])
def test_without_jobmon_run_and_force_exit_two_saying_to_install_or_pass_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str, steps: list[str]
) -> None:
    """With the Jobmon distributions reported as not installed and no fake adapter, 'dagger run -c def.py -P proj_test' and 'dagger force -c def.py -P proj_test a' exit 2; stderr says to install vivarium-dagger[cluster] or pass --local; no step body runs and nothing is written under the root."""
    _report_jobmon(monkeypatch, {})
    path = _definition(tmp_path)

    result = _dagger(verb, "-c", str(path), "-P", "proj_test", *steps)

    assert result.exit_code == 2, _describe(result)
    assert _CLUSTER_EXTRA in result.stderr, result.stderr
    assert "--local" in result.stderr, result.stderr
    assert _calls(tmp_path) == []
    assert _contents(_root(tmp_path)) == []


def test_without_scontrol_run_exits_two_naming_a_compute_node_and_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With supported Jobmon versions reported but scontrol not on PATH and no fake adapter, 'dagger run -c def.py -P proj_test' exits 2; stderr names /opt/slurm/bin and --local; nothing is written under the root."""
    _report_jobmon(monkeypatch, _SUPPORTED_JOBMON)
    path = _definition(tmp_path)
    monkeypatch.setenv("PATH", str(make_dir(tmp_path / "empty_bin")))

    result = _dagger("run", "-c", str(path), "-P", "proj_test")

    assert result.exit_code == 2, _describe(result)
    assert "/opt/slurm/bin" in result.stderr, result.stderr
    assert "--local" in result.stderr, result.stderr
    assert _calls(tmp_path) == []
    assert _contents(_root(tmp_path)) == []


def test_a_definition_error_is_reported_before_the_jobmon_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Jobmon reported as not installed, 'dagger run -c missing.py' exits 2 with the definition error and does not mention vivarium-dagger[cluster]."""
    _report_jobmon(monkeypatch, {})
    missing = tmp_path / "missing.py"

    result = _dagger("run", "-c", str(missing), "-P", "proj_test")

    assert result.exit_code == 2, _describe(result)
    assert "missing.py" in result.stderr, result.stderr
    assert _CLUSTER_EXTRA not in result.stderr, result.stderr


def test_a_bridged_run_imports_jobmon_only_through_the_adapter(tmp_path: Path) -> None:
    """Under FakeJobmon, a successful 'dagger run -c def.py -P proj_test' in a fresh interpreter leaves jobmon out of sys.modules."""
    path = _definition(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-c", _FRESH_RUN, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "jobmon modules: []" in completed.stdout.splitlines(), completed.stdout
    assert _labels(tmp_path) == ["a", "b", "c"]
