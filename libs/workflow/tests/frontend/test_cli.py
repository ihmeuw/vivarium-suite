"""Tests for the ``vwf`` command (``frontend.cli``)."""

import importlib.metadata
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

from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.frontend.cli import vwf

_HEADER = """\
import dataclasses
import sys
from pathlib import Path

import pandas as pd

from vivarium.workflow.backend.nodes import file_node
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend import Pipeline, handoff_inputs, python_step
from vivarium.workflow.harness import stable_code_id

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
"""The start of vwf's message for the artifact that both _DOUBLE_CLAIM steps produce."""

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

_THEN_NEXT = '"$1" run "$2" && touch "$3"'
"""A shell list that runs ``vwf run`` and, only if it succeeds, a next command."""

_RUN_LINE = re.compile(r"run  ([^:\s]+)(: .+)?")
"""A preview line for a step that would run."""

_PLAN_LINE = re.compile(r"run  \S+(: .+)?|skip \S+: .+|\d+ of \d+ steps? would run\.")
"""Any line ``vwf preview`` prints for a successful plan."""

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


def _invoke(*args: str) -> Result:
    """Run ``vwf`` with ``args`` in this process."""
    return CliRunner().invoke(vwf, list(args))


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
    """Return the non-blank lines ``vwf preview`` wrote to standard output."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def _would_run(result: Result) -> list[str]:
    """Return the steps ``vwf preview`` printed as would-run, in its order."""
    matches = (_RUN_LINE.fullmatch(line) for line in _plan(result))
    return [match.group(1) for match in matches if match]


def _stopped(code: str) -> str:
    """Return vwf's line for a build that pytask stopped with the exit code named ``code``."""
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


def test_vwf_help_lists_run_preview_and_force() -> None:
    """'vwf --help' exits 0 and lists the subcommands run, preview and force, and no others."""
    result = _invoke("--help")

    assert result.exit_code == 0, _describe(result)
    _, _, commands = result.output.partition("Commands:")
    listed = set(re.findall(r"^  (\S+)", commands, flags=re.MULTILINE))
    assert listed == {"run", "preview", "force"}


def test_the_vwf_console_script_resolves_to_the_click_group() -> None:
    """The console_scripts entry point named 'vwf' is declared and loads vivarium.workflow.frontend.cli.vwf."""
    [entry_point] = importlib.metadata.entry_points(group="console_scripts", name="vwf")

    assert entry_point.value == "vivarium.workflow.frontend.cli:vwf"
    assert entry_point.load() is vwf


def test_the_installed_vwf_script_exit_status_can_gate_a_shell_chain(tmp_path: Path) -> None:
    """The installed vwf script, run in a subprocess, exits 0 for a definition that succeeds and 1 for one whose step fails, so 'vwf run def.py && next' runs next only on success."""
    script = Path(sys.executable).parent / "vwf"
    assert script.is_file(), f"No vwf console script next to {sys.executable}."

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


# vwf run


def test_run_exits_zero_and_writes_every_product(tmp_path: Path) -> None:
    """'vwf run def.py' on a three-step chain exits 0 and every step's product exists."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", str(path))

    assert result.exit_code == 0, _describe(result)
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)


def test_run_until_runs_the_named_step_and_its_ancestors_only(tmp_path: Path) -> None:
    """'vwf run def.py --until b' on the chain a -> b -> c exits 0, runs a and b, and leaves c's product unwritten."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("run", str(path), "--until", "b")

    assert result.exit_code == 0, _describe(result)
    assert _calls(tmp_path) == ["a", "b"]
    assert _product(tmp_path, "b").exists()
    assert not _product(tmp_path, "c").exists()


def test_run_after_run_until_continues_with_the_remaining_steps(tmp_path: Path) -> None:
    """With stable code identities, 'vwf run --until b' then 'vwf run' executes only c the second time; a and b are reported unchanged and their bodies are not called again."""
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
    """A python_step whose function raises ValueError makes 'vwf run' exit 1; the output contains the user's frame and 'ValueError: <message>' exactly once, and no build.py or python_step.py frame."""
    path = _write_definition(tmp_path, _FAILING)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "explode_in_user_code" in result.output
    assert "ValueError: the step broke" in result.output
    # vwf's summary repeats the error line by design, so the raise statement, which only a
    # traceback shows, is what proves that the traceback appears once.
    assert result.output.count('raise ValueError("the step broke")') == 1, result.output
    assert "build.py" not in result.output
    assert "python_step.py" not in result.output


def test_the_failure_summary_names_the_failed_step_and_the_steps_not_run(
    tmp_path: Path,
) -> None:
    """After a step fails, vwf's own output names the failed step with its error type and message, and names its downstream steps as not run because an upstream step failed; an independent step still runs."""
    path = _write_definition(tmp_path, _FAILING)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "Step 'b' failed: ValueError: the step broke" in result.stderr
    assert "Not run because an upstream step failed: 'c', 'd'." in result.stderr
    assert "side" in _calls(tmp_path)
    assert _product(tmp_path, "side").exists()


def test_a_failing_command_step_exits_one_and_names_the_called_process_error(
    tmp_path: Path,
) -> None:
    """A command step that exits non-zero makes 'vwf run' exit 1 and vwf's summary names the step and CalledProcessError."""
    path = _write_definition(tmp_path, _COMMAND_FAILS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert "Step 'shout' failed: CalledProcessError" in result.stderr
    assert "exit 3" in result.stderr
    assert "Not run because" not in result.stderr


def test_an_interrupted_run_exits_130_and_says_rerunning_continues(tmp_path: Path) -> None:
    """A step that raises KeyboardInterrupt makes 'vwf run' exit 130 and print that the run was interrupted and that running the same command again continues it."""
    path = _write_definition(tmp_path, _INTERRUPTED)

    result = _invoke("run", str(path))

    assert result.exit_code == 130, _describe(result)
    assert "Interrupted" in result.stderr
    assert "run the same command again to continue" in result.stderr.lower()


def test_a_step_that_raises_system_exit_makes_vwf_exit_nonzero(tmp_path: Path) -> None:
    """A step that calls sys.exit(0) stops the build, and 'vwf run' exits 1 with a message saying a step exited, not 0."""
    path = _write_definition(tmp_path, _EXITS)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert re.search(r"(?i)\bexited\b", result.stderr), result.stderr
    assert re.search(r"\b0\b", result.stderr), result.stderr


def test_pytask_stopping_without_a_recorded_error_exits_one_with_a_readable_message(
    tmp_path: Path,
) -> None:
    """When pytask stops before any step runs and records no error (a malformed pytask.toml under the root), 'vwf run' exits 1 with a message that names pytask's exit code and is more than the bare code name."""
    path = _write_definition(tmp_path, _CHAIN)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "pytask.toml").write_text("[[[ not toml\n")

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    assert _stopped("CONFIGURATION_FAILED") in result.stderr, result.stderr
    assert _calls(tmp_path) == []


# vwf preview


def test_preview_prints_one_line_per_step_in_artifact_key_terms(tmp_path: Path) -> None:
    """'vwf preview def.py' exits 0 and prints one 'run <step>: <reasons>' line per stale step, naming artifacts by key and code as "code of '<step>'", with no absolute path, then an '<n> of <m> steps would run.' line."""
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
    """After 'vwf preview', no product exists and the output contains none of pytask's own output, such as its 'Start pytask session' banner or its table."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("preview", str(path))

    assert result.exit_code == 0, _describe(result)
    assert not any(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)
    assert _calls(tmp_path) == []
    assert "Start pytask session" not in result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert all(_PLAN_LINE.fullmatch(line) for line in lines), result.output


def test_preview_until_reports_the_other_steps_as_not_selected(tmp_path: Path) -> None:
    """'vwf preview def.py --until a' prints 'skip <step>: not selected' for each step outside a's ancestors."""
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
    """With stable code identities and after a full run, 'vwf preview' prints 'skip <step>: unchanged' for every step and '0 of <m> steps would run.'."""
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


# vwf force


def test_force_reruns_a_current_step(tmp_path: Path) -> None:
    """With stable code identities, 'vwf force def.py b' after a full run executes b again and exits 0."""
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
    """'vwf force def.py a' on a fresh root runs a (and every other stale step) and exits 0."""
    path = _write_definition(tmp_path, _CHAIN)

    result = _invoke("force", str(path), "a")

    assert result.exit_code == 0, _describe(result)
    assert _calls(tmp_path) == ["a", "b", "c"]
    assert all(_product(tmp_path, step).exists() for step in _CHAIN_STEPS)


def test_force_without_a_step_is_a_usage_error(tmp_path: Path) -> None:
    """'vwf force def.py' with no STEP exits 2 with click's usage message."""
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
    assert result.stderr.startswith(f"Usage: vwf {verb} "), result.stderr
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
    """A spec naming a library function that needs an argument (vivarium.workflow.harness:build_fixture) fails with a TypeError raised only in framework frames; vwf exits 2 and prints the traceback with those frames instead of an empty one."""
    result = _invoke("run", "vivarium.workflow.harness:build_fixture")

    assert result.exit_code == 2, _describe(result)
    assert "Traceback (most recent call last):" in result.stderr
    frames = [line for line in result.stderr.splitlines() if line.startswith('  File "')]
    assert frames, result.stderr
    assert any("vivarium/workflow/" in line for line in frames), result.stderr
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
    """'vwf run' exits 1 for a raising step; after the definition file is edited to fix it, 'vwf run' in the same process exits 0 and the failed step and its descendants run."""
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
    """Each '$ vwf ...' line in docs/source/vwf.rst, run in order on a copy of tests/frontend/vwf_example/pipeline.py in tmp_path, exits 0, and the page shows at least one example of run, preview and force."""
    page = Path(__file__).parents[2] / "docs" / "source" / "vwf.rst"
    prompt = "$ vwf "
    commands = [
        line.strip().removeprefix(prompt)
        for line in page.read_text().splitlines()
        if line.strip().startswith(prompt)
    ]
    shutil.copy(Path(__file__).parent / "vwf_example" / "pipeline.py", tmp_path)

    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        for command in commands:
            result = _invoke(*shlex.split(command))
            assert result.exit_code == 0, f"$ vwf {command}\n{_describe(result)}"

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
    """A pipeline() that calls sys.exit(0) makes 'vwf run' exit 2, not 0, with a trimmed traceback ending in 'SystemExit: 0' and nothing run."""
    path = _write_definition(tmp_path, _EXITS_WHILE_LOADING)

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert "Traceback (most recent call last):" in result.stderr
    assert "SystemExit: 0" in result.stderr
    assert not (tmp_path / "state").exists()
    assert _calls(tmp_path) == []


def test_a_root_that_cannot_be_created_exits_two_naming_the_root(tmp_path: Path) -> None:
    """A pipeline whose root is an existing file makes 'vwf run' exit 2 with 'Error: Cannot use the pipeline root <root>: ...' on stderr and no traceback."""
    path = _write_definition(tmp_path, _CHAIN)
    root = tmp_path.resolve() / "state"
    root.write_text("a file where the root directory should be\n")

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"Error: Cannot use the pipeline root {root}: " in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert _calls(tmp_path) == []


def test_preview_shows_pytasks_output_when_pytask_stops_the_plan(tmp_path: Path) -> None:
    """With a malformed pytask.toml under the root, 'vwf preview' exits 1, prints no plan on stdout, and writes pytask's TOMLDecodeError output to stderr before vwf's line naming CONFIGURATION_FAILED."""
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
    """A hand-built command step whose input file does not exist makes 'vwf preview' exit 1, with pytask's report naming the missing file on stderr before vwf's line naming FAILED."""
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
    """Two hand-built command steps whose outputs are a file_node and a PathNode named 'custom' on the same file exit 2 with vwf's double-claim message naming both steps."""
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
    """A pipeline whose root is <tmp>/blocker/state, where blocker is an existing file, makes 'vwf run' exit 2 with 'Error: Cannot use the pipeline root <root>: ' on stderr, no traceback, and nothing created."""
    path = _write_definition(tmp_path, _ROOT_UNDER_A_FILE)
    blocker = tmp_path / "blocker"
    content = "a file where a directory above the root should be\n"
    blocker.write_text(content)
    root = tmp_path.resolve() / "blocker" / "state"

    result = _invoke("run", str(path))

    assert result.exit_code == 2, _describe(result)
    assert f"Error: Cannot use the pipeline root {root}: " in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert blocker.is_file() and blocker.read_text() == content
    assert not (tmp_path / "out").exists()
    assert _calls(tmp_path) == []


def test_an_error_writing_pytasks_state_after_the_build_exits_one_naming_the_root(
    tmp_path: Path,
) -> None:
    """When pytask cannot write its lockfile at the end of a build (a directory already sits at <root>/pytask.lock.tmp), 'vwf run' exits 1, not 2, the step's product exists, and stderr has 'Could not read or write the pipeline's state under <root>: ' with no traceback."""
    path = _write_definition(tmp_path, _CHAIN)
    root = tmp_path.resolve() / "state"
    # pytask writes the lockfile to pytask.lock.tmp and then renames it, so a directory at
    # that path fails the write that ends a build in which every step ran.
    (root / "pytask.lock.tmp").mkdir(parents=True)

    result = _invoke("run", str(path))

    assert result.exit_code == 1, _describe(result)
    # CliRunner also reports an exception that vwf did not handle as exit code 1.
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
    """Two hand-built command steps whose outputs are unnamed PathNodes on HERE / 'sub' / '..' / 'x.txt' and HERE / 'x.txt' (with HERE / 'sub' existing) exit 2 with vwf's double-claim message naming both steps."""
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
