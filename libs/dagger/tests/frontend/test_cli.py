"""Tests for the ``dagger`` command (``frontend.cli``)."""

import ast
import importlib.metadata
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from tests.frontend.notebooks import (
    broken_env,
    isolated_jupyter_dirs,
    recorded_errors,
    write_source_notebook,
)
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.cli import dagger

_HEADER = """\
import dataclasses
import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.frontend import Pipeline, handoff_inputs, python_step
from vivarium.dagger.harness import stable_code_id

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
        target, inputs=inputs, produces=f"{name}.parquet", registry=registry, name=name
    )


def stable(tasks):
    return [dataclasses.replace(t, code_id=stable_code_id(f"{t.name}::code")) for t in tasks]
"""
"""The start of every definition. ``log`` records that a step body ran, ``step`` wires step
'<name>' to produce '<name>.parquet' from its sources' products, and ``stable`` gives each
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(make_b, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
    ]
    return Pipeline(stable(tasks), HERE / "state", registry)
"""
)
"""A definition of the chain a -> b -> c with stable code identities."""

_CHAIN_STEPS = ("a", "b", "c")

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
    registry = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        step(make_p, "p", registry),
        step(make_x, "x", registry, source="p"),
        step(make_w, "w", registry, source="x"),
        step(make_q, "q", registry),
        step(make_y, "y", registry, source="q"),
        step(make_z, "z", registry),
    ]
    return Pipeline(stable(tasks), HERE / "state", registry)
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(explode_in_user_code, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
        step(make_d, "d", registry, source="c"),
        step(make_side, "side", registry),
    ]
    return Pipeline(stable(tasks), HERE / "state", registry)
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
    return Pipeline([task], HERE / "state")
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(stop, "stop", registry)], HERE / "state", registry)
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(leave, "leave", registry)], HERE / "state", registry)
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
    return Pipeline([task], HERE / "state")
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
    reg = ArtifactRegistry(HERE / "out", "run1")
    a = python_step(make_a, inputs={}, produces="population.parquet", registry=reg)
    b = python_step(make_b, inputs={"x": "populaton.parquet"}, produces="b.csv", registry=reg)
    return Pipeline([a, b], HERE / "state", reg)
"""
)
"""A definition whose step 'b.csv' reads a mistyped key, 'populaton.parquet'."""

_DOUBLE_CLAIM = """

def make_shared():
    log("shared")
    return frame()


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    claim = {"inputs": {}, "produces": "shared.parquet", "registry": registry}
    tasks = [
        python_step(make_shared, name=FIRST, **claim),
        python_step(make_shared, name=SECOND, **claim),
    ]
    return Pipeline(tasks, HERE / "state", registry)
"""
"""The end of a definition whose steps FIRST and SECOND both produce 'shared.parquet'."""

_CLAIMED = "Artifact 'shared.parquet' is produced by more than one step"
"""The start of dagger's message for the artifact that both _DOUBLE_CLAIM steps produce."""

_HANDOFF_NOT_READY = (
    _HEADER
    + """

def pipeline():
    inputs = handoff_inputs(HERE / "handoff", ["ethiopia"], {"coverage.csv": ["coverage"]})
    registry = ArtifactRegistry(HERE / "out", "run1", root_inputs=inputs)
    return Pipeline([], HERE / "state", registry)
"""
)
"""A definition whose handoff check fails because ethiopia's coverage.csv is missing."""

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
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(make_a, "a", registry)], Path("state"), registry)
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
    return Pipeline([task], HERE / "state")
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        step(make_a, "a", registry),
        step(make_b, "b", registry, source="a"),
        step(make_c, "c", registry, source="b"),
    ]
    return Pipeline(stable(tasks), HERE / "state", registry)
"""
)
"""The chain a -> b -> c where b raises until ``_BROKEN`` is replaced by ``_FIXED``."""

_PASSES = (
    _HEADER
    + """

def make_a():
    return frame()


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(make_a, "a", registry)], HERE / "state", registry)
"""
)
"""A definition of one step that succeeds."""

_FAILS = (
    _HEADER
    + """

def make_a():
    raise ValueError("the step broke")


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(make_a, "a", registry)], HERE / "state", registry)
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
    return Pipeline([task], HERE / "state")
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
    return Pipeline([touch(name, node) for name, node in STEPS], HERE / "state")
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
    return Pipeline([copy_step("a", y, x), copy_step("b", x, y)], HERE / "state")
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    root = HERE / "blocker" / "state"
    return Pipeline([step(make_a, "a", registry)], root, registry)
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
    registry = ArtifactRegistry(HERE / "out", "run1")
    return Pipeline([step(make_a, "a", registry)], HERE.joinpath(*ROOT), registry)
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


def _calls(directory: Path) -> list[str]:
    """Return the steps whose bodies ran for a definition in ``directory``, in call order."""
    log = directory / "calls.log"
    return log.read_text().split() if log.exists() else []


def _product(directory: Path, step: str) -> Path:
    """Return the path of ``step``'s product for a definition in ``directory``."""
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


def test_dagger_help_lists_run_preview_force_and_restart() -> None:
    """'dagger --help' exits 0 and lists the subcommands run, preview, force and restart, and no others."""
    result = _dagger("--help")

    assert result.exit_code == 0, _describe(result)
    _, _, commands = result.output.partition("Commands:")
    listed = set(re.findall(r"^  (\S+)", commands, flags=re.MULTILINE))
    assert listed == {"run", "preview", "force", "restart"}


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


def test_run_exits_zero_and_writes_every_product(tmp_path: Path) -> None:
    """'dagger run -c def.py' on a three-step chain exits 0 and every step's product exists."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)


def test_run_until_runs_the_named_step_and_its_ancestors_only(tmp_path: Path) -> None:
    """'dagger run -c def.py --until b' on the chain a -> b -> c exits 0, runs a and b, and leaves c's product unwritten."""
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


def test_an_interrupted_run_exits_130_and_says_rerunning_continues(tmp_path: Path) -> None:
    """A step that raises KeyboardInterrupt makes 'dagger run' exit 130 and print that the run was interrupted and that running the same command again continues it."""
    path = _write_definition(tmp_path, _INTERRUPTED)

    result = _invoke("run", str(path))

    assert result.exit_code == 130, _describe(result)
    assert "Interrupted" in result.stderr
    assert "run the same command again to continue" in result.stderr.lower()


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


def test_preview_prints_one_line_per_step_in_artifact_key_terms(tmp_path: Path) -> None:
    """'dagger preview -c def.py' exits 0 and prints one 'run <step>: <reasons>' line per stale step, naming artifacts by key and code as "code of '<step>'", with no absolute path, then an '<n> of <m> steps would run.' line."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    lines = _plan(result)
    assert len(lines) == 4, result.output
    assert _would_run(result) == ["a", "b", "c"]
    assert lines[0].startswith("run  a: ")
    assert "code of 'a'" in lines[0]
    assert "artifact 'a.parquet'" in lines[0]
    assert lines[3] == "3 of 3 steps would run."
    assert str(tmp_path) not in result.output
    assert str(tmp_path.resolve()) not in result.output


def test_preview_writes_no_product_and_prints_no_pytask_output(tmp_path: Path) -> None:
    """After 'dagger preview', no product exists and the output contains none of pytask's own output, such as its 'Start pytask session' banner or its table."""
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
    """An unknown step name given to run --until, preview --until or force exits 2 with click's usage error for '--until' or 'STEP...' carrying the verb's message, which lists every valid step name, and no pytask state or product is written."""
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


def test_a_double_claimed_artifact_exits_two_naming_the_key_and_both_steps(
    tmp_path: Path,
) -> None:
    """Two python_steps with different names and the same produces key exit 2 with a message naming the artifact key and both step names, before any build."""
    path = _write_definition(tmp_path, _double_claim("first", "second"))

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"{_CLAIMED}: 'first', 'second'." in result.stderr
    assert not (tmp_path / "state").exists()
    assert _calls(tmp_path) == []


def test_the_same_produces_key_with_default_names_is_reported_as_a_double_claim(
    tmp_path: Path,
) -> None:
    """Two python_steps with the same produces key and default names exit 2 with the double-claim message naming the key, not the backend's duplicate-task-name message."""
    path = _write_definition(tmp_path, _double_claim(None, None))

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert _CLAIMED in result.stderr
    assert "Task names must be unique" not in result.output


def test_a_handoff_error_exits_two_and_prints_only_its_message(tmp_path: Path) -> None:
    """A definition whose handoff_inputs check fails exits 2 and prints the HandoffError message with no traceback."""
    path = _write_definition(tmp_path, _HANDOFF_NOT_READY)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "ethiopia: coverage.csv is missing" in result.stderr
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
    """A definition that returns Pipeline(tasks, Path('state')) exits 2 with a message saying the root must be absolute."""
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
    """Each '$ dagger ...' line in docs/source/python_pipelines.rst, run in order on a copy of tests/frontend/pipeline_example/pipeline.py in tmp_path, exits 0, and the page shows at least one example of run, preview and force."""
    page = Path(__file__).parents[2] / "docs" / "source" / "python_pipelines.rst"
    prompt = "$ dagger "
    commands = [
        line.strip().removeprefix(prompt)
        for line in page.read_text().splitlines()
        if line.strip().startswith(prompt)
    ]
    shutil.copy(Path(__file__).parent / "pipeline_example" / "pipeline.py", tmp_path)

    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        for command in commands:
            result = _dagger(*shlex.split(command))
            assert result.exit_code == 0, f"$ dagger {command}\n{_describe(result)}"

    assert {"run", "preview", "force"} <= {shlex.split(command)[0] for command in commands}


# Review fixes (round 2)


@pytest.mark.parametrize("command", ["run", "preview", "force"])
def test_an_interrupt_while_loading_exits_130_with_the_verbs_message(
    tmp_path: Path, command: str
) -> None:
    """A definition that raises KeyboardInterrupt at import exits 130 under run, preview and force (with a STEP), with no traceback; only run and force add that running the same command again continues."""
    path = _write_definition(tmp_path, _INTERRUPTED_WHILE_LOADING)
    steps: list[str] = ["a"] if command == "force" else []

    result = _invoke(command, str(path), *steps)

    assert result.exit_code == 130, _describe(result)
    assert result.stderr.startswith("Interrupted."), result.stderr
    assert "Traceback" not in result.stderr
    continues = "run the same command again to continue" in result.stderr.lower()
    assert continues == (command != "preview"), result.stderr


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
    """Two hand-built command steps whose outputs are a file_node and a PathNode named 'custom' on the same file exit 2 with dagger's double-claim message naming both steps."""
    definition = _touches(
        ("one", 'file_node(HERE / "same.txt")'),
        ("two", 'pytask.PathNode(name="custom", path=HERE / "same.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    claimed = "is produced by more than one step: 'one', 'two'."
    assert claimed in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "same.txt").exists()


def test_a_double_claim_without_a_registry_names_the_node(tmp_path: Path) -> None:
    """Two hand-built command steps 'one' and 'two' that both output file_node(<tmp>/same.txt), with no registry, exit 2 with "Artifact '<that node's name>' is produced by more than one step: 'one', 'two'."."""
    definition = _touches(
        ("one", 'file_node(HERE / "same.txt")'),
        ("two", 'file_node(HERE / "same.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    key = file_node(tmp_path / "same.txt").name
    claimed = f"Artifact '{key}' is produced by more than one step: 'one', 'two'."
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
    """When pytask cannot write its lockfile at the end of a build (a directory already sits at <root>/pytask.lock.tmp), 'dagger run' exits 1, not 2, the step's product exists, and stderr has 'Could not read or write the pipeline's state under <root>: ' with no traceback."""
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
    """Two hand-built command steps whose outputs are unnamed PathNodes on HERE / 'sub' / '..' / 'x.txt' and HERE / 'x.txt' (with HERE / 'sub' existing) exit 2 with dagger's double-claim message naming both steps."""
    (tmp_path / "sub").mkdir()
    definition = _touches(
        ("one", 'pytask.PathNode(path=HERE / "sub" / ".." / "x.txt")'),
        ("two", 'pytask.PathNode(path=HERE / "x.txt")'),
    )
    path = _write_definition(tmp_path, definition)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    claimed = "is produced by more than one step: 'one', 'two'."
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


@pytest.mark.parametrize("verb, steps", [("run", []), ("force", ["a"])])
def test_a_definition_without_local_exits_two_and_runs_nothing(
    tmp_path: Path, verb: str, steps: list[str]
) -> None:
    """'dagger run' and 'dagger force' on a pipeline definition without --local exit 2 with a usage error saying Jobmon runs are not available yet, and run no step."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _dagger(verb, "-c", str(path), *steps)

    assert result.exit_code == 2, _describe(result)
    assert "through Jobmon is not available yet" in result.stderr
    assert "--local" in result.stderr
    assert _calls(tmp_path) == []


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


@pytest.mark.parametrize(
    "option", [["-P", "proj_x"], ["--queue", "all.q"], ["-o", "out"], ["-v"], ["--pdb"]]
)
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

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, notebook_step, python_step

HERE = Path(__file__).resolve().parent
ENV = __ENV__


def read_summary(summary):
    return pd.read_csv(summary)


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    summarize = notebook_step(
        HERE / "summarize.ipynb",
        executed="executed/summarize.ipynb",
        registry=registry,
        env=ENV,
        produces={"summary_path": "summary.csv"},
        name="summarize",
    )
    report = python_step(
        read_summary,
        inputs={"summary": "summary.csv"},
        produces="report.parquet",
        registry=registry,
        name="report",
    )
    return Pipeline([summarize, report], HERE / "state", registry)
"""
"""A definition of the notebook step 'summarize', which runs summarize.ipynb next to the
definition under ENV and writes 'summary.csv', and the python step 'report' that reads it."""

_RAISES_KEY_ERROR = "{}['no_such_column']"
"""A notebook body that raises KeyError('no_such_column')."""

_WRITES_SUMMARY = "from pathlib import Path\nPath(summary_path).write_text('n\\n1\\n')"
"""A notebook body that writes the summary product."""


@pytest.fixture
def jupyter_dirs(tmp_path: Path) -> Iterator[None]:
    """Point IPython and Jupyter at empty directories, so no kernel reads or writes HOME."""
    with isolated_jupyter_dirs(tmp_path / "jupyter"):
        yield


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
    """A python step that reads the failing notebook's product is listed on stderr as not run because an upstream step failed."""
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
    """Use case: after 'dagger run' reports a notebook's KeyError, editing that notebook to stop raising and running the same command again exits 0, and the notebook step and its downstream step both run and write their products."""
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
from vivarium.dagger.frontend import notebook_step

NOTEBOOK = HERE / "summarize.ipynb"
ENV = sys.prefix
INPUTS = {"population_path": "populaton.parquet"}


def pipeline():
    reg = ArtifactRegistry(HERE / "out", "run1")
    a = step(frame, "population", reg)
    nb = notebook_step(NOTEBOOK, executed="run.ipynb", registry=reg, env=ENV, inputs=INPUTS)
    return Pipeline([a, nb], HERE / "state", reg)
"""
)
"""A definition whose notebook step, named by default 'run.ipynb', reads a mistyped key,
'populaton.parquet'."""

_UNLIKE_EVERY_KEY = (
    _HEADER
    + """

BAD = {"path": "zzz"}


def pipeline():
    reg = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        step(frame, "population", reg),
        step(plus_one, "adults", reg, path="population"),
        python_step(plus_one, inputs=BAD, produces="s.csv", registry=reg, name="s"),
    ]
    return Pipeline(tasks, HERE / "state", reg)
"""
)
"""A definition whose step 's' reads the key 'zzz', which is unlike every registered key."""

_SHARED_NAME = (
    _HEADER
    + """

NAME = "population"


def pipeline():
    reg = ArtifactRegistry(HERE / "out", "run1")
    tasks = [
        python_step(frame, inputs={}, produces="a.parquet", registry=reg, name=NAME),
        python_step(frame, inputs={}, produces="b.parquet", registry=reg, name=NAME),
    ]
    return Pipeline(tasks, HERE / "state", reg)
"""
)
"""A definition of two steps with different products that are both named 'population'."""


def test_a_step_that_calls_sys_exit_is_named_in_the_message(tmp_path: Path) -> None:
    """A step 'leave' that calls sys.exit(0) makes 'dagger run' exit 1 with "Step 'leave' exited with code 0 before the build finished." on stderr."""
    path = _write_definition(tmp_path, _EXITS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    expected = "Step 'leave' exited with code 0 before the build finished."
    assert expected in result.stderr, result.stderr


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
    """Two steps with different products and the same name make 'dagger run' and 'dagger preview' exit 2 with "Two steps are named 'population'; pass name= to python_step or notebook_step to tell them apart." on stderr, no "Task names must be unique", no "['", and nothing run."""
    path = _write_definition(tmp_path, _SHARED_NAME)
    expected = (
        "Two steps are named 'population'; pass name= to python_step or notebook_step to "
        "tell them apart."
    )

    for verb in ("run", "preview"):
        result = _invoke(verb, str(path))

        assert result.exit_code == 2, f"{verb}\n{_describe(result)}"
        assert expected in result.stderr, result.stderr
        assert "Task names must be unique" not in result.output, result.output
        assert "['" not in result.output, result.output
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "command",
    ["run --until", "preview --until", "force"],
    ids=["run", "preview", "force"],
)
def test_two_steps_with_one_name_and_an_unknown_step_report_the_shared_name_as_a_definition_problem(
    tmp_path: Path, command: str
) -> None:
    """With two steps named 'population' and an unknown step 'bb' given to --until or force, the command exits 2 with "Error: Two steps are named 'population'; ..." and no usage line or "Invalid value"."""
    path = _write_definition(tmp_path, _SHARED_NAME)
    verb, *option = command.split()

    result = _invoke(verb, str(path), *option, "bb")

    assert result.exit_code == 2, _describe(result)
    expected = (
        "Error: Two steps are named 'population'; pass name= to python_step or "
        "notebook_step to tell them apart."
    )
    assert expected in result.stderr, result.stderr
    assert "Usage:" not in result.stderr, result.stderr
    assert "Invalid value" not in result.stderr, result.stderr
    assert not (tmp_path / "state").exists()


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


def test_the_example_definitions_import_the_registry_from_the_frontend() -> None:
    """tests/frontend/pipeline_example/pipeline.py imports nothing from vivarium.dagger.backend, and neither example definition imports from vivarium.dagger.backend.wiring."""
    here = Path(__file__).parent
    examples = {
        name: _imported_modules(here / name / "pipeline.py")
        for name in ("pipeline_example", "handoff_example")
    }

    backend = [
        module
        for module in examples["pipeline_example"]
        if f"{module}.".startswith("vivarium.dagger.backend.")
    ]
    assert backend == []
    for name, imported in examples.items():
        assert "vivarium.dagger.backend.wiring" not in imported, name
        assert "ArtifactRegistry" in imported.get("vivarium.dagger.frontend", set()), name
