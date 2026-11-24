"""Tests for the ``dagger`` command (``frontend.cli``)."""

import ast
import importlib.metadata
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import traceback
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import click
import pandas as pd
import pytask
import pytest
from click.testing import CliRunner, Result
from pytask import TaskOutcome

from tests.backend.file_snapshot import Snapshot, age_files, changed_files, snapshot_files
from tests.backend.order_names import EIGHT
from tests.frontend.fake_dot import install_fake_dot
from tests.frontend.notebooks import broken_env, recorded_errors, write_source_notebook
from tests.frontend.pipelines import stable_snapshot
from tests.frontend.simulations import (
    copy_simulation_example,
    make_stub_env,
    write_branches,
    write_spec,
)
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry, WiringError
from vivarium.dagger.cli import dagger
from vivarium.dagger.frontend import cli as frontend_cli
from vivarium.dagger.frontend import load_pipeline
from vivarium.dagger.frontend.builder import _PipelineSnapshot
from vivarium.dagger.frontend.execution import _preview, _run
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon, Perturbed

_HEADER = """\
import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry
from vivarium.dagger.frontend import Pipeline, location_inputs
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import with_stable_code_id

HERE = Path(__file__).resolve().parent


def log(name):
    with open(HERE / "calls.log", "a") as file:
        print(name, file=file)


def frame():
    return pd.DataFrame({"n": [1, 2, 3]})


def plus_one(path):
    return pd.read_parquet(path) + 1


def step(target, name, registry, **sources):
    inputs = {param: f"{source}.parquet" for param, source in sources.items()}
    return python_step(
        target, inputs=inputs, outputs=f"{name}.parquet", registry=registry, name=name
    )


def stable(tasks):
    return [with_stable_code_id(t) for t in tasks]
"""
"""The start of every definition. ``log`` records that a step body ran, ``step`` wires step
'<name>' to produce '<name>.parquet' from its sources' outputs, and ``stable`` gives each
step a fixed code identity so that a current step is skipped."""

_CHAIN = (
    _HEADER
    + """

def make_a():
    log("a")
    return frame()


def make_b(source):
    log("b")
    return plus_one(source)


def make_c(source):
    log("c")
    return plus_one(source)


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(make_b, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
    ]
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for task in stable(tasks):
        p.add_task(task)
    return p
"""
)
"""A definition of the chain a -> b -> c with stable code identities."""

_CHAIN_STEPS = ("a", "b", "c")

_KEYED_CHAIN = _CHAIN.replace(
    _CHAIN[_CHAIN.index("def pipeline():") :],
    """def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(make_a, inputs={}, outputs="a.parquet", name="a")
    p.add_python_step(make_b, inputs={"source": "a.parquet"}, outputs="b.parquet", name="b")
    p.add_python_step(make_c, inputs={"source": "b.parquet"}, outputs="c.parquet", name="c")
    return p
""",
)
"""The chain of :data:`_CHAIN` added with the step methods, so the pipeline knows each
step's file key; its steps have no stable code identity."""

_BRANCHES = (
    _HEADER
    + """

def make_p():
    log("p")
    return frame()


def make_x(source):
    log("x")
    return plus_one(source)


def make_w(source):
    log("w")
    return plus_one(source)


def make_q():
    log("q")
    return frame()


def make_y(source):
    log("y")
    return plus_one(source)


def make_z():
    log("z")
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    tasks = [
        step(make_p, "p", registry),
        step(make_x, "x", registry, source="p"),
        step(make_w, "w", registry, source="x"),
        step(make_q, "q", registry),
        step(make_y, "y", registry, source="q"),
        step(make_z, "z", registry),
    ]
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for task in stable(tasks):
        p.add_task(task)
    return p
"""
)
"""A definition of the branches p -> x -> w and q -> y, and the lone step z."""

_FAILING = (
    _HEADER
    + """

def make_a():
    log("a")
    return frame()


def explode_in_user_code(source):
    log("b")
    raise ValueError("the step broke")


def make_c(source):
    log("c")
    return plus_one(source)


def make_d(source):
    log("d")
    return plus_one(source)


def make_side():
    log("side")
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(explode_in_user_code, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
        step(make_d, "d", registry, source="c"),
        step(make_side, "side", registry),
    ]
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for task in stable(tasks):
        p.add_task(task)
    return p
"""
)
"""A definition of a -> b -> c -> d, where b raises, and the independent step side."""

_COMMAND_FAILS = (
    _HEADER
    + """

def pipeline():
    task = Task(
        name="shout",
        run="exit 3",
        inputs={},
        outputs={"product": file_node(HERE / "shout.txt")},
        resources={},
        env=None,
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(task)
    return p
"""
)
"""A definition of one command step that exits with status 3."""

_INTERRUPTED = (
    _HEADER
    + """

def stop():
    log("stop")
    raise KeyboardInterrupt


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(step(stop, "stop", registry))
    return p
"""
)
"""A definition of one step that raises KeyboardInterrupt, as Ctrl-C does."""

_EXITS = (
    _HEADER
    + """

def leave():
    log("leave")
    sys.exit(0)


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(step(leave, "leave", registry))
    return p
"""
)
"""A definition of one step that ends the process with sys.exit(0)."""

_UNREGISTERED = (
    _HEADER
    + """

def pipeline():
    source, target = HERE / "source.txt", HERE / "target.txt"
    task = Task(
        name="copy",
        run=f"cp {source} {target}",
        inputs={"source": file_node(source)},
        outputs={"target": file_node(target)},
        resources={},
        env=None,
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(task)
    return p
"""
)
"""A definition of one hand-built command step and no registry."""

_MISSING_PRODUCER = (
    _HEADER
    + """

def make_a():
    return frame()


def make_b(x):
    return plus_one(x)


def pipeline():
    reg = FileRegistry(HERE / "out", "run1")
    a = python_step(make_a, inputs={}, outputs="population.parquet", registry=reg)
    b = python_step(make_b, inputs={"x": "populaton.parquet"}, outputs="b.csv", registry=reg)
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(a)
    p.add_task(b)
    return p
"""
)
"""A definition whose step 'b.csv' reads a mistyped key, 'populaton.parquet'."""

_DOUBLE_CLAIM = """

def make_shared():
    log("shared")
    return frame()


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(make_shared, inputs={}, outputs="shared.parquet", name=FIRST)
    p.add_python_step(make_shared, inputs={}, outputs="shared.parquet", name=SECOND)
    return p
"""
"""The end of a definition whose steps FIRST and SECOND both produce 'shared.parquet'."""

_CLAIMED = "output 'shared.parquet' is already produced by step"
"""The middle of the Pipeline's message for the second step of _DOUBLE_CLAIM."""

_LOCATION_INPUTS_NOT_READY = (
    _HEADER
    + """

def pipeline():
    inputs = location_inputs(HERE / "inputs", ["ethiopia"], {"coverage.csv": ["coverage"]})
    return Pipeline(HERE / "out", "run1", root_inputs=inputs, state=HERE / "state")
"""
)
"""A definition whose location inputs check fails because ethiopia's coverage.csv is missing."""

_RAISES = {
    "import": 'raise RuntimeError("the definition broke")\n',
    "function": 'def pipeline():\n    raise RuntimeError("the definition broke")\n',
}
"""Definitions that raise at import time or inside pipeline()."""

_FRAMEWORK_FILES = ("/click/", "cli.py", "definition.py")
"""Path fragments of frames that a trimmed definition traceback must not show."""

_RELATIVE_ROOT = (
    _HEADER
    + """

def make_a():
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=Path("state"))
    p.add_task(step(make_a, "a", registry))
    return p
"""
)
"""A definition whose pipeline has the relative root 'state'."""

_UNRESOLVED_ENV = (
    _HEADER
    + """

def pipeline():
    product = HERE / "ran.txt"
    task = Task(
        name="elsewhere",
        run=f"touch {product}",
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=str(HERE / "no_such_env"),
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(task)
    return p
"""
)
"""A definition of one command step whose env is a path that does not exist."""

_BROKEN = 'raise ValueError("not fixed yet")'
_FIXED = "return plus_one(source)"

_FIXABLE = (
    _HEADER
    + """

def make_a():
    log("a")
    return frame()


def make_b(source):
    log("b")
    raise ValueError("not fixed yet")


def make_c(source):
    log("c")
    return plus_one(source)


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(make_b, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
    ]
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for task in stable(tasks):
        p.add_task(task)
    return p
"""
)
"""The chain a -> b -> c where b raises until ``_BROKEN`` is replaced by ``_FIXED``."""

_PASSES = (
    _HEADER
    + """

def make_a():
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(step(make_a, "a", registry))
    return p
"""
)
"""A definition of one step that succeeds."""

_FAILS = (
    _HEADER
    + """

def make_a():
    raise ValueError("the step broke")


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(step(make_a, "a", registry))
    return p
"""
)
"""A definition of one step that raises."""

_THEN_NEXT = '"$1" run -c "$2" --local && touch "$3"'
"""A shell list that runs ``dagger run`` and, only if it succeeds, a next command."""

_RUN_LINE = re.compile(r"run  ([^:\s]+)(: .+)?")
"""A preview line for a step that would run."""

_PLAN_LINE = re.compile(r"run  \S+(: .+)?|skip \S+: .+|\d+ of \d+ steps? would run\.")
"""Any line ``dagger preview`` prints for a successful plan."""

_INTERRUPTED_WHILE_LOADING = "raise KeyboardInterrupt\n"
"""A definition that raises KeyboardInterrupt at import, as Ctrl-C while loading does."""

_CHAINED = """
def load():
    try:
        raise KeyError("missing")
    except KeyError as error:
        raise RuntimeError("could not configure") from error


def pipeline():
    load()
"""
"""A definition whose pipeline() raises a RuntimeError caused by a KeyError."""

_EXITS_WHILE_LOADING = (
    _HEADER
    + """

def pipeline():
    sys.exit(0)
"""
)
"""A definition whose pipeline() calls sys.exit(0)."""

_MISSING_INPUT = (
    _HEADER
    + """
import pytask


def pipeline():
    product = HERE / "read.txt"
    source = pytask.PathNode(name="absent_input.txt", path=HERE / "absent_input.txt")
    task = Task(
        name="reader",
        run=f"touch {product}",
        inputs={"source": source},
        outputs={"product": file_node(product)},
        resources={},
        env=None,
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(task)
    return p
"""
)
"""A definition of one command step whose input file 'absent_input.txt' does not exist. The
node's short name keeps pytask's 80-column report from folding the name across lines, as
it can fold a long absolute path."""

_TOUCHES = """

def touch(name, node):
    return Task(
        name=name,
        run=f"touch {node.path}",
        inputs={},
        outputs={"product": node},
        resources={},
        env=None,
    )


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for name, node in STEPS:
        p.add_task(touch(name, node))
    return p
"""
"""The end of a definition, with no registry, whose command steps each touch the file of the
output node that STEPS pairs with the step's name."""

_CYCLE = (
    _HEADER
    + """

def copy_step(name, source, target):
    return Task(
        name=name,
        run=f"cp {source} {target}",
        inputs={"source": file_node(source)},
        outputs={"target": file_node(target)},
        resources={},
        env=None,
    )


def pipeline():
    x, y = HERE / "x.txt", HERE / "y.txt"
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(copy_step("a", y, x))
    p.add_task(copy_step("b", x, y))
    return p
"""
)
"""A definition, with no registry, of the command steps a, which reads y.txt and writes
x.txt, and b, which reads x.txt and writes y.txt, so that each step depends on the other."""

_ROOT_UNDER_A_FILE = (
    _HEADER
    + """

def make_a():
    log("a")
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    root = HERE / "blocker" / "state"
    p = Pipeline(HERE / "out", "run1", state=root)
    p.add_task(step(make_a, "a", registry))
    return p
"""
)
"""A definition of one step whose pipeline root is 'blocker/state' next to the definition."""

_SUBSCRIPT = 'value = {}["missing"]'
"""A statement that raises KeyError, for which Python prints a caret line under the
subscript."""

_RAISES_IN_A_SUBSCRIPT = f"""
def pipeline():
    {_SUBSCRIPT}
    return value
"""
"""A definition whose pipeline() runs ``_SUBSCRIPT``."""

_ROOTED_STEP = """

def make_a():
    log("a")
    return frame()


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    p = Pipeline(HERE / "out", "run1", state=HERE.joinpath(*ROOT))
    p.add_task(step(make_a, "a", registry))
    return p
"""
"""The end of a definition of one step 'a' whose root is HERE joined with ROOT's parts."""


def _write_definition(directory: Path, source: str) -> Path:
    """Write ``source`` to a new file in ``directory`` whose stem no other test uses."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"def_{uuid.uuid4().hex[:12]}.py"
    path.write_text(source)
    return path


def _double_claim(first: str | None, second: str | None) -> str:
    """Return a definition whose two steps, named ``first`` and ``second``, claim one key."""
    return f"{_HEADER}\nFIRST = {first!r}\nSECOND = {second!r}\n{_DOUBLE_CLAIM}"


def _touches(*steps: tuple[str, str]) -> str:
    """Return a definition of command steps, each a (name, output node source) pair."""
    listed = "".join(f"\n    ({name!r}, {node})," for name, node in steps)
    return f"{_HEADER}\nimport pytask\n\nSTEPS = [{listed}\n]\n{_TOUCHES}"


def _rooted_at(*parts: str) -> str:
    """Return a definition of one step 'a' whose root is its directory joined with ``parts``."""
    return f"{_HEADER}\nROOT = {parts!r}\n{_ROOTED_STEP}"


def _dagger(*args: str) -> Result:
    """Run ``dagger`` with ``args`` in this process."""
    return CliRunner().invoke(dagger, list(args))


def _invoke(verb: str, definition: str, *args: str) -> Result:
    """Run ``dagger <verb> -c <definition>`` with ``args``, adding ``--local`` where needed."""
    local = ["--local"] if verb in ("run", "force") else []
    return _dagger(verb, "-c", definition, *local, *args)


def _describe(result: Result) -> str:
    """Return ``result``'s output and any exception it caught, for an assert message."""
    text = result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += "".join(traceback.format_exception(result.exception))
    return text


def _jobmon_option_help(verb: str) -> dict[str, str]:
    """Return the help of ``dagger <verb>``'s -P, -q and -m options by parameter name."""
    options = {
        param.name: param
        for param in dagger.commands[verb].params
        if isinstance(param, click.Option)
    }
    return {name: options[name].help or "" for name in ("project", "queue", "max_attempts")}


def _calls(directory: Path) -> list[str]:
    """Return the steps whose bodies ran for a definition in ``directory``, in call order."""
    log = directory / "calls.log"
    return log.read_text().split() if log.exists() else []


def _product(directory: Path, step: str) -> Path:
    """Return the path of ``step``'s output for a definition in ``directory``."""
    return directory / "out" / "run1" / f"{step}.parquet"


def _plan(result: Result) -> list[str]:
    """Return the non-blank lines ``dagger preview`` wrote to standard output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def _would_run(result: Result) -> list[str]:
    """Return the steps ``dagger preview`` printed as would-run, in its order."""
    matches = (_RUN_LINE.fullmatch(line) for line in _plan(result))
    return [match.group(1) for match in matches if match]


def _stopped(code: str) -> str:
    """Return dagger's line for a build that pytask stopped with the exit code named ``code``."""
    return f"pytask stopped the build with exit code {code}; its output above shows why."


def _in_order(text: str, *parts: str) -> bool:
    """Return whether each of ``parts`` first occurs in ``text`` after the one before it."""
    positions = [text.find(part) for part in parts]
    return -1 not in positions and positions == sorted(positions)


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
    # Only definition modules are dropped: a compiled extension that a step imported
    # lazily, such as pyarrow's dataset module, cannot be imported a second time. A
    # definition loaded by a relative spec can keep a relative __file__.
    for name in set(sys.modules) - modules:
        if _loaded_from(sys.modules.get(name), tmp_path):
            del sys.modules[name]


# The command and its entry point


def test_dagger_help_lists_run_preview_force_restart_show_and_graph() -> None:
    """'dagger --help' exits 0 and lists the subcommands run, preview, force, restart, show and graph, and no others."""
    result = _dagger("--help")

    assert result.exit_code == 0, _describe(result)
    _, _, commands = result.output.partition("Commands:")
    listed = set(re.findall(r"^  (\S+)", commands, flags=re.MULTILINE))
    assert listed == {"run", "preview", "force", "restart", "show", "graph"}


def test_the_dagger_console_script_resolves_to_the_click_group() -> None:
    """The console_scripts entry point named 'dagger' loads vivarium.dagger.cli.dagger, and no 'dagger' entry point is declared."""
    [entry_point] = importlib.metadata.entry_points(group="console_scripts", name="dagger")

    assert entry_point.value == "vivarium.dagger.cli:dagger"
    assert not importlib.metadata.entry_points(group="console_scripts", name="vwf")
    assert entry_point.load() is dagger


def test_the_installed_dagger_script_exit_status_can_gate_a_shell_chain(
    tmp_path: Path,
) -> None:
    """The installed dagger script, run in a subprocess, exits 0 for a definition that succeeds and 1 for one whose step fails, so 'dagger run -c def.py --local && next' runs next only on success."""
    script = Path(sys.executable).parent / "dagger"
    assert script.is_file(), f"No dagger console script next to {sys.executable}."

    for name, source, status in [("passes", _PASSES, 0), ("fails", _FAILS, 1)]:
        definition = _write_definition(tmp_path / name, source)
        marker = tmp_path / f"{name}.next"
        completed = subprocess.run(
            ["sh", "-c", _THEN_NEXT, "sh", str(script), str(definition), str(marker)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert completed.returncode == status, completed.stdout + completed.stderr
        assert marker.exists() == (status == 0)


# dagger run


def test_run_exits_zero_and_writes_every_output(tmp_path: Path) -> None:
    """'dagger run -c def.py' on a three-step chain exits 0 and every step's output exists."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)


def test_run_until_runs_the_named_step_and_its_ancestors_only(tmp_path: Path) -> None:
    """'dagger run -c def.py --until b' on the chain a -> b -> c exits 0, runs a and b, and leaves c's output unwritten."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", str(path), "--until", "b")

    assert result.exit_code == 0, _describe(result)
    assert _calls(tmp_path) == ["a", "b"]
    assert _product(tmp_path, "b").exists()
    assert not _product(tmp_path, "c").exists()


def test_run_after_run_until_continues_with_the_remaining_steps(tmp_path: Path) -> None:
    """With stable code identities, 'dagger run --until b' then 'dagger run' executes only c the second time; a and b are reported unchanged and their bodies are not called again."""
    path = _write_definition(tmp_path, _CHAIN)
    until = _invoke("run", str(path), "--until", "b")
    assert until.exit_code == 0, _describe(until)
    assert _calls(tmp_path) == ["a", "b"]

    rest = _invoke("run", str(path))

    assert rest.exit_code == 0, _describe(rest)
    assert _calls(tmp_path) == ["a", "b", "c"]
    assert _product(tmp_path, "c").exists()


def test_run_until_given_twice_runs_the_union_of_both_ancestor_sets(tmp_path: Path) -> None:
    """'--until x --until y' on two independent branches runs both branches' steps and nothing else."""
    path = _write_definition(tmp_path, _BRANCHES)

    result = _invoke("run", str(path), "--until", "x", "--until", "y")

    assert result.exit_code == 0, _describe(result)
    assert sorted(_calls(tmp_path)) == ["p", "q", "x", "y"]
    assert not _product(tmp_path, "w").exists()
    assert not _product(tmp_path, "z").exists()


def test_a_failing_python_step_exits_one_and_shows_the_user_traceback_once(
    tmp_path: Path,
) -> None:
    """A python_step whose function raises ValueError makes 'dagger run' exit 1; the output contains the user's frame and 'ValueError: <message>' exactly once, and no build.py or python_step.py frame."""
    path = _write_definition(tmp_path, _FAILING)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "explode_in_user_code" in result.output
    assert "ValueError: the step broke" in result.output
    # dagger's summary repeats the error line by design, so the raise statement, which only a
    # traceback shows, is what proves that the traceback appears once.
    assert result.output.count('raise ValueError("the step broke")') == 1, result.output
    assert "build.py" not in result.output
    assert "python_step.py" not in result.output


def test_the_failure_summary_names_the_failed_step_and_the_steps_not_run(
    tmp_path: Path,
) -> None:
    """After a step fails, dagger's own output names the failed step with its error type and message, and names its downstream steps as not run because an upstream step failed; an independent step still runs."""
    path = _write_definition(tmp_path, _FAILING)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "Step 'b' failed: ValueError: the step broke" in result.stderr
    assert "Not run because an upstream step failed: 'c', 'd'." in result.stderr
    assert "side" in _calls(tmp_path)
    assert _product(tmp_path, "side").exists()


def test_a_failing_command_step_exits_one_and_gives_the_exit_status_not_the_command(
    tmp_path: Path,
) -> None:
    """A command step running 'exit 3' makes 'dagger run' exit 1; stderr has "Step 'shout' failed: the command exited with status 3; its output is in pytask's report for the step." and neither 'exit 3' nor 'CalledProcessError', and no 'Not run because' line; pytask's report on stdout still shows CalledProcessError."""
    path = _write_definition(tmp_path, _COMMAND_FAILS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    errors = result.stderr
    assert (
        "Step 'shout' failed: the command exited with status 3; its output is in pytask's "
        "report for the step."
    ) in errors, errors
    assert "exit 3" not in errors
    assert "CalledProcessError" not in errors
    assert "Not run because" not in errors
    assert "CalledProcessError" in result.stdout, result.stdout


def test_an_interrupted_run_exits_130_and_says_to_run_it_again(
    tmp_path: Path,
) -> None:
    """A step that raises KeyboardInterrupt makes 'dagger run' exit 130 and print that the run was interrupted and to run the same command again to run the pipeline again."""
    path = _write_definition(tmp_path, _INTERRUPTED)

    result = _invoke("run", str(path))

    assert result.exit_code == 130, _describe(result)
    assert "Interrupted" in result.stderr
    assert "run the same command again to run the pipeline again" in result.stderr.lower()


def test_a_step_that_raises_system_exit_makes_dagger_exit_nonzero(tmp_path: Path) -> None:
    """A step that calls sys.exit(0) stops the build, and 'dagger run' exits 1 with a message saying a step exited, not 0."""
    path = _write_definition(tmp_path, _EXITS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert re.search(r"(?i)\bexited\b", result.stderr), result.stderr
    assert re.search(r"\b0\b", result.stderr), result.stderr


def test_pytask_stopping_without_a_recorded_error_exits_one_with_a_readable_message(
    tmp_path: Path,
) -> None:
    """When pytask stops before any step runs and records no error (a malformed pytask.toml under the root), 'dagger run' exits 1 with a message that names pytask's exit code and is more than the bare code name."""
    path = _write_definition(tmp_path, _CHAIN)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "pytask.toml").write_text("[[[ not toml\n")

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert _stopped("CONFIGURATION_FAILED") in result.stderr, result.stderr
    assert _calls(tmp_path) == []


# dagger preview


def test_preview_prints_one_line_per_step_in_file_key_terms(tmp_path: Path) -> None:
    """'dagger preview -c def.py' of a chain added with the step methods exits 0 and prints one 'run <step>: <reasons>' line per stale step, naming files as "file '<key>'" and code as "code of '<step>'", with no absolute path, then an '<n> of <m> steps would run.' line."""
    path = _write_definition(tmp_path, _KEYED_CHAIN)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    lines = _plan(result)
    assert len(lines) == 4, result.output
    assert _would_run(result) == ["a", "b", "c"]
    assert lines[0].startswith("run  a: ")
    assert "code of 'a'" in lines[0]
    assert "file 'a.parquet'" in lines[0]
    assert lines[3] == "3 of 3 steps would run."
    assert str(tmp_path) not in result.output
    assert str(tmp_path.resolve()) not in result.output


def test_preview_writes_no_output_and_prints_no_pytask_output(tmp_path: Path) -> None:
    """After 'dagger preview', no output exists and the output contains none of pytask's own output, such as its 'Start pytask session' banner or its table."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    assert not any(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)
    assert _calls(tmp_path) == []
    assert "Start pytask session" not in result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert all(_PLAN_LINE.fullmatch(line) for line in lines), result.output


def test_preview_until_reports_the_other_steps_as_not_selected(tmp_path: Path) -> None:
    """'dagger preview -c def.py --until a' prints 'skip <step>: not selected' for each step outside a's ancestors."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("preview", str(path), "--until", "a")

    assert result.exit_code == 0, _describe(result)
    assert _would_run(result) == ["a"]
    assert _plan(result)[1:] == [
        "skip b: not selected",
        "skip c: not selected",
        "1 of 3 steps would run.",
    ]


def test_preview_reports_a_current_step_as_unchanged(tmp_path: Path) -> None:
    """With stable code identities and after a full run, 'dagger preview' prints 'skip <step>: unchanged' for every step and '0 of <m> steps would run.'."""
    path = _write_definition(tmp_path, _CHAIN)
    ran = _invoke("run", str(path))
    assert ran.exit_code == 0, _describe(ran)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    assert _plan(result) == [
        "skip a: unchanged",
        "skip b: unchanged",
        "skip c: unchanged",
        "0 of 3 steps would run.",
    ]


def test_preview_of_a_step_without_registry_keys_prints_the_raw_node_name(
    tmp_path: Path,
) -> None:
    """A pipeline of hand-built command tasks with no registry previews without error, and its reasons show the file's path as pytask names it."""
    (tmp_path / "source.txt").write_text("x")
    path = _write_definition(tmp_path, _UNREGISTERED)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    lines = _plan(result)
    assert len(lines) == 2, result.output
    assert lines[0].startswith("run  copy: ")
    assert file_node(tmp_path / "source.txt").name in lines[0]
    assert file_node(tmp_path / "target.txt").name in lines[0]
    assert "artifact " not in lines[0]
    assert lines[1] == "1 of 1 step would run."


# dagger force


def test_force_reruns_a_current_step(tmp_path: Path) -> None:
    """With stable code identities, 'dagger force -c def.py b' after a full run executes b again and exits 0."""
    path = _write_definition(tmp_path, _CHAIN)
    ran = _invoke("run", str(path))
    assert ran.exit_code == 0, _describe(ran)
    assert _calls(tmp_path) == ["a", "b", "c"]

    result = _invoke("force", str(path), "b")

    assert result.exit_code == 0, _describe(result)
    again = _calls(tmp_path)[3:]
    assert again[:1] == ["b"]
    assert "a" not in again


def test_force_on_a_step_that_never_ran_runs_it(tmp_path: Path) -> None:
    """'dagger force -c def.py a' on a fresh root runs a (and every other stale step) and exits 0."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("force", str(path), "a")

    assert result.exit_code == 0, _describe(result)
    assert _calls(tmp_path) == ["a", "b", "c"]
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)


def test_force_without_a_step_is_a_usage_error(tmp_path: Path) -> None:
    """'dagger force -c def.py' with no STEP exits 2 with click's usage message."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("force", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "Usage:" in result.output
    assert "Missing argument" in result.output
    assert _calls(tmp_path) == []


# Errors in the definition or the command


@pytest.mark.parametrize(
    "command, hint",
    [
        ("run --until", "'--until'"),
        ("preview --until", "'--until'"),
        ("force", "'STEP...'"),
    ],
    ids=["run", "preview", "force"],
)
def test_an_unknown_step_exits_two_and_lists_the_valid_step_names(
    tmp_path: Path, command: str, hint: str
) -> None:
    """An unknown step name given to run --until, preview --until or force exits 2 with click's usage error for '--until' or 'STEP...' carrying the verb's message, which lists every valid step name, and no pytask state or output is written."""
    path = _write_definition(tmp_path, _CHAIN)
    verb, *option = command.split()

    result = _invoke(verb, str(path), *option, "bb")

    assert result.exit_code == 2, _describe(result)
    assert result.stderr.startswith(f"Usage: dagger {verb} "), result.stderr
    assert f"Error: Invalid value for {hint}: No step named 'bb'." in result.stderr
    assert "'bb'" in result.output
    assert "Valid steps: 'a', 'b', 'c'." in result.output
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "out").exists()
    assert _calls(tmp_path) == []


def test_a_definition_error_exits_two_with_a_one_line_message(tmp_path: Path) -> None:
    """A spec naming a missing function exits 2 and prints the DefinitionError message without a traceback."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", f"{path}:no_such_function")

    assert result.exit_code == 2, _describe(result)
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("Error: ")
    assert "no_such_function" in lines[0]
    assert "Traceback" not in result.output


def test_a_missing_producer_exits_two_naming_the_step_and_the_key(tmp_path: Path) -> None:
    """A step reading a mistyped key exits 2; the output names the step, the mistyped key and the did-you-mean key, shows the definition's python_step(...) line and an '[N framework frames hidden]' line, and has no wiring.py or python_step.py frame."""
    path = _write_definition(tmp_path, _MISSING_PRODUCER)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert "Step 'b.csv'" in errors
    assert "'populaton.parquet'" in errors
    assert "Did you mean 'population.parquet'" in errors
    assert "b = python_step(make_b" in errors
    assert re.search(r"\[\d+ framework frames? hidden\]", errors), errors
    assert "wiring.py" not in errors
    assert "python_step.py" not in errors
    assert "During handling of the above exception" not in errors


def test_a_double_claimed_file_exits_two_naming_the_key_and_both_steps(
    tmp_path: Path,
) -> None:
    """Two python steps with different names and the same outputs key exit 2 when the Pipeline refuses the second, with a message naming the second step, the file key and the first step, before any build."""
    path = _write_definition(tmp_path, _double_claim("first", "second"))

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"Step 'second': {_CLAIMED} 'first'" in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()
    assert _calls(tmp_path) == []


def test_the_same_outputs_key_with_default_names_is_reported_as_a_double_claim(
    tmp_path: Path,
) -> None:
    """Two python steps with the same outputs key and default names exit 2 with the Pipeline's message naming the key as already produced, not the message for a name already added or the backend's duplicate-task-name message."""
    path = _write_definition(tmp_path, _double_claim(None, None))

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert _CLAIMED in result.stderr, result.stderr
    assert "already added" not in result.stderr, result.stderr
    assert "Task names must be unique" not in result.output


def test_a_location_input_error_exits_two_and_prints_only_its_message(tmp_path: Path) -> None:
    """A definition whose location_inputs check fails exits 2 and prints the LocationInputError message with no traceback."""
    path = _write_definition(tmp_path, _LOCATION_INPUTS_NOT_READY)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "Location 'ethiopia': coverage.csv is missing" in result.stderr
    assert "Traceback" not in result.output
    assert not re.search(r"frames? hidden", result.output)


@pytest.mark.parametrize("where", ["import", "function"])
def test_definition_code_that_raises_exits_two_with_a_traceback_starting_in_the_definition(
    tmp_path: Path, where: str
) -> None:
    """A definition raising at import time or inside pipeline() exits 2; the traceback's first frame is the definition file, it has no click, cli.py or definition.py frame, and it ends with the exception's type and message."""
    path = _write_definition(tmp_path, _RAISES[where])

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    lines = result.stderr.splitlines()
    assert "Traceback (most recent call last):" in lines, result.stderr
    frames = [index for index, line in enumerate(lines) if line.startswith('  File "')]
    assert frames, result.stderr
    assert path.name in lines[frames[0]]
    for index in frames:
        assert not any(part in lines[index] for part in _FRAMEWORK_FILES), lines[index]
    assert "RuntimeError: the definition broke" in lines, result.stderr
    assert lines.index("RuntimeError: the definition broke") > frames[-1]


def test_an_error_with_no_user_frame_prints_the_full_traceback(tmp_path: Path) -> None:
    """A spec naming a library function that needs an argument (vivarium.dagger.harness:build_fixture) fails with a TypeError raised only in framework frames; dagger exits 2 and prints the traceback with those frames instead of an empty one."""
    result = _invoke("run", "vivarium.dagger.harness:build_fixture")

    assert result.exit_code == 2, _describe(result)
    assert "Traceback (most recent call last):" in result.stderr
    frames = [line for line in result.stderr.splitlines() if line.startswith('  File "')]
    assert frames, result.stderr
    assert any("vivarium/dagger/" in line for line in frames), result.stderr
    assert not re.search(r"frames? hidden", result.stderr)
    assert "TypeError" in result.stderr


def test_a_relative_pipeline_root_exits_two_naming_the_root(tmp_path: Path) -> None:
    """A definition that makes Pipeline(..., state=Path('state')) exits 2 with a message saying the state must be absolute."""
    path = _write_definition(tmp_path, _RELATIVE_ROOT)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "ValueError" in result.stderr
    assert "absolute" in result.stderr.lower()


def test_a_pipeline_the_backend_rejects_exits_two_with_its_reason(tmp_path: Path) -> None:
    """A pipeline whose command step declares an env that does not resolve exits 2 from run and from preview, prints the backend's reason naming the env, and runs nothing."""
    path = _write_definition(tmp_path, _UNRESOLVED_ENV)

    for verb in ("run", "preview"):
        result = _invoke(verb, str(path))

        assert result.exit_code == 2, _describe(result)
        assert "no_such_env" in result.stderr
    assert not (tmp_path / "ran.txt").exists()


# Use cases and the documented examples


def test_an_operator_previews_runs_until_a_step_then_completes_and_forces(
    tmp_path: Path,
) -> None:
    """With stable code identities: preview lists every step as run; run --until b runs a and b; preview then lists only c as run; run executes only c; force b runs b again; every command exits 0."""
    spec = str(_write_definition(tmp_path, _CHAIN))

    planned = _invoke("preview", spec)
    assert planned.exit_code == 0, _describe(planned)
    assert _would_run(planned) == ["a", "b", "c"]

    until = _invoke("run", spec, "--until", "b")
    assert until.exit_code == 0, _describe(until)
    assert _calls(tmp_path) == ["a", "b"]

    replanned = _invoke("preview", spec)
    assert replanned.exit_code == 0, _describe(replanned)
    assert _plan(replanned)[:2] == ["skip a: unchanged", "skip b: unchanged"]
    assert _would_run(replanned) == ["c"]

    rest = _invoke("run", spec)
    assert rest.exit_code == 0, _describe(rest)
    assert _calls(tmp_path) == ["a", "b", "c"]

    forced = _invoke("force", spec, "b")
    assert forced.exit_code == 0, _describe(forced)
    again = _calls(tmp_path)[3:]
    assert again[:1] == ["b"]
    assert "a" not in again


def test_an_operator_fixes_a_failed_step_and_reruns(tmp_path: Path) -> None:
    """'dagger run' exits 1 for a raising step; after the definition file is edited to fix it, 'dagger run' in the same process exits 0 and the failed step and its descendants run."""
    path = _write_definition(tmp_path, _FIXABLE)
    failed = _invoke("run", str(path))
    assert failed.exit_code == 1, _describe(failed)
    assert "not fixed yet" in failed.output
    assert _calls(tmp_path) == ["a", "b"]

    # The fix changes the file's length, so the bytecode cache cannot serve the old code
    # even when the edit lands within the same second.
    path.write_text(_FIXABLE.replace(_BROKEN, _FIXED))
    fixed = _invoke("run", str(path))

    assert fixed.exit_code == 0, _describe(fixed)
    assert _calls(tmp_path)[2:] == ["b", "c"]
    assert _product(tmp_path, "c").exists()


def test_every_command_in_the_docs_page_exits_zero(tmp_path: Path) -> None:
    """Each '$ dagger ...' line in docs/source/python_pipelines.rst, run in order under FakeJobmon with SLURM_JOB_ID unset on a copy of tests/frontend/pipeline_example/pipeline.py in tmp_path, exits 0; the page shows at least one example of run, preview and force, and at least one run without --local, which binds a plan."""
    page = Path(__file__).parents[2] / "docs" / "source" / "python_pipelines.rst"
    prompt = "$ dagger "
    commands = [
        line.strip().removeprefix(prompt)
        for line in page.read_text().splitlines()
        if line.strip().startswith(prompt)
    ]
    shutil.copy(Path(__file__).parent / "pipeline_example" / "pipeline.py", tmp_path)

    with pytest.MonkeyPatch.context() as patch, FakeJobmon() as jobmon:
        patch.chdir(tmp_path)
        # Outside an allocation, so that no bridged run asks Slurm for its remaining time.
        patch.delenv("SLURM_JOB_ID", raising=False)
        for command in commands:
            result = _dagger(*shlex.split(command))
            assert result.exit_code == 0, f"$ dagger {command}\n{_describe(result)}"

    words = [shlex.split(command) for command in commands]
    assert {"run", "preview", "force"} <= {command[0] for command in words}
    assert any(command[0] == "run" and "--local" not in command for command in words)
    assert jobmon.plans, "no command on the page bound a plan"


# Review fixes (round 2)


@pytest.mark.parametrize("command", ["run", "preview", "force"])
def test_an_interrupt_while_loading_exits_130_with_the_verbs_message(
    tmp_path: Path, command: str
) -> None:
    """A definition that raises KeyboardInterrupt at import exits 130 under run, preview and force (with a STEP), with no traceback; only run and force add to run the same command again to run the pipeline again."""
    path = _write_definition(tmp_path, _INTERRUPTED_WHILE_LOADING)
    steps: list[str] = ["a"] if command == "force" else []

    result = _invoke(command, str(path), *steps)

    assert result.exit_code == 130, _describe(result)
    assert result.stderr.startswith("Interrupted."), result.stderr
    assert "Traceback" not in result.stderr
    reruns = "run the same command again to run the pipeline again" in result.stderr.lower()
    assert reruns == (command != "preview"), result.stderr


def test_a_chained_definition_error_prints_each_exception_trimmed(tmp_path: Path) -> None:
    """A pipeline() that raises RuntimeError from a KeyError exits 2; stderr shows the KeyError block with its user frame, then Python's direct-cause line, then the RuntimeError, and no framework frame in either block."""
    path = _write_definition(tmp_path, _CHAINED)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert _in_order(
        errors,
        'raise KeyError("missing")',
        "KeyError: 'missing'",
        "The above exception was the direct cause of the following exception:",
        "RuntimeError: could not configure",
    ), errors
    frames = [line for line in errors.splitlines() if line.startswith('  File "')]
    assert any(path.name in line for line in frames), errors
    for line in frames:
        assert not any(part in line for part in _FRAMEWORK_FILES), line


def test_a_definition_that_calls_sys_exit_while_loading_exits_two(tmp_path: Path) -> None:
    """A pipeline() that calls sys.exit(0) makes 'dagger run' exit 2, not 0, with a trimmed traceback ending in 'SystemExit: 0' and nothing run."""
    path = _write_definition(tmp_path, _EXITS_WHILE_LOADING)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "Traceback (most recent call last):" in result.stderr
    assert "SystemExit: 0" in result.stderr
    assert not (tmp_path / "state").exists()
    assert _calls(tmp_path) == []


def test_a_root_that_cannot_be_created_exits_two_naming_the_root(tmp_path: Path) -> None:
    """A pipeline whose root is an existing file makes 'dagger run' exit 2 with 'Error: Cannot use the pipeline root <root>: ...' on stderr and no traceback."""
    path = _write_definition(tmp_path, _CHAIN)
    root = tmp_path.resolve() / "state"
    root.write_text("a file where the root directory should be\n")

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"Error: Cannot use the pipeline root {root}: " in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert _calls(tmp_path) == []


def test_preview_shows_pytasks_output_when_pytask_stops_the_plan(tmp_path: Path) -> None:
    """With a malformed pytask.toml under the root, 'dagger preview' exits 1, prints no plan on stdout, and writes pytask's TOMLDecodeError output to stderr before dagger's line naming CONFIGURATION_FAILED."""
    path = _write_definition(tmp_path, _CHAIN)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "pytask.toml").write_text("[[[ not toml\n")

    result = _invoke("preview", str(path))

    assert result.exit_code == 1, _describe(result)
    assert not any(_PLAN_LINE.fullmatch(line) for line in _plan(result)), result.stdout
    assert _in_order(
        result.stderr, "TOMLDecodeError", _stopped("CONFIGURATION_FAILED")
    ), result.stderr


def test_preview_of_a_step_whose_input_file_is_missing_exits_one_with_pytasks_report(
    tmp_path: Path,
) -> None:
    """A hand-built command step whose input file does not exist makes 'dagger preview' exit 1, with pytask's report naming the missing file on stderr before dagger's line naming FAILED."""
    path = _write_definition(tmp_path, _MISSING_INPUT)

    result = _invoke("preview", str(path))

    assert result.exit_code == 1, _describe(result)
    assert not any(_PLAN_LINE.fullmatch(line) for line in _plan(result)), result.stdout
    assert _in_order(result.stderr, "absent_input.txt", _stopped("FAILED")), result.stderr
    assert not (tmp_path / "read.txt").exists()


def test_unnamed_path_nodes_on_different_files_are_not_a_double_claim(tmp_path: Path) -> None:
    """Two hand-built command steps whose outputs are unnamed pytask.PathNode(path=...) nodes on different files run with exit 0 and write both files."""
    definition = _touches(
        ("one", 'pytask.PathNode(path=HERE / "a.txt")'),
        ("two", 'pytask.PathNode(path=HERE / "b.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()


def test_two_nodes_on_one_file_with_different_names_are_a_double_claim(
    tmp_path: Path,
) -> None:
    """Two hand-built command steps whose outputs are a file_node and a PathNode named 'custom' on the same file exit 2 when add_task refuses the second, with a message naming 'two', the file's path and 'one'."""
    definition = _touches(
        ("one", 'file_node(HERE / "same.txt")'),
        ("two", 'pytask.PathNode(name="custom", path=HERE / "same.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    same = repr(str((tmp_path / "same.txt").resolve()))
    claimed = f"Step 'two': output {same} is already produced by step 'one'"
    assert claimed in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "same.txt").exists()


def test_a_double_claim_of_added_tasks_names_the_path(tmp_path: Path) -> None:
    """Two hand-built command steps 'one' and 'two' that both output file_node(<tmp>/same.txt), added with add_task, exit 2 with "Step 'two': output '<that path>' is already produced by step 'one'"."""
    definition = _touches(
        ("one", 'file_node(HERE / "same.txt")'),
        ("two", 'file_node(HERE / "same.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    same = repr(str((tmp_path / "same.txt").resolve()))
    claimed = f"Step 'two': output {same} is already produced by step 'one'"
    assert claimed in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()


# Review fixes (round 3)


@pytest.mark.parametrize("command", ["run", "preview"])
def test_a_dependency_cycle_exits_two_with_pytasks_message(
    tmp_path: Path, command: str
) -> None:
    """Two hand-built command steps that each read the other's output exit 2 under run and preview, with 'DAG_FAILED' and pytask's 'contains cycles' text on stderr, no "pytask stopped the build" line, and (for preview) no captured pytask session output such as 'Start pytask session'."""
    path = _write_definition(tmp_path, _CYCLE)

    result = _invoke(command, str(path))

    assert result.exit_code == 2, _describe(result)
    assert "DAG_FAILED" in result.stderr, result.stderr
    assert "contains cycles" in result.stderr, result.stderr
    assert "pytask stopped the build" not in result.stderr, result.stderr
    assert not (tmp_path / "x.txt").exists()
    assert not (tmp_path / "y.txt").exists()
    if command == "preview":
        assert "Start pytask session" not in result.output, result.output
        assert not any(_PLAN_LINE.fullmatch(line) for line in _plan(result)), result.stdout


def test_a_root_under_a_file_exits_two_naming_the_root(tmp_path: Path) -> None:
    """A pipeline whose root is <tmp>/blocker/state, where blocker is an existing file, makes 'dagger run' exit 2 with 'Error: Cannot use the pipeline root <root>: ' on stderr, no traceback, and nothing created."""
    path = _write_definition(tmp_path, _ROOT_UNDER_A_FILE)
    blocker = tmp_path / "blocker"
    content = "a file where a directory above the root should be\n"
    blocker.write_text(content)
    root = tmp_path.resolve() / "blocker" / "state"

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    reason = f"{blocker.resolve()} is not a directory"
    assert f"Error: Cannot use the pipeline root {root}: {reason}" in result.stderr
    assert "Traceback" not in result.stderr
    assert blocker.is_file() and blocker.read_text() == content
    assert not (tmp_path / "out").exists()
    assert _calls(tmp_path) == []


def test_an_error_writing_pytasks_state_after_the_build_exits_one_naming_the_root(
    tmp_path: Path,
) -> None:
    """When pytask cannot write its lockfile at the end of a build (a directory already sits at <root>/pytask.lock.tmp), 'dagger run' exits 1, not 2, the step's output exists, and stderr has 'Could not read or write the pipeline's state under <root>: ' with no traceback."""
    path = _write_definition(tmp_path, _CHAIN)
    root = tmp_path.resolve() / "state"
    # pytask writes the lockfile to pytask.lock.tmp and then renames it, so a directory at
    # that path fails the write that ends a build in which every step ran.
    (root / "pytask.lock.tmp").mkdir(parents=True)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    # CliRunner also reports an exception that dagger did not handle as exit code 1.
    assert isinstance(result.exception, SystemExit), _describe(result)
    assert _calls(tmp_path) == list(_CHAIN_STEPS)
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)
    error = f"Error: Could not read or write the pipeline's state under {root}: "
    assert error in result.stderr, result.stderr
    assert "Traceback" not in result.stderr


def test_a_kept_frame_prints_as_python_prints_it(tmp_path: Path) -> None:
    """A definition whose pipeline() runs `value = {}["missing"]` exits 2, and the trimmed traceback shows the definition's source line followed by Python's caret line (a line of '~' and '^' marks)."""
    path = _write_definition(tmp_path, _RAISES_IN_A_SUBSCRIPT)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    lines = result.stderr.splitlines()
    shown = [index for index, line in enumerate(lines) if _SUBSCRIPT in line]
    assert shown, result.stderr
    for index in shown:
        frame = lines[index - 1]
        assert frame.startswith('  File "') and path.name in frame, result.stderr
        carets = lines[index + 1] if index + 1 < len(lines) else ""
        assert "^" in carets and set(carets) <= {" ", "~", "^"}, result.stderr


def test_a_double_claim_through_two_spellings_of_one_path_is_caught(tmp_path: Path) -> None:
    """Two hand-built command steps whose outputs are unnamed PathNodes on HERE / 'sub' / '..' / 'x.txt' and HERE / 'x.txt' (with HERE / 'sub' existing) exit 2 when add_task refuses the second, with a message naming 'two', the resolved path and 'one'."""
    (tmp_path / "sub").mkdir()
    definition = _touches(
        ("one", 'pytask.PathNode(path=HERE / "sub" / ".." / "x.txt")'),
        ("two", 'pytask.PathNode(path=HERE / "x.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    x = repr(str((tmp_path / "x.txt").resolve()))
    claimed = f"Step 'two': output {x} is already produced by step 'one'"
    assert claimed in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
def test_a_root_whose_parent_is_not_writable_exits_two_naming_the_parent(
    tmp_path: Path,
) -> None:
    """A root under a read-only directory makes 'dagger run' exit 2 with a message naming that directory as not writable, and nothing is created."""
    path = _write_definition(tmp_path, _rooted_at("locked", "state"))
    locked = tmp_path.resolve() / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        result = _invoke("run", str(path))
    finally:
        locked.chmod(0o755)

    assert result.exit_code == 2, _describe(result)
    error = (
        f"Error: Cannot use the pipeline root {locked / 'state'}: {locked} is not writable"
    )
    assert error in result.stderr, result.stderr
    assert not (locked / "state").exists()
    assert _calls(tmp_path) == []


def test_a_fresh_root_under_missing_directories_is_accepted(tmp_path: Path) -> None:
    """A root whose parent directories do not exist yet passes the root check, and 'dagger run' creates it and exits 0."""
    path = _write_definition(tmp_path, _rooted_at("a", "b", "state"))

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert (tmp_path / "a" / "b" / "state").is_dir()
    assert _calls(tmp_path) == ["a"]


def test_a_root_through_a_symlink_to_a_directory_is_accepted(tmp_path: Path) -> None:
    """A root under a symlink to a writable directory passes the root check, and the state is written in the link's target."""
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    path = _write_definition(tmp_path, _rooted_at("link", "state"))

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert (tmp_path / "real" / "state").is_dir()
    assert _calls(tmp_path) == ["a"]


# One dagger command for YAML workflows and pipeline definitions

_WORKFLOW_YAML = "workflow:\n  name: w\n  steps: []\n"
"""A workflow YAML file's text; the tests that use it stop before it is read."""

_HELP_AND_PREVIEW = """
import sys

from click.testing import CliRunner

from vivarium.dagger.cli import dagger

for args in (["--help"], ["run", "--help"], ["preview", "-c", sys.argv[1]]):
    result = CliRunner().invoke(dagger, args)
    assert result.exit_code == 0, (args, result.output)
loaded = sorted(name for name in sys.modules if name.partition(".")[0] == "jobmon")
print("jobmon modules:", loaded)
"""
"""A script that runs 'dagger --help', 'dagger run --help' and 'dagger preview' on the
definition its first argument names, then prints the jobmon modules it imported."""


@pytest.mark.parametrize("verb, steps", [("run", []), ("force", ["a"])])
@pytest.mark.parametrize(
    "option", [["-P", "proj_x"], ["--queue", "all.q"], ["-m", "3"], ["--max-attempts", "3"]]
)
def test_jobmon_options_with_local_exit_two_saying_they_need_a_jobmon_run(
    tmp_path: Path, verb: str, steps: list[str], option: list[str]
) -> None:
    """'dagger run' and 'dagger force' on a pipeline definition with --local and -P, -q or -m exit 2 saying the option applies only to a run through Jobmon, and run no step."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _dagger(verb, "-c", str(path), "--local", *option, *steps)

    assert result.exit_code == 2, _describe(result)
    assert "applies only to a run through Jobmon" in result.stderr, result.stderr
    assert _calls(tmp_path) == []
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
@pytest.mark.parametrize("verb, args", [("preview", []), ("force", ["a", "--local"])])
def test_preview_and_force_refuse_a_workflow_yaml_file(
    tmp_path: Path, suffix: str, verb: str, args: list[str]
) -> None:
    """'dagger preview' and 'dagger force' with a .yaml or .yml file exit 2 saying they take a Python pipeline definition."""
    path = tmp_path / f"workflow{suffix}"
    path.write_text(_WORKFLOW_YAML)

    result = _dagger(verb, "-c", str(path), *args)

    assert result.exit_code == 2, _describe(result)
    assert "takes a Python pipeline definition" in result.stderr


@pytest.mark.parametrize("option", [["-n", "name"], ["-o", "out"], ["-v"], ["--pdb"]])
def test_run_refuses_yaml_only_options_for_a_definition(
    tmp_path: Path, option: list[str]
) -> None:
    """'dagger run -c def.py --local' with an option that overrides a YAML file's settings exits 2 naming the option, and runs no step."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _dagger("run", "-c", str(path), "--local", *option)

    assert result.exit_code == 2, _describe(result)
    assert "applies only to a workflow YAML file" in result.stderr
    assert _calls(tmp_path) == []


@pytest.mark.parametrize("option", [["--until", "a"], ["--local"]])
def test_run_refuses_definition_options_for_a_yaml_file(
    tmp_path: Path, option: list[str]
) -> None:
    """'dagger run -c workflow.yaml' with --until or --local exits 2 saying the option applies only to a Python pipeline definition."""
    path = tmp_path / "workflow.yaml"
    path.write_text(_WORKFLOW_YAML)

    result = _dagger("run", "-c", str(path), *option)

    assert result.exit_code == 2, _describe(result)
    assert "applies only to a Python pipeline definition" in result.stderr


def test_a_missing_yaml_file_is_a_usage_error(tmp_path: Path) -> None:
    """'dagger run -c missing.yaml' exits 2 with click's message that the file does not exist."""
    result = _dagger("run", "-c", str(tmp_path / "missing.yaml"))

    assert result.exit_code == 2, _describe(result)
    assert "does not exist" in result.stderr


def test_dagger_help_and_a_preview_do_not_import_jobmon(tmp_path: Path) -> None:
    """In a fresh interpreter, 'dagger --help', 'dagger run --help' and 'dagger preview -c def.py' on a definition exit 0 and leave jobmon out of sys.modules."""
    path = _write_definition(tmp_path, _CHAIN)

    completed = subprocess.run(
        [sys.executable, "-c", _HELP_AND_PREVIEW, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "jobmon modules: []" in completed.stdout.splitlines(), completed.stdout
    assert _calls(tmp_path) == []


def test_dagger_run_help_describes_project_queue_and_max_attempts_for_a_definition() -> None:
    """'dagger run --help' and 'dagger force --help' list -P/--project, -q/--queue and -m/--max-attempts, and say that a definition runs through Jobmon unless given --local."""
    for verb in ("run", "force"):
        result = _dagger(verb, "--help")

        assert result.exit_code == 0, _describe(result)
        text = " ".join(result.output.split())
        for option in ("-P, --project", "-q, --queue", "-m, --max-attempts", "--local"):
            assert option in text, (verb, text)
        assert re.search(r"(?i)through jobmon", text), (verb, text)


def test_dagger_force_help_describes_jobmon_options_without_workflow_yaml() -> None:
    """'dagger force --help' describes -P/--project, -q/--queue and -m/--max-attempts for a run through Jobmon and does not mention a workflow YAML file, while 'dagger run --help' still does."""
    subjects = {"project": "project", "queue": "queue", "max_attempts": "each step"}
    for verb, for_yaml in (("force", False), ("run", True)):
        result = _dagger(verb, "--help")

        assert result.exit_code == 0, _describe(result)
        # Squashed, so that a help text that click wraps still matches.
        shown = "".join(result.output.split())
        for name, text in _jobmon_option_help(verb).items():
            assert "".join(text.split()) in shown, (verb, name, result.output)
            assert subjects[name] in text, (verb, name, text)
            assert re.search(r"(?i)through jobmon", text), (verb, name, text)
            assert ("workflow YAML file" in text) is for_yaml, (verb, name, text)
            assert ("config file" in text) is for_yaml, (verb, name, text)
    force_help = dagger.commands["force"].help or ""
    assert "yaml" not in force_help.lower(), force_help


def test_importing_the_cli_does_not_import_jobmon() -> None:
    """Importing vivarium.dagger.cli in a fresh interpreter leaves jobmon out of sys.modules, so a pipeline definition runs with --local without the cluster extra."""
    code = "import sys, vivarium.dagger.cli; print('jobmon' in sys.modules)"

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"


# A failing notebook step (MIC-7785)

_NOTEBOOK_PIPELINE = """\
import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline
from vivarium.dagger.backend.wiring import FileRegistry
from vivarium.dagger.frontend.notebook_step import notebook_step
from vivarium.dagger.frontend.python_step import python_step

HERE = Path(__file__).resolve().parent
ENV = __ENV__


def read_summary(summary):
    return pd.read_csv(summary)


def pipeline():
    registry = FileRegistry(HERE / "out", "run1")
    summarize = notebook_step(
        HERE / "summarize.ipynb",
        executed="executed/summarize.ipynb",
        registry=registry,
        env=ENV,
        outputs={"summary_path": "summary.csv"},
        name="summarize",
    )
    report = python_step(
        read_summary,
        inputs={"summary": "summary.csv"},
        outputs="report.parquet",
        registry=registry,
        name="report",
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(summarize)
    p.add_task(report)
    return p
"""
"""A definition of the notebook step 'summarize', which runs summarize.ipynb next to the
definition under ENV and writes 'summary.csv', and the python step 'report' that reads it."""

_RAISES_KEY_ERROR = "{}['no_such_column']"
"""A notebook body that raises KeyError('no_such_column')."""

_WRITES_SUMMARY = "from pathlib import Path\nPath(summary_path).write_text('n\\n1\\n')"
"""A notebook body that writes the summary output."""


def _notebook_definition(directory: Path, body: str, env: Path | None = None) -> Path:
    """Write summarize.ipynb running ``body`` and a definition running it under ``env``.

    ``None`` runs the notebook in this process's environment.
    """
    write_source_notebook(directory / "summarize.ipynb", "summary_path = 'unset'", body)
    declared = "sys.prefix" if env is None else repr(str(env))
    return _write_definition(directory, _NOTEBOOK_PIPELINE.replace("__ENV__", declared))


def _executed(directory: Path) -> Path:
    """Return the executed notebook's absolute path for a definition in ``directory``."""
    return (directory / "out" / "run1" / "executed" / "summarize.ipynb").resolve()


def _key_error_line(directory: Path) -> str:
    """Return the summary line for the notebook in ``directory`` that raised KeyError."""
    return (
        "Step 'summarize' failed: notebook summarize.ipynb raised KeyError: "
        "'no_such_column' in cell In [3]. The executed notebook, with the error, is at "
        f"{_executed(directory)}."
    )


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebook_exits_one_naming_the_step_notebook_cell_error_and_executed_path(
    tmp_path: Path,
) -> None:
    """A definition whose notebook step 'summarize' raises KeyError('no_such_column') in its body cell makes 'dagger run -c def.py --local' exit 1; stderr has "Step 'summarize' failed: notebook summarize.ipynb raised KeyError: 'no_such_column' in cell In [3]. The executed notebook, with the error, is at <path>." with the executed notebook's absolute path, and that file holds the error."""
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert _key_error_line(tmp_path) in result.stderr, result.stderr
    assert ("KeyError", "'no_such_column'") in recorded_errors(_executed(tmp_path))


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebooks_generated_command_is_in_pytasks_report_not_in_daggers_summary(
    tmp_path: Path,
) -> None:
    """For a failing notebook step, stderr contains neither 'papermill', 'ipykernel' nor 'mkdir -p', while stdout, pytask's report, still contains the papermill command."""
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    for text in ("papermill", "ipykernel", "mkdir -p"):
        assert text not in result.stderr, result.stderr
    assert "papermill" in result.stdout, result.stdout


@pytest.mark.usefixtures("jupyter_dirs")
def test_a_failing_notebook_lists_its_downstream_step_as_not_run(tmp_path: Path) -> None:
    """A python step that reads the failing notebook's output is listed on stderr as not run because an upstream step failed."""
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "Not run because an upstream step failed: 'report'." in result.stderr
    assert not _product(tmp_path, "report").exists()


def test_a_notebook_that_fails_before_it_runs_exits_one_with_the_exit_status(
    tmp_path: Path,
) -> None:
    """A notebook step whose env's python exits 1 before papermill starts makes 'dagger run' exit 1; stderr names the step and the notebook, says it left no error in an executed notebook at its path, gives 'the command exited with status 1', and does not contain the generated command."""
    env = broken_env(tmp_path / "broken_env")
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR, env=env)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    expected = (
        "Step 'summarize' failed: notebook summarize.ipynb left no error in an executed "
        f"notebook at {_executed(tmp_path)}, and the command exited with status 1; its "
        "output is in pytask's report for the step."
    )
    assert expected in result.stderr, result.stderr
    assert "papermill" not in result.stderr


@pytest.mark.usefixtures("jupyter_dirs")
def test_the_installed_dagger_script_reports_a_failing_notebook_end_to_end(
    tmp_path: Path,
) -> None:
    """End to end: the installed dagger script, run in a subprocess on a definition whose notebook raises KeyError, exits 1; its stderr has the one-line summary naming the step, the notebook, 'In [3]', the KeyError and the executed path, and no 'papermill'; its stdout has pytask's report with the notebook's traceback; the summary line is under 300 characters apart from the executed path."""
    script = Path(sys.executable).parent / "dagger"
    assert script.is_file(), f"No dagger console script next to {sys.executable}."
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR)

    completed = subprocess.run(
        [str(script), "run", "-c", str(path), "--local"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    lines = completed.stderr.splitlines()
    [summary] = [line for line in lines if "Step 'summarize' failed:" in line]
    assert summary.endswith(_key_error_line(tmp_path)), completed.stderr
    assert len(summary) - len(str(_executed(tmp_path))) < 300, summary
    assert "papermill" not in completed.stderr, completed.stderr
    assert "KeyError" in completed.stdout, completed.stdout
    assert "no_such_column" in completed.stdout, completed.stdout


@pytest.mark.usefixtures("jupyter_dirs")
def test_an_operator_fixes_the_notebook_named_in_the_message_and_reruns(
    tmp_path: Path,
) -> None:
    """Use case: after 'dagger run' reports a notebook's KeyError, editing that notebook to stop raising and running the same command again exits 0, and the notebook step and its downstream step both run and write their outputs."""
    path = _notebook_definition(tmp_path, _RAISES_KEY_ERROR)
    failed = _invoke("run", str(path))
    assert failed.exit_code == 1, _describe(failed)
    assert _key_error_line(tmp_path) in failed.stderr, failed.stderr
    assert not _product(tmp_path, "report").exists()

    write_source_notebook(
        tmp_path / "summarize.ipynb", "summary_path = 'unset'", _WRITES_SUMMARY
    )
    fixed = _invoke("run", str(path))

    assert fixed.exit_code == 0, _describe(fixed)
    assert recorded_errors(_executed(tmp_path)) == []
    assert (tmp_path / "out" / "run1" / "summary.csv").read_text() == "n\n1\n"
    assert _product(tmp_path, "report").exists()


# Other errors in step terms (MIC-7785)

_NOTEBOOK_MISTYPED = (
    _HEADER
    + """
from vivarium.dagger.frontend.notebook_step import notebook_step

NOTEBOOK = HERE / "summarize.ipynb"
ENV = sys.prefix
INPUTS = {"population_path": "populaton.parquet"}


def pipeline():
    reg = FileRegistry(HERE / "out", "run1")
    a = step(frame, "population", reg)
    nb = notebook_step(NOTEBOOK, executed="run.ipynb", registry=reg, env=ENV, inputs=INPUTS)
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(a)
    p.add_task(nb)
    return p
"""
)
"""A definition whose notebook step, named by default 'run.ipynb', reads a mistyped key,
'populaton.parquet'."""

_NOTEBOOK_MISSPELLED_PARAMETER = (
    _HEADER
    + """
from vivarium.dagger.frontend.notebook_step import notebook_step

NOTEBOOK = HERE / "report.ipynb"
EXECUTED = "run.ipynb"
ENV = sys.prefix
PARAMS = {"loaction": "179"}


def make_a():
    log("a")
    return frame()


def pipeline():
    reg = FileRegistry(HERE / "out", "run1")
    a = step(make_a, "a", reg)
    nb = notebook_step(NOTEBOOK, executed=EXECUTED, registry=reg, env=ENV, parameters=PARAMS)
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(a)
    p.add_task(nb)
    return p
"""
)
"""A definition whose notebook step, named by default 'run.ipynb', passes the misspelled
parameter 'loaction' after a step 'a' that logs when it runs."""

_UNLIKE_EVERY_KEY = (
    _HEADER
    + """

BAD = {"path": "zzz"}


def pipeline():
    reg = FileRegistry(HERE / "out", "run1")
    tasks = [
        step(frame, "population", reg),
        step(plus_one, "adults", reg, path="population"),
        python_step(plus_one, inputs=BAD, outputs="s.csv", registry=reg, name="s"),
    ]
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for task in tasks:
        p.add_task(task)
    return p
"""
)
"""A definition whose step 's' reads the key 'zzz', which is unlike every registered key."""

_SHARED_NAME = (
    _HEADER
    + """

NAME = "population"


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(frame, inputs={}, outputs="a.parquet", name=NAME)
    p.add_python_step(frame, inputs={}, outputs="b.parquet", name=NAME)
    return p
"""
)
"""A definition that adds two steps with different outputs that are both named
'population', so the Pipeline refuses the second."""

_NAME_ADDED = (
    "Step 'population': a step named 'population' was already added to the pipeline; "
    "give each step a different name= to tell them apart."
)
"""The Pipeline's message for the second step of :data:`_SHARED_NAME`."""

_TWO_NAMED_POPULATION = (
    "Two steps are named 'population'; give each a different name= to tell them apart."
)
"""The message of the verbs' own check for two steps named 'population'."""


def _shared_name_snapshot(tmp_path: Path) -> _PipelineSnapshot:
    """Return a snapshot, which a Pipeline would refuse, of two steps named 'population'."""
    registry = FileRegistry(tmp_path / "out", "run1")
    tasks = [
        python_step(_frame, inputs={}, outputs=key, registry=registry, name="population")
        for key in ("a.parquet", "b.parquet")
    ]
    return _PipelineSnapshot(tasks, tmp_path / "state", registry)


def test_a_step_that_calls_sys_exit_is_named_in_the_message(tmp_path: Path) -> None:
    """A step 'leave' that calls sys.exit(0) makes 'dagger run' exit 1 with "Step 'leave' exited with code 0 before the build finished." on stderr."""
    path = _write_definition(tmp_path, _EXITS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    expected = "Step 'leave' exited with code 0 before the build finished."
    assert expected in result.stderr, result.stderr


def test_a_system_exit_from_outside_any_step_names_the_build_not_a_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SystemExit that no step raised (here from the run verb itself) makes 'dagger run' exit 1 with "The build exited with code 5 before it finished." and no mention of a step."""
    path = _write_definition(tmp_path, _CHAIN)

    def exits(*args: object, **kwargs: object) -> None:
        raise SystemExit(5)

    monkeypatch.setattr("vivarium.dagger.frontend.cli._run", exits)
    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "The build exited with code 5 before it finished." in result.stderr, result.stderr
    assert "step" not in result.stderr.lower(), result.stderr


def test_a_notebook_step_with_a_mistyped_input_key_exits_two_naming_the_step(
    tmp_path: Path,
) -> None:
    """A definition whose notebook_step reads a mistyped key exits 2; stderr has "Step '<name>': input '<key>' is unresolved" with the did-you-mean, the definition's notebook_step(...) line, and no notebook_step.py or wiring.py frame."""
    write_source_notebook(tmp_path / "summarize.ipynb", "population_path = 'unset'")
    path = _write_definition(tmp_path, _NOTEBOOK_MISTYPED)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert "Step 'run.ipynb': input 'populaton.parquet' is unresolved" in errors, errors
    assert "Did you mean 'population.parquet'" in errors
    assert "nb = notebook_step(NOTEBOOK" in errors
    assert "notebook_step.py" not in errors
    assert "wiring.py" not in errors


def test_a_notebook_step_passing_a_misspelled_parameter_exits_two_naming_the_step_and_name(
    tmp_path: Path,
) -> None:
    """A definition whose notebook_step passes parameters={'loaction': ...} over a cell assigning 'location' makes 'dagger run -c' exit 2; stderr has "Step '<name>': the parameters cell of notebook", the quoted notebook path, "'loaction' (parameters; did you mean 'location'?)", the definition's notebook_step(...) line, and no notebook_step.py frame, and nothing runs."""
    notebook = write_source_notebook(tmp_path / "report.ipynb", "location = 'unset'")
    path = _write_definition(tmp_path, _NOTEBOOK_MISSPELLED_PARAMETER)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert "Step 'run.ipynb': the parameters cell of notebook " in errors, errors
    assert repr(str(notebook.resolve())) in errors
    assert "'loaction' (parameters; did you mean 'location'?)" in errors
    assert "nb = notebook_step(NOTEBOOK" in errors
    assert "notebook_step.py" not in errors
    assert _calls(tmp_path) == []
    assert not _product(tmp_path, "a").exists()
    assert not (tmp_path / "state").exists()


def test_an_input_key_with_no_close_match_exits_two_listing_the_registered_keys(
    tmp_path: Path,
) -> None:
    """A step reading a key unlike every registered key exits 2; stderr names the step and the key and lists the keys registered so far in sorted order, and has no did-you-mean."""
    path = _write_definition(tmp_path, _UNLIKE_EVERY_KEY)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    expected = (
        "Step 's': input 'zzz' is unresolved: no registered producer and no root input. "
        "The keys registered so far are 'adults.parquet', 'population.parquet'."
    )
    assert expected in errors, errors
    assert "Did you mean" not in errors


def test_two_steps_with_one_name_exit_two_with_the_name_and_how_to_tell_them_apart(
    tmp_path: Path,
) -> None:
    """A definition that adds two steps with different outputs and the same name makes 'dagger run' and 'dagger preview' exit 2 with the Pipeline's message "Step 'population': a step named 'population' was already added to the pipeline; give each step a different name= to tell them apart." on stderr, no "Task names must be unique", no "['", and nothing run."""
    path = _write_definition(tmp_path, _SHARED_NAME)

    for verb in ("run", "preview"):
        result = _invoke(verb, str(path))

        assert result.exit_code == 2, f"{verb}\n{_describe(result)}"
        assert _NAME_ADDED in result.stderr, result.stderr
        assert "Task names must be unique" not in result.output, result.output
        assert "['" not in result.output, result.output
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "command",
    ["run --until", "preview --until", "force"],
    ids=["run", "preview", "force"],
)
def test_two_steps_with_one_name_and_an_unknown_step_exit_two_with_the_pipelines_message(
    tmp_path: Path, command: str
) -> None:
    """With a definition that adds two steps named 'population' and an unknown step 'bb' given to --until or force, the command exits 2 with the Pipeline's message for the second step and no usage line or "Invalid value"."""
    path = _write_definition(tmp_path, _SHARED_NAME)
    verb, *option = command.split()

    result = _invoke(verb, str(path), *option, "bb")

    assert result.exit_code == 2, _describe(result)
    assert _NAME_ADDED in result.stderr, result.stderr
    assert "Usage:" not in result.stderr, result.stderr
    assert "Invalid value" not in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()


# The command's checks after loading, which a Pipeline makes unreachable (MIC-7810)


def _frame() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _touch(name: str, node: pytask.PNode) -> Task:
    """Return a hand-built command step ``name`` whose one output is ``node``."""
    assert isinstance(node, pytask.PathNode)
    return Task(
        name=name,
        run=f"touch {node.path}",
        inputs={},
        outputs={"product": node},
        resources={},
        env=None,
    )


def _definition_problem(call: Callable[[], object]) -> click.ClickException:
    """Return the click error that ``call`` raises, which must have exit status 2."""
    with pytest.raises(click.ClickException) as info:
        call()
    assert info.value.exit_code == 2, info.value
    return info.value


def test_the_single_producer_check_names_the_key_and_both_steps(tmp_path: Path) -> None:
    """On a snapshot of two python steps 'first' and 'second' that produce 'shared.parquet' through one registry, the command's single-producer check raises a click error with exit status 2 and the message "Output 'shared.parquet' is produced by more than one step: 'first', 'second'."."""
    registry = FileRegistry(tmp_path / "out", "run1")
    claim: dict[str, Any] = {"inputs": {}, "outputs": "shared.parquet", "registry": registry}
    tasks = [python_step(_frame, name=name, **claim) for name in ("first", "second")]
    snapshot = _PipelineSnapshot(tasks, tmp_path / "state", registry)

    error = _definition_problem(lambda: frontend_cli._check_single_producers(snapshot))

    assert error.message == (
        "Output 'shared.parquet' is produced by more than one step: 'first', 'second'."
    )


@pytest.mark.parametrize(
    "second",
    ["same", "other_spelling", "named_node"],
    ids=["same node", "two spellings of one path", "a node with another name"],
)
def test_the_single_producer_check_without_a_registry_names_the_node(
    tmp_path: Path, second: str
) -> None:
    """On a snapshot with no registry whose steps 'one' and 'two' output file_node(<tmp>/same.txt) and, for 'two', the same node, an unnamed PathNode through 'sub/..', or a PathNode named 'custom' on that file, the single-producer check raises a click error with exit status 2 naming the first node's name and both steps."""
    (tmp_path / "sub").mkdir()
    same = tmp_path / "same.txt"
    nodes: dict[str, pytask.PNode] = {
        "same": file_node(same),
        "other_spelling": pytask.PathNode(path=tmp_path / "sub" / ".." / "same.txt"),
        "named_node": pytask.PathNode(name="custom", path=same),
    }
    tasks = [_touch("one", file_node(same)), _touch("two", nodes[second])]
    snapshot = _PipelineSnapshot(tasks, tmp_path / "state")

    error = _definition_problem(lambda: frontend_cli._check_single_producers(snapshot))

    key = file_node(same).name
    assert error.message == f"Output '{key}' is produced by more than one step: 'one', 'two'."


def test_unnamed_path_nodes_on_different_files_pass_the_single_producer_check(
    tmp_path: Path,
) -> None:
    """On a snapshot whose steps output unnamed PathNodes on different files, the single-producer check raises nothing."""
    tasks = [
        _touch(name, pytask.PathNode(path=tmp_path / f"{name}.txt")) for name in ("a", "b")
    ]

    frontend_cli._check_single_producers(_PipelineSnapshot(tasks, tmp_path / "state"))


@pytest.mark.parametrize("verb", ["run", "preview"])
@pytest.mark.parametrize("requested", [(), ("bb",)], ids=["no step", "an unknown step"])
def test_the_verbs_shared_name_check_is_reported_as_a_definition_problem(
    tmp_path: Path, verb: str, requested: tuple[str, ...]
) -> None:
    """On a snapshot of two steps named 'population', the private verb that the command calls, run with no step or with the unknown step 'bb' requested, is reported by the command as a click error with exit status 2 and the message "Two steps are named 'population'; ...", not as a bad parameter, and writes no state."""
    snapshot = _shared_name_snapshot(tmp_path)
    calls: dict[str, Callable[[], object]] = {
        "run": lambda: _run(snapshot, only=requested or None),
        "preview": lambda: frontend_cli._quiet_preview(snapshot, requested),
    }

    def verb_call() -> None:
        with frontend_cli._verb_errors(snapshot, requested, "'--until'"):
            calls[verb]()

    error = _definition_problem(verb_call)

    assert not isinstance(error, click.BadParameter)
    assert error.message == _TWO_NAMED_POPULATION
    assert not (tmp_path / "state").exists()


def test_the_root_check_names_a_root_under_a_file(tmp_path: Path) -> None:
    """On a snapshot whose root is under an existing file, the command's root check raises a click error with exit status 2 naming the root and saying the file is not a directory."""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    snapshot = _PipelineSnapshot([], blocker / "state")

    error = _definition_problem(lambda: frontend_cli._check_root(snapshot.root))

    assert error.message == (
        f"Cannot use the pipeline root {blocker / 'state'}: {blocker} is not a directory"
    )


def test_the_command_runs_the_snapshot_it_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'dagger run' freezes the loaded pipeline once: the snapshot its single-producer check and its root check receive is the object it passes to the private run verb."""
    path = _write_definition(tmp_path, _CHAIN)
    seen: dict[str, object] = {}
    check_single_producers = frontend_cli._check_single_producers
    check_root = frontend_cli._check_root

    def record_producers(snapshot: _PipelineSnapshot) -> None:
        seen["producers"] = snapshot
        check_single_producers(snapshot)

    def record_root(root: Path) -> None:
        seen["root"] = root
        check_root(root)

    def record_run(snapshot: _PipelineSnapshot, **kwargs: object) -> dict[str, object]:
        seen["run"] = snapshot
        return {}

    monkeypatch.setattr(frontend_cli, "_check_single_producers", record_producers)
    monkeypatch.setattr(frontend_cli, "_check_root", record_root)
    monkeypatch.setattr(frontend_cli, "_run", record_run)

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    ran = seen["run"]
    assert isinstance(ran, _PipelineSnapshot)
    assert seen["producers"] is ran
    assert seen["root"] == ran.root
    assert _calls(tmp_path) == []


# A definition that does not parse (MIC-7785)

_UNPARSABLE = "import sys\n\n\ndef pipeline(:\n    pass\n"
"""A definition whose fourth line, 'def pipeline(:', is a syntax error."""

_BADLY_INDENTED = "def pipeline():\nreturn None\n"
"""A definition whose second line, 'return None', is not indented."""


def _assert_short_report(
    result: Result, path: Path, line: int, source: str, kind: str, caret: bool = True
) -> None:
    """Assert that ``result`` shows only where ``path`` failed to parse, then dagger's line."""
    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    lines = errors.splitlines()
    [where] = [index for index, text in enumerate(lines) if text.startswith('  File "')]
    assert path.name in lines[where], errors
    assert lines[where].endswith(f", line {line}"), errors
    assert lines[where + 1].strip() == source, errors
    if caret:
        carets = lines[where + 2]
        assert "^" in carets and set(carets) <= {" ", "~", "^"}, errors
    assert _in_order(
        errors, lines[where], source, f"\n{kind}: ", "Could not load the definition"
    ), errors
    assert "Traceback (most recent call last)" not in errors, errors
    assert not re.search(r"frames? hidden", errors), errors
    for fragment in ("vivarium/dagger", "definition.py", "importlib"):
        assert fragment not in errors, errors


def test_a_syntax_error_in_the_definition_shows_only_the_file_line_caret_and_error(
    tmp_path: Path,
) -> None:
    """A definition file with 'def pipeline(:' exits 2; stderr shows the definition's path and line number, the source line, a caret line, and 'SyntaxError: ...', then the 'Could not load the definition' line, with no 'Traceback (most recent call last)', no 'frames hidden' line, and no vivarium/dagger, definition.py or importlib frame."""
    path = _write_definition(tmp_path, _UNPARSABLE)

    result = _invoke("run", str(path))

    _assert_short_report(result, path, 4, "def pipeline(:", "SyntaxError")


def test_an_indentation_error_in_the_definition_is_shown_the_same_way(tmp_path: Path) -> None:
    """A definition file with a badly indented line exits 2 and shows only its path, line, source line and 'IndentationError: ...', with no traceback."""
    path = _write_definition(tmp_path, _BADLY_INDENTED)

    result = _invoke("run", str(path))

    _assert_short_report(result, path, 2, "return None", "IndentationError", caret=False)


def test_a_syntax_error_in_a_module_the_definition_imports_keeps_the_import_line(
    tmp_path: Path,
) -> None:
    """A definition that imports a sibling module with a syntax error exits 2; stderr shows the definition's import line, a 'frames hidden' line, and the sibling's path, line and 'SyntaxError: ...'."""
    sibling = f"sibling_{uuid.uuid4().hex[:12]}"
    (tmp_path / f"{sibling}.py").write_text("x = 1\ndef broken(:\n    pass\n")
    path = _write_definition(tmp_path, f"import {sibling}\n\n\ndef pipeline():\n    pass\n")

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert _in_order(
        errors,
        f"import {sibling}",
        "framework frame",
        f'{sibling}.py", line 2',
        "def broken(:",
        "SyntaxError: ",
        "Could not load the definition",
    ), errors
    assert re.search(r"\[\d+ framework frames? hidden\]", errors), errors


def test_a_syntax_error_with_preview_and_force_is_shown_the_same_way(tmp_path: Path) -> None:
    """'dagger preview' and 'dagger force' on a definition with a syntax error exit 2 with the same short report as 'dagger run'."""
    path = _write_definition(tmp_path, _UNPARSABLE)
    ran = _invoke("run", str(path))
    _assert_short_report(ran, path, 4, "def pipeline(:", "SyntaxError")

    cases: list[tuple[str, list[str]]] = [("preview", []), ("force", ["a"])]
    for verb, steps in cases:
        result = _invoke(verb, str(path), *steps)

        _assert_short_report(result, path, 4, "def pipeline(:", "SyntaxError")
        assert result.stderr == ran.stderr, verb


# Definitions import from the frontend (MIC-7785)


def _imported_modules(definition: Path) -> dict[str, set[str]]:
    """Map each module ``definition`` imports from to the names it imports from it."""
    imported: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(definition.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.setdefault(node.module, set()).update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.setdefault(alias.name, set())
    return imported


def test_the_example_definitions_use_pipeline_and_import_no_registry() -> None:
    """Each of the pipeline, location inputs, command, simulation and several outputs example definitions imports Pipeline from vivarium.dagger.frontend, imports nothing from vivarium.dagger.backend, and neither imports nor names FileRegistry or passes registry=."""
    backend_parts = ["vivarium", "dagger", "backend"]
    examples = ("pipeline", "location_inputs", "command", "simulation", "several_outputs")
    for example in examples:
        definition = Path(__file__).parent / f"{example}_example" / "pipeline.py"
        imported = _imported_modules(definition)

        assert "Pipeline" in imported.get("vivarium.dagger.frontend", set()), example
        backend = [module for module in imported if module.split(".")[:3] == backend_parts]
        assert backend == [], (example, backend)
        source = definition.read_text()
        assert "FileRegistry" not in source, example
        keywords = {
            node.arg for node in ast.walk(ast.parse(source)) if isinstance(node, ast.keyword)
        }
        assert "registry" not in keywords, example


# The command step example (MIC-7740)

_COMMAND_EXAMPLE = Path(__file__).parent / "command_example"
"""The command step example, whose definition the docs page includes."""

_EXAMPLE_STEPS = ["population", "adults", "summary"]
"""The command example's steps, in dependency order."""

_EXAMPLE_PRODUCTS = ("population.csv", "adults.csv", "summary.csv")


def _copy_command_example(directory: Path) -> Path:
    """Copy the command example's definition and script to ``directory``; return the first."""
    for name in ("pipeline.py", "select_adults.py"):
        shutil.copy(_COMMAND_EXAMPLE / name, directory)
    return directory / "pipeline.py"


def _example_product(directory: Path, key: str) -> Path:
    """Return the path of output ``key`` of the command example copied to ``directory``."""
    return directory / "output" / "example" / key


def test_the_command_example_runs_with_dagger_run_local(tmp_path: Path) -> None:
    """'dagger run -c pipeline.py --local' on a copy of tests/frontend/command_example (pipeline.py and select_adults.py) in tmp_path exits 0, and adults.csv holds the two adult rows and summary.csv the adult total of 70."""
    definition = _copy_command_example(tmp_path)

    result = _invoke("run", str(definition))

    assert result.exit_code == 0, _describe(result)
    adults = _example_product(tmp_path, "adults.csv").read_text().splitlines()
    assert adults == ["age_group,count", "15-64,55", "65+,15"]
    summary = _example_product(tmp_path, "summary.csv").read_text().splitlines()
    assert summary == ["adults", "70"]


def test_the_command_example_previews_every_step_before_its_first_run(tmp_path: Path) -> None:
    """'dagger preview -c pipeline.py' on a fresh copy of tests/frontend/command_example exits 0, lists population, adults and summary as would-run in that order, and writes no output."""
    definition = _copy_command_example(tmp_path)

    result = _invoke("preview", str(definition))

    assert result.exit_code == 0, _describe(result)
    assert _would_run(result) == _EXAMPLE_STEPS
    written = [key for key in _EXAMPLE_PRODUCTS if _example_product(tmp_path, key).exists()]
    assert written == []


def test_editing_the_command_example_script_makes_preview_name_it(tmp_path: Path) -> None:
    """With the copy's pipeline loaded by load_pipeline and frozen with a stable code identity on each step, after a successful _run() an edit to the copied select_adults.py makes _preview() report that adults would run with "file 'select_adults.py'" among its reasons."""
    pipeline = stable_snapshot(load_pipeline(str(_copy_command_example(tmp_path))))
    assert _run(pipeline) == dict.fromkeys(_EXAMPLE_STEPS, TaskOutcome.SUCCESS)

    with Perturbed(tmp_path / "select_adults.py", b"\n# An edit.\n"):
        plan = {step.name: step for step in _preview(pipeline)}

    adults = plan["adults"]
    assert adults.would_run
    nodes = [reason.node for reason in adults.reasons]
    assert "file 'select_adults.py'" in nodes, adults.reasons


# The several outputs example (MIC-7827)

_SEVERAL_OUTPUTS_EXAMPLE = Path(__file__).parent / "several_outputs_example"
"""The example of a python step with several outputs, whose definition the docs include."""

_SPLIT_EXAMPLE_STEPS = ["population", "split", "summary"]
"""The several outputs example's steps, in dependency order."""


def _copy_several_outputs_example(directory: Path) -> Path:
    """Copy the several outputs example's definition to ``directory``; return the copy."""
    return Path(shutil.copy(_SEVERAL_OUTPUTS_EXAMPLE / "pipeline.py", directory))


def test_the_several_outputs_example_runs_with_dagger_run_local(tmp_path: Path) -> None:
    """'dagger run -c pipeline.py --local' on a copy of tests/frontend/several_outputs_example in tmp_path exits 0; children.csv holds the 0-14 row, adults.parquet the two adult rows, and summary.csv the adult total of 70."""
    definition = _copy_several_outputs_example(tmp_path)

    result = _invoke("run", str(definition))

    assert result.exit_code == 0, _describe(result)
    children = _example_product(tmp_path, "children.csv").read_text().splitlines()
    assert children == ["age_group,count", "0-14,30"]
    adults = pd.read_parquet(_example_product(tmp_path, "adults.parquet"))
    assert adults["age_group"].tolist() == ["15-64", "65+"]
    summary = _example_product(tmp_path, "summary.csv").read_text().splitlines()
    assert summary == ["adults", "70"]


def test_the_several_outputs_example_previews_every_step_before_its_first_run(
    tmp_path: Path,
) -> None:
    """'dagger preview -c pipeline.py' on a fresh copy of tests/frontend/several_outputs_example exits 0 and lists population, split and summary as would-run in that order."""
    definition = _copy_several_outputs_example(tmp_path)

    result = _invoke("preview", str(definition))

    assert result.exit_code == 0, _describe(result)
    assert _would_run(result) == _SPLIT_EXAMPLE_STEPS


def test_preview_of_the_several_outputs_example_names_each_missing_output(
    tmp_path: Path,
) -> None:
    """With the copy's pipeline loaded by load_pipeline and frozen with a stable code identity on each step, after a successful _run() the removal of children.csv and adults.parquet makes _preview() report that split would run with "file 'children.csv'" and "file 'adults.parquet'" among its reasons."""
    pipeline = stable_snapshot(load_pipeline(str(_copy_several_outputs_example(tmp_path))))
    assert _run(pipeline) == dict.fromkeys(_SPLIT_EXAMPLE_STEPS, TaskOutcome.SUCCESS)

    for key in ("children.csv", "adults.parquet"):
        _example_product(tmp_path, key).unlink()
    plan = {step.name: step for step in _preview(pipeline)}

    split = plan["split"]
    assert split.would_run
    nodes = [reason.node for reason in split.reasons]
    assert {"file 'children.csv'", "file 'adults.parquet'"} <= set(nodes), split.reasons


# The Pipeline builder (MIC-7805, MIC-7810)

_BUILDER_HEADER = (
    _HEADER
    + """
from vivarium.dagger.frontend import Pipeline


def make_a():
    log("a")
    return frame()


def make_b(x):
    log("b")
    return plus_one(x)
"""
)
"""The start of every Pipeline definition: ``make_a`` and ``make_b`` log that they ran."""

_FIRST_STEP = (
    'p.add_python_step(make_a, inputs={}, outputs="population.parquet", name="first")'
)
"""A step 'first' that produces 'population.parquet'."""

_SAME_NAME_STEP = (
    'p.add_python_step(make_a, inputs={}, outputs="other.parquet", name="first")'
)
"""A step with :data:`_FIRST_STEP`'s name and another output."""

_SAME_KEY_STEP = (
    'p.add_python_step(make_a, inputs={}, outputs="population.parquet", name="second")'
)
"""A step 'second' with :data:`_FIRST_STEP`'s output."""

_MISTYPED_STEP = (
    'p.add_python_step(make_b, inputs={"x": "populaton.parquet"}, outputs="b.csv", name="b")'
)
"""A step 'b' that reads the mistyped key 'populaton.parquet'."""

_HIDDEN_FRAMES = re.compile(r"  \[\d+ framework frames? hidden\]")
"""The line a trimmed traceback shows in place of its framework frames."""


def _builder_definition(*steps: str) -> str:
    """Return a definition that adds ``steps``, each one line of source, to one Pipeline."""
    added = "".join(f"    {step}\n" for step in steps)
    return (
        f"{_BUILDER_HEADER}\n\ndef pipeline():\n"
        f'    p = Pipeline(HERE / "out", "run1")\n{added}    return p\n'
    )


def _last_frame(lines: list[str]) -> int:
    """Return the index of the last ``File`` line of a traceback's ``lines``."""
    frames = [index for index, line in enumerate(lines) if line.startswith('  File "')]
    assert frames, "\n".join(lines)
    return frames[-1]


def test_a_pipeline_step_with_a_name_already_added_exits_two_with_the_definitions_line(
    tmp_path: Path,
) -> None:
    """'dagger preview' and 'dagger run --local' of a definition that adds a second step named like the first through a Pipeline exit 2; stderr names the step, its traceback's last frame is the definition's second p.add_python_step( line, no frame of vivarium.dagger is shown, and no step body runs."""
    path = _write_definition(tmp_path, _builder_definition(_FIRST_STEP, _SAME_NAME_STEP))

    for verb in ("preview", "run"):
        result = _invoke(verb, str(path))

        assert result.exit_code == 2, f"{verb}\n{_describe(result)}"
        errors = result.stderr
        assert "Step 'first':" in errors, errors
        lines = errors.splitlines()
        last = _last_frame(lines)
        assert path.name in lines[last], errors
        assert lines[last].endswith(", in pipeline"), errors
        assert lines[last + 1].strip() == _SAME_NAME_STEP, errors
        frames = [line for line in lines if line.startswith('  File "')]
        assert not any("vivarium/dagger/" in frame for frame in frames), errors
    assert _calls(tmp_path) == []


def test_a_pipeline_step_producing_a_key_already_produced_exits_two_naming_both_steps_and_the_key(
    tmp_path: Path,
) -> None:
    """'dagger run --local' of a definition whose second Pipeline step produces the first step's key exits 2, stderr names both steps and the key, and no step body runs."""
    path = _write_definition(tmp_path, _builder_definition(_FIRST_STEP, _SAME_KEY_STEP))

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    messages = [line for line in result.stderr.splitlines() if "Step 'second':" in line]
    assert messages, result.stderr
    message = messages[-1]
    assert "WiringError" in message, result.stderr
    assert "'first'" in message, result.stderr
    assert "'population.parquet'" in message, result.stderr
    assert _calls(tmp_path) == []


def test_a_definition_time_error_in_a_pipeline_method_points_at_the_modelers_line(
    tmp_path: Path,
) -> None:
    """'dagger preview' of a definition whose p.add_python_step names an input key no step produces exits 2; the trimmed traceback's last frame is the definition's p.add_python_step( line, followed by the hidden-frames line and the WiringError naming the step and the key."""
    path = _write_definition(tmp_path, _builder_definition(_FIRST_STEP, _MISTYPED_STEP))

    result = _invoke("preview", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    lines = errors.splitlines()
    last = _last_frame(lines)
    assert path.name in lines[last], errors
    assert lines[last + 1].strip() == _MISTYPED_STEP, errors
    hidden = last + 2
    # Python 3.11+ prints a caret line under a call that does not cover its whole line.
    carets = lines[hidden] if hidden < len(lines) else ""
    if "^" in carets and set(carets) <= {" ", "~", "^"}:
        hidden += 1
    assert hidden + 1 < len(lines), errors
    assert _HIDDEN_FRAMES.fullmatch(lines[hidden]), errors
    error = "WiringError: Step 'b': input 'populaton.parquet' is unresolved"
    assert error in lines[hidden + 1], errors
    assert _calls(tmp_path) == []


def test_a_preview_of_a_pipeline_definition_does_not_import_jobmon(tmp_path: Path) -> None:
    """In a fresh interpreter, 'dagger preview -c def.py' of a definition built with Pipeline exits 0 and leaves jobmon out of sys.modules."""
    path = _write_definition(tmp_path, _builder_definition(_FIRST_STEP))

    completed = subprocess.run(
        [sys.executable, "-c", _HELP_AND_PREVIEW, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "jobmon modules: []" in completed.stdout.splitlines(), completed.stdout
    assert _calls(tmp_path) == []


def test_the_pipeline_example_keeps_its_state_under_the_output_roots_dagger_directory(
    tmp_path: Path,
) -> None:
    """'dagger run -c pipeline.py --local' on a copy of tests/frontend/pipeline_example writes the outputs under output/example and pytask.lock under output/.dagger/example, and creates no state directory beside the definition."""
    shutil.copy(Path(__file__).parent / "pipeline_example" / "pipeline.py", tmp_path)

    result = _invoke("run", str(tmp_path / "pipeline.py"))

    assert result.exit_code == 0, _describe(result)
    products = tmp_path / "output" / "example"
    for key in ("population.parquet", "adults.parquet", "summary.csv"):
        assert (products / key).is_file(), key
    assert (tmp_path / "output" / ".dagger" / "example" / "pytask.lock").is_file()
    assert not (products / "pytask.lock").exists()
    assert not (tmp_path / "state").exists()


def test_dagger_preview_run_and_force_of_a_definition_that_returns_its_pipeline_exit_zero(
    tmp_path: Path,
) -> None:
    """For a definition that makes a Pipeline, adds two steps and returns it, 'dagger preview' lists both steps as would-run, 'dagger run --local' runs both and exits 0, and 'dagger force --local first' runs 'first' again and exits 0."""
    second = (
        'p.add_python_step(make_b, inputs={"x": "population.parquet"}, outputs="b.csv", '
        'name="b")'
    )
    path = _write_definition(tmp_path, _builder_definition(_FIRST_STEP, second))
    products = tmp_path / "out" / "run1"

    previewed = _invoke("preview", str(path))

    assert previewed.exit_code == 0, _describe(previewed)
    assert _would_run(previewed) == ["first", "b"]
    assert _calls(tmp_path) == []

    ran = _invoke("run", str(path))

    assert ran.exit_code == 0, _describe(ran)
    assert _calls(tmp_path) == ["a", "b"]
    assert (products / "population.parquet").is_file()
    assert (products / "b.csv").is_file()

    forced = _invoke("force", str(path), "first")

    assert forced.exit_code == 0, _describe(forced)
    assert _calls(tmp_path)[2:3] == ["a"]


# Errors and terminal output (MIC-7848)

_FIRST_BAD_VALUE = 'raise ValueError("bad value 0")'
_SECOND_BAD_VALUE = 'raise ValueError("bad value 1")'

_GROUP_WHILE_LOADING = f"""
def first_check():
    {_FIRST_BAD_VALUE}


def second_check():
    {_SECOND_BAD_VALUE}


def failures(*checks):
    errors = []
    for check in checks:
        try:
            check()
        except ValueError as error:
            errors.append(error)
    return errors


def pipeline():
    raise ExceptionGroup("two checks failed", failures(first_check, second_check))
"""
"""A definition whose pipeline() raises an ExceptionGroup of the ValueErrors that two
checks raised, each on its own line."""

_INNER_BAD_TYPE = 'raise TypeError("bad inner type")'
_OUTER_BAD_VALUE = 'raise ValueError("bad outer value")'

_NESTED_GROUP_WHILE_LOADING = f"""
def caught(check):
    try:
        check()
    except Exception as error:
        return error


def inner_check():
    {_INNER_BAD_TYPE}


def outer_check():
    {_OUTER_BAD_VALUE}


def inner_checks():
    raise ExceptionGroup("inner checks failed", [caught(inner_check)])


def pipeline():
    raise ExceptionGroup("outer checks failed", [caught(outer_check), caught(inner_checks)])
"""
"""A definition whose pipeline() raises an ExceptionGroup of a ValueError and an inner
ExceptionGroup that holds a TypeError."""


def _shown_frames(errors: str) -> list[str]:
    """Return the ``File`` lines of a traceback, including an exception group's."""
    return [line for line in errors.splitlines() if line.lstrip(" |").startswith('File "')]


def _assert_no_framework_frames(errors: str, path: Path) -> None:
    """Assert that ``errors`` shows a frame of ``path`` and no framework frame."""
    frames = _shown_frames(errors)
    assert any(path.name in line for line in frames), errors
    for line in frames:
        assert not any(part in line for part in _FRAMEWORK_FILES), line
        assert "vivarium/dagger/" not in line, line


def _with_closed_stdout(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the installed dagger script with ``args``, its stdout a pipe that no one reads."""
    script = Path(sys.executable).parent / "dagger"
    assert script.is_file(), f"No dagger console script next to {sys.executable}."
    read_end, write_end = os.pipe()
    # Closing the read end first makes every write to stdout fail, as it does once a
    # reader such as 'head -1' has exited, without depending on when the reader exits.
    os.close(read_end)
    try:
        return subprocess.run(
            [str(script), *args],
            cwd=directory,
            stdout=write_end,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
    finally:
        os.close(write_end)


def test_an_exception_group_raised_while_loading_shows_each_sub_exception(
    tmp_path: Path,
) -> None:
    """A definition that raises ExceptionGroup('two checks failed', [ValueError('bad value 0'), ValueError('bad value 1')]) while it loads makes 'dagger preview -c' exit 2; stderr shows the group's message, both sub-exceptions' types and messages and the user lines that raised them, and no framework frames."""
    path = _write_definition(tmp_path, _GROUP_WHILE_LOADING)

    result = _invoke("preview", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert "two checks failed" in errors, errors
    assert _in_order(errors, _FIRST_BAD_VALUE, "ValueError: bad value 0"), errors
    assert _in_order(errors, _SECOND_BAD_VALUE, "ValueError: bad value 1"), errors
    _assert_no_framework_frames(errors, path)


def test_a_nested_exception_group_shows_the_inner_groups_sub_exceptions(
    tmp_path: Path,
) -> None:
    """A definition whose ExceptionGroup holds a second ExceptionGroup makes 'dagger preview -c' show the inner group's sub-exceptions' types and messages as well as the outer ones."""
    path = _write_definition(tmp_path, _NESTED_GROUP_WHILE_LOADING)

    result = _invoke("preview", str(path))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert "outer checks failed" in errors, errors
    assert "inner checks failed" in errors, errors
    assert _in_order(errors, _OUTER_BAD_VALUE, "ValueError: bad outer value"), errors
    assert _in_order(errors, _INNER_BAD_TYPE, "TypeError: bad inner type"), errors
    _assert_no_framework_frames(errors, path)


def test_run_local_with_a_closed_stdout_exits_one_without_blaming_the_build(
    tmp_path: Path,
) -> None:
    """'dagger run -c <definition> --local' run in a subprocess whose stdout is a pipe with its read end closed exits with status 1, and its stderr contains neither 'The build exited' nor "Could not read or write the pipeline's state"."""
    path = _write_definition(tmp_path, _PASSES)

    completed = _with_closed_stdout(tmp_path, "run", "-c", str(path), "--local")

    assert completed.returncode == 1, completed.stderr
    assert "The build exited" not in completed.stderr, completed.stderr
    assert (
        "Could not read or write the pipeline's state" not in completed.stderr
    ), completed.stderr


def test_preview_with_a_closed_stdout_exits_one_without_blaming_the_build(
    tmp_path: Path,
) -> None:
    """'dagger preview -c <definition>' run in a subprocess whose stdout is a pipe with its read end closed exits with status 1, and its stderr contains neither 'The build exited' nor "Could not read or write the pipeline's state"."""
    path = _write_definition(tmp_path, _PASSES)

    completed = _with_closed_stdout(tmp_path, "preview", "-c", str(path))

    assert completed.returncode == 1, completed.stderr
    assert "The build exited" not in completed.stderr, completed.stderr
    assert (
        "Could not read or write the pipeline's state" not in completed.stderr
    ), completed.stderr


def test_help_shows_no_rst_double_backticks() -> None:
    """'dagger --help' and '--help' for each of run, preview, force, restart, show and graph print no double backtick (``)."""
    commands: list[list[str]] = [
        [],
        ["run"],
        ["preview"],
        ["force"],
        ["restart"],
        ["show"],
        ["graph"],
    ]
    for command in commands:
        result = _dagger(*command, "--help")

        assert result.exit_code == 0, (command, _describe(result))
        assert "``" not in result.output, (command, result.output)


# Step order


_OUT_OF_ORDER = (
    "Step 'consumer' depends on step 'producer', which comes after it; add step "
    "'producer' before step 'consumer'."
)
"""The message for the step 'consumer' that is added before the step 'producer' it depends on."""

_CONSUMER_FIRST = (
    _HEADER
    + """

def command(name, run, inputs, outputs):
    return Task(name=name, run=run, inputs=inputs, outputs=outputs, resources={}, env=None)


def pipeline():
    a, b = HERE / "a.txt", HERE / "b.txt"
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(
        command("consumer", f"cp {a} {b}", {"source": file_node(a)}, {"target": file_node(b)})
    )
    p.add_task(command("producer", f"touch {a}", {}, {"target": file_node(a)}))
    return p
"""
)
"""A definition that adds with add_task the hand-built command step 'consumer', which reads
a.txt beside the definition, and then 'producer', which writes a.txt."""

_EIGHT_INDEPENDENT = f"""\
from pathlib import Path

from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for name in {EIGHT!r}:
        p.add_command_step("touch {{out}}", outputs={{"out": f"{{name}}.txt"}}, name=name)
    return p
"""
"""A definition of the independent command steps of ``EIGHT``, added in that order."""

_FAILED_AND_BLOCKED = """\
from pathlib import Path

from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent

PAIRS = (
    ("zulu_fails", "yankee_down"),
    ("alpha_fails", "xray_down"),
    ("mike_fails", "charlie_down"),
    ("bravo_fails", "delta_down"),
)


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    for fails, down in PAIRS:
        p.add_command_step("exit 1", outputs={"out": f"{fails}.txt"}, name=fails)
        p.add_command_step(
            "cp {src} {out}",
            inputs={"src": f"{fails}.txt"},
            outputs={"out": f"{down}.txt"},
            name=down,
        )
    return p
"""
"""A definition of four command steps that run 'exit 1', each followed by a step that reads
its output: zulu_fails, yankee_down, alpha_fails, xray_down, mike_fails, charlie_down,
bravo_fails, delta_down."""


def test_run_exits_two_for_a_step_added_before_the_step_it_depends_on(tmp_path: Path) -> None:
    """'dagger run --local' on a definition that adds with add_task the hand-built command step 'consumer', which reads a.txt beside the definition, and then 'producer', which writes a.txt, exits 2; stderr has "Step 'consumer' depends on step 'producer', which comes after it; add step 'producer' before step 'consumer'." and no step ran, so a.txt does not exist."""
    path = _write_definition(tmp_path, _CONSUMER_FIRST)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert _OUT_OF_ORDER in result.stderr, result.stderr
    assert not (tmp_path / "a.txt").exists()


def test_preview_exits_two_for_a_step_added_before_the_step_it_depends_on(
    tmp_path: Path,
) -> None:
    """'dagger preview' on a definition that adds with add_task the hand-built command step 'consumer', which reads a.txt beside the definition, and then 'producer', which writes a.txt, exits 2; stderr has "Step 'consumer' depends on step 'producer', which comes after it; add step 'producer' before step 'consumer'." and stdout has no plan line."""
    path = _write_definition(tmp_path, _CONSUMER_FIRST)

    result = _invoke("preview", str(path))

    assert result.exit_code == 2, _describe(result)
    assert _OUT_OF_ORDER in result.stderr, result.stderr
    assert not any(_PLAN_LINE.fullmatch(line) for line in _plan(result)), result.stdout


def test_preview_prints_the_plan_in_definition_order(tmp_path: Path) -> None:
    """'dagger preview' on a definition of the independent steps zeta, alpha, mike, bravo, yankee, charlie, xray, delta, added in that order, exits 0, prints its eight 'run <step>' lines in that order, and ends with the line '8 of 8 steps would run.' as its last line."""
    path = _write_definition(tmp_path, _EIGHT_INDEPENDENT)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    assert _would_run(result) == list(EIGHT), result.stdout
    assert _plan(result)[-1] == "8 of 8 steps would run.", result.stdout


def test_run_prints_failed_steps_and_blocked_steps_in_definition_order(
    tmp_path: Path,
) -> None:
    """'dagger run --local' on a definition that adds eight command steps in this order, zulu_fails, yankee_down (reads the output of zulu_fails), alpha_fails, xray_down (reads the output of alpha_fails), mike_fails, charlie_down (reads the output of mike_fails), bravo_fails, delta_down (reads the output of bravo_fails), in which each *_fails step runs 'exit 1', exits 1; the lines of stderr that, once click's "Error: " prefix is removed, start with "Step '" and contain "failed" name zulu_fails, alpha_fails, mike_fails, bravo_fails in that order, and stderr has "Not run because an upstream step failed: 'yankee_down', 'xray_down', 'charlie_down', 'delta_down'.", which is the order of the definition and not the order the steps ran in."""
    path = _write_definition(tmp_path, _FAILED_AND_BLOCKED)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    # click starts the first line of the error with "Error: ".
    lines = [line.removeprefix("Error: ") for line in result.stderr.splitlines()]
    failed = [
        line.split("'")[1] for line in lines if line.startswith("Step '") and "failed" in line
    ]
    assert failed == ["zulu_fails", "alpha_fails", "mike_fails", "bravo_fails"], result.stderr
    blocked = "'yankee_down', 'xray_down', 'charlie_down', 'delta_down'"
    assert (
        f"Not run because an upstream step failed: {blocked}." in result.stderr
    ), result.stderr


# Outputs stay inside the run


_ESCAPING = (
    _HEADER
    + """

def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(frame, inputs={}, outputs="../escaped.csv", name="escape")
    return p
"""
)
"""A definition whose python step 'escape' writes '../escaped.csv', outside the run."""


def test_run_exits_two_for_an_output_that_leaves_the_run(tmp_path: Path) -> None:
    """'dagger run --local' of a definition whose python step has output '../escaped.csv' exits 2, prints the step name and the outside message, and writes no file outside the run's output directory."""
    path = _write_definition(tmp_path, _ESCAPING)
    registry = FileRegistry(tmp_path / "out", "run1")
    with pytest.raises(WiringError) as outside:
        registry.register_output("../escaped.csv")

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"Step 'escape': {outside.value}" in result.stderr, result.stderr
    assert not registry.path_for("../escaped.csv").exists()
    assert not (tmp_path / "state").exists()


# A preview writes nothing under the state root


def test_dagger_preview_of_a_pipeline_that_has_run_changes_no_state_file(
    tmp_path: Path,
) -> None:
    """'dagger run --local' of a definition, then 'dagger preview' of it: every file under the pipeline's state root keeps its bytes and modification time."""
    path = _write_definition(tmp_path, _CHAIN)
    state = tmp_path / "state"
    ran = _invoke("run", str(path))
    assert ran.exit_code == 0, _describe(ran)
    age_files(state)
    before = snapshot_files(state)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    assert before, f"'dagger run' wrote nothing under {state}"
    assert changed_files(before, snapshot_files(state)) == []


# dagger show

_PYTHON_THEN_COMMAND = (
    _HEADER
    + """

def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(frame, inputs={}, outputs="population.parquet", name="population")
    p.add_command_step(
        "cp {source} {target}",
        inputs={"source": "population.parquet"},
        outputs={"target": "copy.parquet"},
        env=sys.prefix,
        name="copy",
    )
    return p
"""
)
"""A definition of the python step 'population', which calls ``frame`` and writes
'population.parquet', then the command step 'copy' with the env ``sys.prefix``, which copies
that file to 'copy.parquet'."""

_READS_A_ROOT_INPUT = (
    _HEADER
    + """

def pipeline():
    p = Pipeline(
        HERE / "out",
        "run1",
        root_inputs={"raw.parquet": HERE / "raw.parquet"},
        state=HERE / "state",
    )
    p.add_python_step(
        plus_one, inputs={"path": "raw.parquet"}, outputs="next.parquet", name="next"
    )
    return p
"""
)
"""A definition of the python step 'next', which reads the root input 'raw.parquet' beside the
definition under the name 'path'."""

_RUNS_A_NOTEBOOK = (
    _HEADER
    + """

def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_notebook_step(
        HERE / "summarize.ipynb",
        executed="executed/summarize.ipynb",
        env=sys.prefix,
        name="summarize",
    )
    return p
"""
)
"""A definition of the notebook step 'summarize', which runs summarize.ipynb beside the
definition under the env ``sys.prefix``."""

_SPLITS = (
    _HEADER
    + """

def split():
    return {"a": frame(), "b": frame()}


def pipeline():
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_python_step(frame, inputs={}, outputs="population.parquet", name="population")
    p.add_python_step(split, inputs={}, outputs={"a": "a.csv", "b": "b.csv"}, name="split")
    return p
"""
)
"""A definition of the single-output python step 'population' and the python step 'split',
whose outputs map 'a' to 'a.csv' and 'b' to 'b.csv'."""


def _blocks(result: Result) -> list[list[str]]:
    """Return the blocks ``dagger show`` wrote to standard output, each as its lines."""
    text = result.stdout.strip("\n")
    if not text:
        return []
    return [block.split("\n") for block in text.split("\n\n")]


def _block(result: Result, step: str) -> list[str]:
    """Return the one block ``dagger show`` wrote for ``step``."""
    found = [block for block in _blocks(result) if block[0].startswith(f"Step '{step}' (")]
    assert len(found) == 1, result.stdout
    return found[0]


# The 2-space indent, the 6-character label and one space.
_CONTINUED = " " * 9
"""The indent of each further line of a step's command, below its ``runs`` line."""


def _runs_line(block: list[str]) -> str:
    """Return the one ``runs`` line of ``block`` joined with its continuation lines."""
    starts = [index for index, line in enumerate(block) if line.startswith("  runs   ")]
    assert len(starts) == 1, block
    lines = [block[starts[0]]]
    for line in block[starts[0] + 1 :]:
        if not line.startswith(_CONTINUED):
            break
        lines.append(line)
    return "\n".join(lines)


def _run_output(directory: Path) -> Path:
    """Return the run's output directory for a definition in ``directory``."""
    return directory.resolve() / "out" / "run1"


def test_show_prints_each_step_in_definition_order_with_its_files_and_command(
    tmp_path: Path,
) -> None:
    """'dagger show -c def.py' of a definition with a python step, then a command step with an env that reads its output, exits 0 and prints one block per step in definition order: "Step '<name>' (python step)" with its output line and a "calls <module>:<qualname>" line, then "Step '<name>' (command step, env <env>)" with an input line naming the python step's key and absolute path, an output line, and a "runs" line holding the filled-in shell command with absolute paths; blocks are separated by one blank line."""
    path = _write_definition(tmp_path, _PYTHON_THEN_COMMAND)
    population = _run_output(tmp_path) / "population.parquet"
    copy = _run_output(tmp_path) / "copy.parquet"

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    assert "\n\n\n" not in result.stdout, result.stdout
    blocks = _blocks(result)
    assert len(blocks) == 2, result.stdout
    # A file loaded by load_pipeline is imported as a module named by its stem.
    assert blocks[0] == [
        "Step 'population' (python step)",
        f"  output 'population.parquet' at {population}",
        f"  calls  {path.stem}:frame",
    ], result.stdout
    assert blocks[1][:3] == [
        f"Step 'copy' (command step, env {sys.prefix})",
        f"  input  source: 'population.parquet' at {population}",
        f"  output target: 'copy.parquet' at {copy}",
    ], result.stdout
    assert blocks[1][3:4] and blocks[1][3].startswith("  runs   "), result.stdout
    runs = _runs_line(blocks[1])
    assert "cp " in runs, runs
    assert str(population) in runs, runs
    assert str(copy) in runs, runs


def test_show_labels_a_root_input_by_its_key_and_path(tmp_path: Path) -> None:
    """A step that reads a declared root input shows an input line "<name>: '<key>' at <absolute path>" for it."""
    raw = tmp_path.resolve() / "raw.parquet"
    raw.write_bytes(b"")
    path = _write_definition(tmp_path, _READS_A_ROOT_INPUT)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    assert f"  input  path: 'raw.parquet' at {raw}" in _block(result, "next"), result.stdout


def test_show_prints_a_notebook_step_with_its_source_executed_notebook_and_command(
    tmp_path: Path,
) -> None:
    """A notebook step's block reads "(notebook step, env <env>)" and has an input line "notebook: <source path>", an output line "executed: '<key>' at <path>", and a "runs" line holding its papermill command."""
    source = write_source_notebook(tmp_path / "summarize.ipynb").resolve()
    executed = _run_output(tmp_path) / "executed" / "summarize.ipynb"
    path = _write_definition(tmp_path, _RUNS_A_NOTEBOOK)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    block = _block(result, "summarize")
    assert block[0] == f"Step 'summarize' (notebook step, env {sys.prefix})", result.stdout
    assert f"  input  notebook: {source}" in block, result.stdout
    assert (
        f"  output executed: 'executed/summarize.ipynb' at {executed}" in block
    ), result.stdout
    runs = _runs_line(block)
    assert "papermill" in runs, runs
    assert str(source) in runs, runs
    assert str(executed) in runs, runs


def test_show_prints_a_simulation_step_with_its_run_directory_and_command(
    tmp_path: Path,
) -> None:
    """A simulation step's block has input lines "spec: <path>" and "branches: <path>", an output line "run directory '<key>' at <path>" with no name, and a "runs" line holding its psimulate command."""
    env = make_stub_env(tmp_path / "env")
    path = copy_simulation_example(tmp_path / "example", env)
    directory = path.parent.resolve()
    spec = directory / "toy_model.yaml"
    branches = directory / "branches.yaml"
    run_directory = directory / "output" / "example" / "sims" / "toy"

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    block = _block(result, "sims/toy")
    assert f"  input  spec: {spec}" in block, result.stdout
    assert f"  input  branches: {branches}" in block, result.stdout
    assert f"  output run directory 'sims/toy' at {run_directory}" in block, result.stdout
    runs = _runs_line(block)
    assert f"psimulate run -M {spec} -B {branches}" in runs, runs


def test_show_names_a_task_added_with_add_task(tmp_path: Path) -> None:
    """A task added with add_task is shown as "Step '<name>' (task added with add_task)" with its inputs and outputs."""
    path = _write_definition(tmp_path, _UNREGISTERED)
    source = tmp_path.resolve() / "source.txt"
    target = tmp_path.resolve() / "target.txt"

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    block = _block(result, "copy")
    assert block[0] == "Step 'copy' (task added with add_task)", result.stdout
    assert f"  input  source: {source}" in block, result.stdout
    assert f"  output target: {target}" in block, result.stdout


def test_show_leaves_out_the_env_of_a_step_without_one(tmp_path: Path) -> None:
    """The header of a step without an env (a python step) has no ", env" part."""
    path = _write_definition(tmp_path, _KEYED_CHAIN)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    assert _block(result, "a")[0] == "Step 'a' (python step)", result.stdout


def test_show_names_each_output_of_a_python_step_with_a_mapping(tmp_path: Path) -> None:
    """A python step with outputs={'a': 'a.csv', 'b': 'b.csv'} shows the output lines "a: 'a.csv' at <path>" and "b: 'b.csv' at <path>", while a single-output python step's output line has no name."""
    path = _write_definition(tmp_path, _SPLITS)
    out = _run_output(tmp_path)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    split = _block(result, "split")
    assert f"  output a: 'a.csv' at {out / 'a.csv'}" in split, result.stdout
    assert f"  output b: 'b.csv' at {out / 'b.csv'}" in split, result.stdout
    single = [line for line in _block(result, "population") if line.startswith("  output ")]
    assert single == [
        f"  output 'population.parquet' at {out / 'population.parquet'}"
    ], result.stdout


def test_show_prints_only_the_named_steps_in_definition_order(tmp_path: Path) -> None:
    """'dagger show -c def.py b a' of a definition adding a, b and c prints the blocks of a and then b, and no block for c."""
    path = _write_definition(tmp_path, _KEYED_CHAIN)

    result = _invoke("show", str(path), "b", "a")

    assert result.exit_code == 0, _describe(result)
    headers = [block[0] for block in _blocks(result)]
    assert headers == ["Step 'a' (python step)", "Step 'b' (python step)"], result.stdout


def test_show_selects_a_step_whose_name_has_a_slash(tmp_path: Path) -> None:
    """'dagger show -c def.py sims/toy' prints the block of the step named 'sims/toy'."""
    path = copy_simulation_example(tmp_path / "example", make_stub_env(tmp_path / "env"))

    result = _invoke("show", str(path), "sims/toy")

    assert result.exit_code == 0, _describe(result)
    blocks = _blocks(result)
    assert len(blocks) == 1, result.stdout
    assert blocks[0][0].startswith("Step 'sims/toy' ("), result.stdout


def test_show_of_an_unknown_step_exits_two_naming_the_valid_steps(tmp_path: Path) -> None:
    """'dagger show -c def.py nope' exits 2, its error output names 'nope' and the valid step names, and nothing is printed to standard output."""
    path = _write_definition(tmp_path, _KEYED_CHAIN)

    result = _invoke("show", str(path), "nope")

    assert result.exit_code == 2, _describe(result)
    assert "'nope'" in result.stderr, result.stderr
    assert "Valid steps: 'a', 'b', 'c'." in result.stderr, result.stderr
    assert result.stdout == "", result.stdout


def test_show_of_a_definition_that_raises_exits_two_as_preview_does(tmp_path: Path) -> None:
    """For a definition whose pipeline function raises, 'dagger show' exits 2 with the same error output as 'dagger preview' of that definition."""
    path = _write_definition(tmp_path, _RAISES["function"])
    previewed = _invoke("preview", str(path))
    assert previewed.exit_code == 2, _describe(previewed)

    result = _invoke("show", str(path))

    assert result.exit_code == 2, _describe(result)
    assert result.stderr == previewed.stderr


def test_show_writes_nothing(tmp_path: Path) -> None:
    """'dagger show' of a definition that never ran leaves its state root and outputs absent; after 'dagger run --local' of it, 'dagger show' leaves every file under the state root with the same bytes and modification time."""
    path = _write_definition(tmp_path, _KEYED_CHAIN)
    state = tmp_path / "state"

    fresh = _invoke("show", str(path))

    assert fresh.exit_code == 0, _describe(fresh)
    assert not state.exists()
    assert not any(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)

    ran = _invoke("run", str(path))
    assert ran.exit_code == 0, _describe(ran)
    age_files(state)
    before = snapshot_files(state)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    assert before, f"'dagger run' wrote nothing under {state}"
    assert changed_files(before, snapshot_files(state)) == []


_DOCS_PAGE = Path(__file__).parents[2] / "docs" / "source" / "python_pipelines.rst"
"""The docs page that shows a sample of 'dagger show' for the command step example."""

_DOCS_ROOT = "/home/user/model"
"""The folder the docs page shows the example's paths under."""

_CALLABLE_WITH_A_VALUE = (
    _HEADER
    + """
import pytask


def pipeline():
    task = Task(
        name="threshold",
        run=lambda threshold: None,
        inputs={"threshold": pytask.PythonNode(name="threshold_value", value=3)},
        outputs={"product": file_node(HERE / "made.txt")},
        resources={},
        env=None,
    )
    p = Pipeline(HERE / "out", "run1", state=HERE / "state")
    p.add_task(task)
    return p
"""
)
"""A definition of the hand-built task 'threshold', whose run is a lambda and whose input
'threshold' is a PythonNode named 'threshold_value'."""

_SIMULATION_WITH_AN_ARTIFACT = (
    _HEADER
    + """

def pipeline():
    p = Pipeline(
        HERE / "out",
        "run1",
        root_inputs={"artifact.hdf": HERE / "artifact.hdf"},
        state=HERE / "state",
    )
    p.add_simulation_step(
        spec=HERE / "model.yaml",
        branches=HERE / "branches.yaml",
        outputs="sims/toy",
        env=sys.prefix,
        options={"project": "proj_simscience"},
        artifact="artifact.hdf",
    )
    return p
"""
)
"""A definition of the simulation step 'sims/toy', given the root input 'artifact.hdf' as its
artifact, with model.yaml and branches.yaml beside the definition."""


def _show_sample() -> str:
    """Return the literal block after "adults`` prints::" on the docs page, dedented."""
    lines = _DOCS_PAGE.read_text().splitlines()
    starts = [index for index, line in enumerate(lines) if line.endswith("adults`` prints::")]
    assert len(starts) == 1, f"no single 'adults`` prints::' line in {_DOCS_PAGE}"
    sample: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        sample.append(line)
    return textwrap.dedent("\n".join(sample)).strip("\n")


def test_show_prints_the_docs_sample_for_the_command_example(tmp_path: Path) -> None:
    """'dagger show -c pipeline.py adults' on a copy of tests/frontend/command_example prints the sample on the docs page, with the sample's folder replaced by the copy's and its env by sys.prefix."""
    definition = _copy_command_example(tmp_path)
    sample = _show_sample()
    header = re.search(r"^Step 'adults' \(command step, env (.+)\)$", sample, re.MULTILINE)
    assert header, sample
    expected = sample.replace(header.group(1), sys.prefix).replace(
        _DOCS_ROOT, str(tmp_path.resolve())
    )

    result = _invoke("show", str(definition), "adults")

    assert result.exit_code == 0, _describe(result)
    assert result.stdout.strip("\n") == expected


def test_show_of_a_hand_built_task_with_a_callable_and_a_non_path_node(
    tmp_path: Path,
) -> None:
    """A task added with add_task whose run is a lambda and whose input is a PythonNode named 'threshold_value' shows a non-empty "calls" line and an input line ending with the node's name."""
    path = _write_definition(tmp_path, _CALLABLE_WITH_A_VALUE)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    block = _block(result, "threshold")
    calls = [line for line in block if line.startswith("  calls  ")]
    assert len(calls) == 1 and calls[0].removeprefix("  calls  ").strip(), result.stdout
    inputs = [line for line in block if line.startswith("  input  ")]
    assert any(line.endswith("threshold_value") for line in inputs), result.stdout


def test_show_names_a_simulations_artifact_input(tmp_path: Path) -> None:
    """A simulation step given artifact= the key of a root input shows an input line starting "  input  artifact: "."""
    write_spec(tmp_path / "model.yaml")
    write_branches(tmp_path / "branches.yaml")
    (tmp_path / "artifact.hdf").write_bytes(b"")
    path = _write_definition(tmp_path, _SIMULATION_WITH_AN_ARTIFACT)

    result = _invoke("show", str(path))

    assert result.exit_code == 0, _describe(result)
    block = _block(result, "sims/toy")
    assert any(line.startswith("  input  artifact: ") for line in block), result.stdout


# dagger graph (MIC-7765)

_PIPELINE_EXAMPLE = Path(__file__).parent / "pipeline_example"
"""The three-step pipeline example, whose definition the docs page includes."""

_GRAPH_TEXT_EXTENSIONS = (".mmd", ".md", ".dot", ".gv")
"""The extensions 'dagger graph' writes as text."""

_GRAPH_EXTENSIONS = (*_GRAPH_TEXT_EXTENSIONS, ".svg", ".png", ".pdf")
"""Every extension 'dagger graph' accepts."""


def _copy_pipeline_example(directory: Path) -> Path:
    """Copy the pipeline example's definition to ``directory``; return the copy."""
    return Path(shutil.copy(_PIPELINE_EXAMPLE / "pipeline.py", directory))


def _literal_block(text: str, intro: str) -> str:
    """Return the literal block after the one line of ``text`` ending ``intro``, dedented.

    The block ends at the first line that is not blank and not indented, and the text
    returned ends with one newline.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.rstrip().endswith(intro)]
    assert len(starts) == 1, f"no single line ending {intro!r}"
    block: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        block.append(line)
    return textwrap.dedent("\n".join(block)).strip("\n") + "\n"


def _graph_docs_sample() -> str:
    """Return the Mermaid sample on the docs page, after "``graph.mmd`` holds::"."""
    sample = _literal_block(_DOCS_PAGE.read_text(), "``graph.mmd`` holds::")
    assert sample.startswith("flowchart LR\n"), sample
    return sample


def _graph_dot_sample() -> str:
    """Return the DOT text that the frontend.drawing module docstring gives for the example."""
    module = importlib.import_module("vivarium.dagger.frontend.drawing")
    sample = _literal_block(module.__doc__ or "", "the DOT text is::")
    assert sample.startswith("digraph pipeline {\n"), sample
    return sample


def _without_bytecode(snapshot: Snapshot) -> Snapshot:
    """Return ``snapshot`` without the bytecode cache that importing a definition writes."""
    return {path: entry for path, entry in snapshot.items() if "__pycache__" not in path}


def _names_extension(text: str, extension: str) -> bool:
    """Return whether ``text`` holds ``extension`` not followed by a word character."""
    return re.search(rf"{re.escape(extension)}(?!\w)", text) is not None


def test_graph_writes_the_mermaid_file_of_the_docs_sample_for_the_pipeline_example(
    tmp_path: Path,
) -> None:
    """'dagger graph -c pipeline.py graph.mmd' on a copy of tests/frontend/pipeline_example exits 0, prints nothing, and writes graph.mmd whose text is exactly the sample under "Drawing a pipeline's graph" on docs/source/python_pipelines.rst, dedented, with a final newline."""
    definition = _copy_pipeline_example(tmp_path)
    target = tmp_path / "graph.mmd"

    result = _invoke("graph", str(definition), str(target))

    assert result.exit_code == 0, _describe(result)
    assert result.stdout == "", result.stdout
    assert result.stderr == "", result.stderr
    assert target.read_text() == _graph_docs_sample()


def test_graph_writes_dot_text_for_a_dot_file(tmp_path: Path) -> None:
    """'dagger graph -c pipeline.py graph.dot' on a copy of the pipeline example writes the DOT text that the frontend.graph module docstring gives for that example."""
    definition = _copy_pipeline_example(tmp_path)
    target = tmp_path / "graph.dot"

    result = _invoke("graph", str(definition), str(target))

    assert result.exit_code == 0, _describe(result)
    assert target.read_text() == _graph_dot_sample()


def test_graph_writes_nothing_but_its_file(tmp_path: Path) -> None:
    """'dagger graph' on a copy of the pipeline example, before and after a local run, changes no file under the example's folder (outputs and state) except the graph file it was asked for, and runs no step."""
    definition = _copy_pipeline_example(tmp_path)
    before = _without_bytecode(snapshot_files(tmp_path))

    fresh = _invoke("graph", str(definition), str(tmp_path / "before.mmd"))

    assert fresh.exit_code == 0, _describe(fresh)
    after = _without_bytecode(snapshot_files(tmp_path))
    assert changed_files(before, after) == ["before.mmd"]
    assert not (tmp_path / "output").exists()

    ran = _invoke("run", str(definition))
    assert ran.exit_code == 0, _describe(ran)
    assert _example_product(tmp_path, "summary.csv").exists()
    age_files(tmp_path)
    before = _without_bytecode(snapshot_files(tmp_path))

    result = _invoke("graph", str(definition), str(tmp_path / "output" / "after.mmd"))

    assert result.exit_code == 0, _describe(result)
    after = _without_bytecode(snapshot_files(tmp_path))
    assert changed_files(before, after) == ["output/after.mmd"]


def test_graph_refuses_an_unknown_extension_with_status_two(tmp_path: Path) -> None:
    """'dagger graph -c pipeline.py graph.txt' exits 2, its standard error names '.txt' and each of the formats that work (.mmd, .md, .dot, .gv, .svg, .png, .pdf), and graph.txt is not written."""
    definition = _copy_pipeline_example(tmp_path)
    target = tmp_path / "graph.txt"

    result = _invoke("graph", str(definition), str(target))

    assert result.exit_code == 2, _describe(result)
    assert _names_extension(result.stderr, ".txt"), result.stderr
    for extension in _GRAPH_EXTENSIONS:
        assert _names_extension(result.stderr, extension), (extension, result.stderr)
    assert not target.exists()


def test_graph_refuses_an_image_without_dot_with_status_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With PATH holding no 'dot' program, 'dagger graph -c pipeline.py graph.svg' exits 2, its standard error names '.svg', says it needs Graphviz's dot program, and names the text formats, and graph.svg is not written."""
    definition = _copy_pipeline_example(tmp_path)
    target = tmp_path / "graph.svg"
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = _invoke("graph", str(definition), str(target))

    assert result.exit_code == 2, _describe(result)
    errors = result.stderr
    assert _names_extension(errors, ".svg"), errors
    assert "Graphviz" in errors, errors
    assert re.search(r"\bdot\b", errors.replace(".dot", "")), errors
    for extension in _GRAPH_TEXT_EXTENSIONS:
        assert _names_extension(errors, extension), (extension, errors)
    assert not target.exists()


def test_graph_reports_a_failing_dot_with_status_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a 'dot' program first on PATH that writes 'boom' to standard error and exits 3, 'dagger graph -c pipeline.py graph.png' exits 1 and its standard error has 'boom' and the exit status 3."""
    install_fake_dot(tmp_path / "fake", monkeypatch, status=3, stderr="boom\n")
    definition = _copy_pipeline_example(tmp_path)

    result = _invoke("graph", str(definition), str(tmp_path / "graph.png"))

    assert result.exit_code == 1, _describe(result)
    assert "boom" in result.stderr, result.stderr
    # A pytest folder such as 'pytest-3' must not stand in for the status.
    errors = result.stderr.replace(str(tmp_path.resolve()), "").replace(str(tmp_path), "")
    assert re.search(r"\b3\b", errors), result.stderr


def test_graph_into_a_missing_folder_exits_one_naming_the_file(tmp_path: Path) -> None:
    """'dagger graph -c pipeline.py missing/graph.mmd', where the folder 'missing' does not exist, exits 1 with 'Could not write' and the file's path on standard error, and creates no folder."""
    definition = _copy_pipeline_example(tmp_path)
    target = tmp_path / "missing" / "graph.mmd"

    result = _invoke("graph", str(definition), str(target))

    assert result.exit_code == 1, _describe(result)
    assert f"Could not write {target}: " in result.stderr, result.stderr
    assert not (tmp_path / "missing").exists()


def test_graph_of_a_definition_that_raises_exits_two_as_show_does(tmp_path: Path) -> None:
    """'dagger graph' on a definition whose pipeline() raises exits 2 with the same message as 'dagger show' on it, and writes no graph file."""
    path = _write_definition(tmp_path, _RAISES["function"])
    shown = _invoke("show", str(path))
    assert shown.exit_code == 2, _describe(shown)
    target = tmp_path / "graph.mmd"

    result = _invoke("graph", str(path), str(target))

    assert result.exit_code == 2, _describe(result)
    assert result.stderr == shown.stderr
    assert not target.exists()
