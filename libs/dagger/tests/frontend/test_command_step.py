"""Acceptance tests for the command_step frontend (``frontend.command_step``).

Every shell command in these tests uses POSIX sh syntax only, since a command step runs
under ``/bin/sh``.
"""

import os
import shlex
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import pytest
from click.testing import CliRunner, Result
from pytask import NodeNotFoundError, PathNode, PNode, TaskOutcome

import vivarium.dagger.frontend as frontend_package
from vivarium.dagger.backend.build import run_command
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry, WiringError
from vivarium.dagger.cli import dagger
from vivarium.dagger.frontend import (
    Pipeline,
    PipelineError,
    StepFailed,
    preview,
    python_step,
    run,
)
from vivarium.dagger.frontend.command_step import command_step
from vivarium.dagger.harness import Perturbed, with_stable_code_id

_V = TypeVar("_V")

_TOOL = "vivarium_dagger_command_step_probe"
"""A tool that exists only in the bin directory of the fake envs these tests make."""

_ENV = "command-env"
"""The env of steps that are compiled but never built; an env is resolved only at build."""

_POPULATION = "inputs/population.csv"
"""The key of a root input table."""

_SCRIPT = "inputs/tool.sh"
"""The key of a root input script."""

_SOURCE = "inputs/source.txt"
"""The key of the root input that the staleness tests copy."""

_REPORT = "its output is in pytask's report for the step."
"""The end of the failure line of a command step that exits non-zero."""

_PROBE_KEY = "?"
"""A key that no test registers and that no registered key is close to."""

_NAMES = {
    "inputs": {"population": _POPULATION, "script": _SCRIPT},
    "produces": {"adults": "adults.csv"},
}
"""The inputs and produces of the placeholder tests, whose names are _SORTED_NAMES."""

_SORTED_NAMES = ("adults", "population", "script")

_RESERVED = ["__code_id__", "__command__", "__notebook__", "__executed__"]

_FORMATTER_MESSAGES = (
    "encountered in format string",
    "before end of string",
    "in field name",
    "in format spec",
    "after conversion specifier",
)
"""Parts of the messages string.Formatter.parse raises for a bad brace or placeholder."""

_DEFINITION = """
from pathlib import Path

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, command_step

HERE = Path(__file__).resolve().parent


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    task = command_step(
        COMMAND, produces={"out": "out.txt"}, registry=registry, env=ENV, name=NAME
    )
    return Pipeline([task], HERE / "state", registry)
"""
"""A definition of one command step writing 'out.txt'; COMMAND, ENV and NAME go before it."""


def _make_env(prefix: Path) -> Path:
    """Create a fake env at ``prefix`` with ``bin/python`` and a ``_TOOL`` script.

    The tool appends its first argument as a line to the file named by its second.
    """
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").touch()
    tool = bin_dir / _TOOL
    tool.write_text('#!/bin/sh\necho "$1" >> "$2"\n')
    tool.chmod(0o755)
    return prefix


def _registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry under ``tmp_path`` with the root inputs _POPULATION and _SCRIPT."""
    supplied = tmp_path / "supplied"
    supplied.mkdir(parents=True, exist_ok=True)
    (supplied / "population.csv").write_text("age,count\n10,1\n40,2\n")
    (supplied / "tool.sh").write_text('cat "$1"\n')
    return ArtifactRegistry(
        tmp_path / "out",
        "run1",
        root_inputs={
            _POPULATION: supplied / "population.csv",
            _SCRIPT: supplied / "tool.sh",
        },
    )


def _registered(registry: ArtifactRegistry) -> str:
    """Return the registry's message for an unknown key, which lists every key it holds."""
    try:
        registry.resolve_input(_PROBE_KEY)
    except WiringError as error:
        return str(error)
    raise AssertionError(f"The registry resolved the unknown key {_PROBE_KEY!r}.")


def _valid() -> dict[str, Any]:
    """Return the arguments, other than the registry, of a valid command_step call."""
    return {
        "command": "true",
        "inputs": {"population": _POPULATION},
        "produces": {"adults": "adults.csv"},
        "name": "subset",
    }


def _refused(registry: ArtifactRegistry, **overrides: Any) -> str:
    """Return the ValueError of a changed valid call, which must register nothing."""
    before = _registered(registry)
    arguments = {**_valid(), **overrides}

    with pytest.raises(ValueError) as info:
        command_step(registry=registry, **arguments)

    assert _registered(registry) == before
    return str(info.value)


def _declaring(name: str, where: str) -> dict[str, Any]:
    """Return command_step arguments that declare ``name`` in ``where``."""
    key = _POPULATION if where == "inputs" else "declared.csv"
    return {where: {name: key}}


def _assert_lists_names(message: str, names: tuple[str, ...] = _SORTED_NAMES) -> None:
    """Assert that ``message`` quotes each of ``names``, in the order given."""
    positions = [message.find(repr(name)) for name in names]
    assert -1 not in positions, message
    assert positions == sorted(positions), message


def _assert_hint_last(message: str, *parts: str) -> None:
    """Assert that ``message`` ends with the brace hint, after every one of ``parts``."""
    hint = message.rfind("{{")
    assert hint != -1 and "}}" in message[hint:], message
    late = [part for part in parts if not -1 < message.find(part) < hint]
    assert late == [], message


def _path_of(node: PNode) -> Path:
    """Return the path of a file node."""
    assert isinstance(node, PathNode)
    assert isinstance(node.path, Path)
    return node.path


def _reversed(mapping: Mapping[str, _V]) -> dict[str, _V]:
    """Return ``mapping``'s entries in reverse insertion order."""
    return dict(reversed(list(mapping.items())))


def _compiled(tmp_path: Path, command: str) -> str:
    """Return the run of a step compiled from ``command`` with a fresh registry."""
    task = command_step(
        command,
        inputs={"population": _POPULATION},
        produces={"adults": "adults.csv"},
        registry=_registry(tmp_path),
        name="subset",
    )
    assert isinstance(task.run, str)
    return task.run


_ORDER_COMMAND = "sh {script} {population} > {adults} 2> {log} && touch {a} {b} {c} {d}"
"""The command of the test that equal mappings in any order compile to one command."""

_ORDER_PRODUCTS = {
    "adults": "adults.csv",
    "log": "logs/adults.log",
    "a": "a/a.txt",
    "b": "b/b.txt",
    "c": "c/c.txt",
    "d": "d/d.txt",
}
"""Products in six directories, so that set order is unlikely to agree across hash seeds."""

_HASH_SEED_SCRIPT = """
import sys
from pathlib import Path

from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.frontend.command_step import command_step

root, population, population_path, script, script_path, command, *pairs = sys.argv[1:]
registry = ArtifactRegistry(
    Path(root),
    "run1",
    root_inputs={population: Path(population_path), script: Path(script_path)},
)
task = command_step(
    command,
    inputs={"script": script, "population": population},
    produces=dict(zip(pairs[::2], pairs[1::2])),
    registry=registry,
    name="subset",
)
sys.stdout.write(task.run)
"""
"""Print the run of the any-order test's step, compiled from the arguments given."""


def _compiled_with_hash_seed(tmp_path: Path, seed: str) -> str:
    """Return the run of the any-order test's step compiled in a process with ``seed``."""
    supplied = tmp_path / "supplied"
    pairs = [part for item in reversed(_ORDER_PRODUCTS.items()) for part in item]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _HASH_SEED_SCRIPT,
            str(tmp_path / "out"),
            _POPULATION,
            str(supplied / "population.csv"),
            _SCRIPT,
            str(supplied / "tool.sh"),
            _ORDER_COMMAND,
            *pairs,
        ],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _one_step(tmp_path: Path, command: str, **arguments: Any) -> tuple[Pipeline, Path]:
    """Return a pipeline of one step 'step' writing 'out.txt', and that product's path."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = command_step(
        command, produces={"out": "out.txt"}, registry=registry, name="step", **arguments
    )
    pipeline = Pipeline([task], tmp_path / "state", registry)
    return pipeline, registry.path_for("out.txt")


def _step_failed(pipeline: Pipeline) -> StepFailed:
    """Run ``pipeline`` and return the StepFailed it raises."""
    with pytest.raises(StepFailed) as info:
        run(pipeline)
    return info.value


def _copy_pipeline(tmp_path: Path, command: str) -> Pipeline:
    """Return a pipeline of one step 'copy', with a stable code identity, reading _SOURCE."""
    source = tmp_path / "supplied" / "source.txt"
    if not source.exists():
        source.parent.mkdir(parents=True)
        source.write_text("n\n1\n")
    registry = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={_SOURCE: source})
    task = command_step(
        command,
        inputs={"source": _SOURCE},
        produces={"target": "copies/source.txt"},
        registry=registry,
        name="copy",
    )
    return Pipeline([with_stable_code_id(task)], tmp_path / "state", registry)


def _write_definition(directory: Path, command: str, env: str | None, name: str) -> Path:
    """Write a definition of one command step to a file whose stem no other test uses."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"def_{uuid.uuid4().hex[:12]}.py"
    settings = f"COMMAND = {command!r}\nENV = {env!r}\nNAME = {name!r}\n"
    path.write_text(settings + _DEFINITION)
    return path


def _defined_product(definition: Path) -> Path:
    """Return the path of the product of the step in ``definition``."""
    return definition.parent / "out" / "run1" / "out.txt"


def _dagger_run(definition: Path) -> Result:
    """Run 'dagger run -c <definition> --local' in this process."""
    return CliRunner().invoke(dagger, ["run", "-c", str(definition), "--local"])


def _people() -> pd.DataFrame:
    """Return a table of names and ages."""
    return pd.DataFrame({"name": ["ana", "bo", "cy"], "age": [12, 34, 56]})


def _total_age(adults: Path) -> pd.DataFrame:
    """Sum the ages in the adults table."""
    frame = pd.read_csv(adults)
    return pd.DataFrame({"total_age": [frame["age"].sum()]})


@pytest.fixture
def forget_definitions(tmp_path: Path) -> Iterator[None]:
    """Restore ``sys.path`` and drop the modules a test loaded from its definition files."""
    path = list(sys.path)
    modules = set(sys.modules)
    yield
    sys.path[:] = path
    for name in set(sys.modules) - modules:
        file = getattr(sys.modules.get(name), "__file__", None)
        if isinstance(file, str) and Path(file).resolve().is_relative_to(tmp_path.resolve()):
            del sys.modules[name]


# The task command_step returns


def test_command_step_is_exported_from_the_frontend() -> None:
    """``vivarium.dagger.frontend.command_step`` is the function in ``frontend.command_step`` and is listed in the frontend's ``__all__``."""
    assert vars(frontend_package)["command_step"] is command_step
    assert "command_step" in frontend_package.__all__


def test_command_step_returns_one_command_task_with_the_declared_env_and_resources(
    tmp_path: Path,
) -> None:
    """command_step returns one Task whose run is a str, whose env is the given env and whose resources are the given dict."""
    resources: dict[str, Any] = {"cores": 2, "memory": "4G", "runtime": "1h"}

    task = command_step(
        "cp {population} {adults}",
        inputs={"population": _POPULATION},
        produces={"adults": "adults.csv"},
        registry=_registry(tmp_path),
        env=_ENV,
        resources=resources,
    )

    assert isinstance(task, Task)
    assert isinstance(task.run, str)
    assert task.env == _ENV
    assert task.resources == resources


def test_inputs_are_the_resolved_input_nodes_keyed_by_input_name(tmp_path: Path) -> None:
    """The task's inputs map each input name to the producing step's own node for a produced key and to a node at the file's path for a root input."""
    registry = _registry(tmp_path)
    produced = registry.register_output("upstream/table.csv")

    task = command_step(
        "cat {table} {population} > {out}",
        inputs={"table": "upstream/table.csv", "population": _POPULATION},
        produces={"out": "out.csv"},
        registry=registry,
    )

    assert set(task.inputs) == {"table", "population"}
    assert task.inputs["table"] is produced
    # A root input resolves to a new node on each call, so only its path can match.
    expected = _path_of(registry.resolve_input(_POPULATION))
    assert _path_of(task.inputs["population"]) == expected


def test_outputs_are_the_registry_nodes_keyed_by_product_name(tmp_path: Path) -> None:
    """The task's outputs map each product name to the node the registry holds for its key, which is the node a downstream step resolves."""
    registry = _registry(tmp_path)
    produces = {"table": "tables/adults.csv", "log": "logs/adults.log"}

    task = command_step(
        "cp {population} {table} 2> {log}",
        inputs={"population": _POPULATION},
        produces=produces,
        registry=registry,
        name="subset",
    )

    assert set(task.outputs) == set(produces)
    for name, key in produces.items():
        assert task.outputs[name] is registry.resolve_input(key)
        assert _path_of(task.outputs[name]) == registry.path_for(key)


def test_inputs_env_and_resources_are_optional(tmp_path: Path) -> None:
    """With no inputs, env or resources the task has no inputs, env None and resources {}."""
    task = command_step(
        "printf x > {out}", produces={"out": "out.txt"}, registry=_registry(tmp_path)
    )

    assert dict(task.inputs) == {}
    assert task.env is None
    assert task.resources == {}


def test_name_defaults_to_the_single_produces_key(tmp_path: Path) -> None:
    """With name=None and one product the task is named by that product's artifact key."""
    task = command_step(
        "printf x > {out}", produces={"out": "tables/out.csv"}, registry=_registry(tmp_path)
    )

    assert task.name == "tables/out.csv"


def test_name_overrides_the_default(tmp_path: Path) -> None:
    """A given name becomes the task name, also when produces has more than one entry."""
    registry = _registry(tmp_path)

    single = command_step(
        "printf x > {out}", produces={"out": "one.txt"}, registry=registry, name="single"
    )
    pair = command_step(
        "printf x > {first} && printf y > {second}",
        produces={"first": "first.txt", "second": "second.txt"},
        registry=registry,
        name="pair",
    )

    assert single.name == "single"
    assert pair.name == "pair"


# Validation: every error is a ValueError raised before anything is registered


@pytest.mark.parametrize("command", ["", "   ", "\n\t "], ids=["empty", "spaces", "mixed"])
def test_an_empty_or_whitespace_command_is_refused(tmp_path: Path, command: str) -> None:
    """A command of "" or only whitespace raises ValueError starting "Step '<name>':", and nothing is registered."""
    message = _refused(_registry(tmp_path), command=command)

    assert message.startswith("Step 'subset':"), message


def test_an_empty_name_is_refused_with_the_command_step_prefix(tmp_path: Path) -> None:
    """name="" raises ValueError starting "command_step:", and nothing is registered."""
    message = _refused(_registry(tmp_path), name="")

    assert message.startswith("command_step:"), message


def test_an_empty_env_is_refused_naming_the_step(tmp_path: Path) -> None:
    """env="" raises ValueError starting "Step '<name>':", and nothing is registered."""
    message = _refused(_registry(tmp_path), env="")

    assert message.startswith("Step 'subset':"), message


def test_an_empty_produces_without_a_name_is_refused_with_the_command_step_prefix(
    tmp_path: Path,
) -> None:
    """produces={} with name=None raises ValueError starting "command_step:", and nothing is registered."""
    message = _refused(_registry(tmp_path), produces={}, name=None)

    assert message.startswith("command_step:"), message


def test_an_empty_produces_with_a_name_is_refused_naming_the_step(tmp_path: Path) -> None:
    """produces={} with a name raises ValueError starting "Step '<name>':", and nothing is registered."""
    message = _refused(_registry(tmp_path), produces={})

    assert message.startswith("Step 'subset':"), message


def test_several_products_without_a_name_are_refused_listing_the_keys_and_asking_for_a_name(
    tmp_path: Path,
) -> None:
    """Two products with name=None raise ValueError starting "command_step:" that lists the produces keys in sorted order and says to pass name=, and nothing is registered."""
    # The names and the keys sort in opposite orders, and the keys are given unsorted.
    produces = {"alpha": "z/out.csv", "zeta": "a/out.csv"}

    message = _refused(_registry(tmp_path), produces=produces, name=None)

    assert message.startswith("command_step:"), message
    first, second = message.find("a/out.csv"), message.find("z/out.csv")
    assert -1 < first < second, message
    assert "name=" in message


def test_the_errors_without_a_step_name_are_checked_before_every_other_error(
    tmp_path: Path,
) -> None:
    """An empty name wins over an empty produces, and the three errors that start "command_step:" are raised even when the command, env and names are also invalid."""
    registry = _registry(tmp_path)
    empty_name = _refused(registry, name="")
    empty_produces = _refused(registry, produces={}, name=None)
    invalid: dict[str, Any] = {"command": " ", "env": "", "inputs": {"my-file": _POPULATION}}

    assert _refused(registry, produces={}, name="") == empty_name
    assert _refused(registry, **invalid, name="") == empty_name
    assert _refused(registry, **invalid, produces={}, name=None) == empty_produces
    several = {"class": "b.csv", "__command__": "a.csv"}
    message = _refused(registry, **invalid, produces=several, name=None)
    assert message.startswith("command_step:"), message
    assert -1 < message.find("a.csv") < message.find("b.csv"), message


@pytest.mark.parametrize("where", ["inputs", "produces"])
def test_a_name_that_is_not_an_identifier_is_refused(tmp_path: Path, where: str) -> None:
    """An input or product name such as "my-file" raises ValueError naming the step and the name, and nothing is registered."""
    message = _refused(_registry(tmp_path), **_declaring("my-file", where))

    assert message.startswith("Step 'subset':"), message
    assert "my-file" in message


@pytest.mark.parametrize("where", ["inputs", "produces"])
def test_a_python_keyword_name_is_refused(tmp_path: Path, where: str) -> None:
    """An input or product name such as "class" raises ValueError naming the step and the name, and nothing is registered."""
    message = _refused(_registry(tmp_path), **_declaring("class", where))

    assert message.startswith("Step 'subset':"), message
    assert "class" in message


@pytest.mark.parametrize("where", ["inputs", "produces"])
@pytest.mark.parametrize("reserved", _RESERVED)
def test_each_reserved_name_is_refused(tmp_path: Path, reserved: str, where: str) -> None:
    """Each of __code_id__, __command__, __notebook__ and __executed__, as an input name and as a product name, raises ValueError naming the step and the name, and nothing is registered."""
    message = _refused(_registry(tmp_path), **_declaring(reserved, where))

    assert message.startswith("Step 'subset':"), message
    assert reserved in message


def test_a_name_in_both_inputs_and_produces_is_refused(tmp_path: Path) -> None:
    """A name used in both inputs and produces raises ValueError naming the step and the name, and nothing is registered."""
    message = _refused(
        _registry(tmp_path),
        inputs={"shared_name": _POPULATION},
        produces={"shared_name": "out.csv"},
    )

    assert message.startswith("Step 'subset':"), message
    assert "shared_name" in message


@pytest.mark.parametrize("right_key", ["x.csv", "./x.csv"], ids=["same_key", "same_path"])
def test_two_products_at_one_path_are_refused_naming_both(
    tmp_path: Path, right_key: str
) -> None:
    """Two produces entries whose keys resolve to one path (the same key under two names, or "x.csv" and "./x.csv") raise ValueError naming both products and the second key, and nothing is registered."""
    produces = {"left_table": "x.csv", "right_table": right_key}

    message = _refused(_registry(tmp_path), produces=produces, name="pair")

    assert message.startswith("Step 'pair':"), message
    assert "'left_table'" in message, message
    assert "'right_table'" in message, message
    assert repr(right_key) in message, message


def test_the_same_key_as_input_and_product_is_refused_before_any_registry_lookup(
    tmp_path: Path,
) -> None:
    """One key in both inputs and produces raises ValueError, not WiringError and not a pytask cycle at build time, even when the key has no producer, naming the input and the product with their keys and saying a step cannot read and write the same artifact, and nothing is registered."""
    # 'shared.csv' has no producer and is no root input, so resolving it would raise
    # WiringError; pytest.raises(ValueError) does not catch a WiringError.
    message = _refused(
        _registry(tmp_path),
        inputs={"source": "shared.csv"},
        produces={"target": "shared.csv"},
        name="copy",
    )

    assert message.startswith("Step 'copy':"), message
    for part in ("source", "target", "shared.csv", "read", "write"):
        assert part in message, part


def test_an_input_produced_at_a_product_path_under_another_key_is_refused(
    tmp_path: Path,
) -> None:
    """An input "x.csv" produced by an earlier step and a product "./x.csv" raise ValueError, not the registry's "resolve to one path" WiringError, naming both inputs and both products with their keys, and nothing more is registered."""
    registry = _registry(tmp_path)
    registry.register_output("x.csv")

    message = _refused(
        registry, inputs={"source": "x.csv"}, produces={"target": "./x.csv"}, name="redo"
    )

    assert message.startswith("Step 'redo':"), message
    assert "source" in message
    assert "target" in message
    assert "./x.csv" in message
    # The input's key is a part of the product's, so look for it elsewhere.
    assert "x.csv" in message.replace("./x.csv", ""), message
    assert "resolve to one path" not in message


def test_a_root_input_at_a_product_path_is_refused(tmp_path: Path) -> None:
    """A root input whose file lies under the run directory at a product's path raises ValueError naming the input and the product with their keys, and nothing is registered."""
    product_key = "tables/given.csv"
    handed_over = (tmp_path / "out").resolve() / "run1" / product_key
    handed_over.parent.mkdir(parents=True)
    handed_over.write_text("n\n1\n")
    registry = ArtifactRegistry(
        tmp_path / "out", "run1", root_inputs={"supplied_table": handed_over}
    )
    assert registry.path_for(product_key) == handed_over.resolve()

    message = _refused(
        registry,
        inputs={"handed": "supplied_table"},
        produces={"result": product_key},
        name="redo",
    )

    assert message.startswith("Step 'redo':"), message
    for part in ("handed", "supplied_table", "result", product_key):
        assert part in message, part
    assert "resolve to one path" not in message


def test_an_unresolved_input_raises_the_wiring_error_before_the_path_check(
    tmp_path: Path,
) -> None:
    """An input key with no producer and no root input raises WiringError starting "Step '<name>': input '<key>' is unresolved", even when another check of the paths would also fail, and nothing is registered."""
    registry = _registry(tmp_path)
    before = _registered(registry)
    # The input key and the product key name one path.
    assert registry.path_for("missing.csv") == registry.path_for("./missing.csv")

    with pytest.raises(WiringError) as info:
        command_step(
            "cp {source} {target}",
            inputs={"source": "missing.csv"},
            produces={"target": "./missing.csv"},
            registry=registry,
            name="redo",
        )

    message = str(info.value)
    assert message.startswith("Step 'redo': input 'missing.csv' is unresolved"), message
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert _registered(registry) == before


def test_a_product_that_is_a_root_input_raises_a_wiring_error_naming_the_step(
    tmp_path: Path,
) -> None:
    """A produces key that is a declared root input raises WiringError starting "Step '<name>':" followed by the registry's message, with no chained cause shown."""
    with pytest.raises(WiringError) as info:
        command_step(
            "printf x > {out}",
            produces={"out": _POPULATION},
            registry=_registry(tmp_path),
            name="clobber",
        )

    error = info.value
    expected = f"Step 'clobber': output {_POPULATION!r} is a declared root input"
    assert str(error).startswith(expected), str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__


def test_a_product_at_another_steps_product_path_raises_a_wiring_error_naming_the_step(
    tmp_path: Path,
) -> None:
    """A produces key that resolves to the path of a different key another step produces raises WiringError starting "Step '<name>':" with the registry's message."""
    registry = _registry(tmp_path)
    registry.register_output("tables/x.csv")

    with pytest.raises(WiringError) as info:
        command_step(
            "printf x > {out}",
            produces={"out": "tables/./x.csv"},
            registry=registry,
            name="clash",
        )

    expected = "Step 'clash': keys 'tables/x.csv' and 'tables/./x.csv' resolve to one path"
    assert str(info.value).startswith(expected), str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


# Placeholders


def test_an_unknown_placeholder_is_refused_with_the_names_a_did_you_mean_and_the_brace_hint(
    tmp_path: Path,
) -> None:
    """{populaton} with inputs population and script raises ValueError starting "Step '<name>':" that quotes "{populaton}", lists the names in sorted order, asks "Did you mean 'population'?", ends with the hint to double a literal brace, and nothing is registered."""
    command = "sh {script} {populaton} > {adults}"
    suggestion = "Did you mean 'population'?"

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert "{populaton}" in message
    assert suggestion in message
    # The suggestion quotes a name too, so the list is checked without it.
    _assert_lists_names(message.replace(suggestion, ""))
    names = [repr(name) for name in _SORTED_NAMES]
    _assert_hint_last(message, "{populaton}", suggestion, *names)


def test_an_unknown_placeholder_with_no_close_name_has_no_did_you_mean(
    tmp_path: Path,
) -> None:
    """A placeholder far from every name, such as {zzz}, is refused with the names and the brace hint but no "Did you mean"."""
    message = _refused(_registry(tmp_path), command="sh {script} {zzz} > {adults}", **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert "{zzz}" in message
    assert "Did you mean" not in message
    _assert_lists_names(message)
    _assert_hint_last(message, "{zzz}", *[repr(name) for name in _SORTED_NAMES])


@pytest.mark.parametrize("placeholder", ["{}", "{0}"], ids=["empty", "positional"])
def test_an_empty_or_positional_placeholder_is_refused(
    tmp_path: Path, placeholder: str
) -> None:
    """Each of {} and {0} raises ValueError that quotes it as written, lists the names and ends with the brace hint, and nothing is registered."""
    command = "sh {script} {population} " + placeholder + " > {adults}"

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert placeholder in message
    _assert_lists_names(message)
    _assert_hint_last(message, placeholder, *[repr(name) for name in _SORTED_NAMES])


@pytest.mark.parametrize(
    "placeholder",
    [
        "{population!r}",
        "{population:>8}",
        "{population:}",
        "{population!r:}",
        "{population.parent}",
        "{population[0]}",
    ],
    ids=[
        "conversion",
        "format_spec",
        "empty_format_spec",
        "conversion_and_empty_format_spec",
        "attribute",
        "index",
    ],
)
def test_a_placeholder_with_a_conversion_spec_attribute_or_index_is_refused(
    tmp_path: Path, placeholder: str
) -> None:
    """Each of {x!r}, {x:>8}, {x:}, {x!r:}, {x.parent} and {x[0]}, where x is a valid name, raises ValueError that quotes it as written, lists the names and ends with the brace hint, and nothing is registered."""
    command = "sh {script} " + placeholder + " > {adults}"

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert placeholder in message
    _assert_lists_names(message)
    _assert_hint_last(message, placeholder, *[repr(name) for name in _SORTED_NAMES])


@pytest.mark.parametrize(
    "placeholder",
    ["{adults:}", "{adults!r:}", "{adults!r}", "{adults:>8}"],
    ids=[
        "empty_format_spec",
        "conversion_and_empty_format_spec",
        "conversion",
        "format_spec",
    ],
)
def test_a_placeholder_after_doubled_braces_is_quoted_as_written(
    tmp_path: Path, placeholder: str
) -> None:
    """After a literal '{{}}', each of {adults:}, {adults!r:}, {adults!r} and {adults:>8} raises ValueError that quotes the placeholder exactly as written; {adults!r} and {adults:>8} need the doubled braces counted to find it, and the empty-spec cases check the ':}' fallback."""
    command = "printf '%s' '{{}}' " + placeholder

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert f"the placeholder {placeholder}, " in message, message
    _assert_lists_names(message)
    _assert_hint_last(message, placeholder, *[repr(name) for name in _SORTED_NAMES])


def test_a_lone_closing_brace_is_refused_with_the_names_and_the_brace_hint(
    tmp_path: Path,
) -> None:
    """A single } that is not part of a placeholder raises ValueError starting "Step '<name>':" that says so, lists the names, ends with the brace hint, has no "Did you mean", and does not show Formatter's own message."""
    command = "sh {script} {population} } > {adults}"

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert "single" in message.lower()
    assert "Did you mean" not in message
    assert [part for part in _FORMATTER_MESSAGES if part in message] == [], message
    _assert_lists_names(message)
    _assert_hint_last(message, *[repr(name) for name in _SORTED_NAMES])


@pytest.mark.parametrize(
    "command",
    ["sh {script} {population} > {adults} {", "sh { {script} {population} > {adults}"],
    ids=["at_the_end", "unclosed"],
)
def test_a_lone_opening_brace_is_refused_with_the_names_and_the_brace_hint(
    tmp_path: Path, command: str
) -> None:
    """An unclosed { raises ValueError starting "Step '<name>':" that says so, lists the names, ends with the brace hint, has no "Did you mean", and does not show Formatter's own message."""
    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert "single" in message.lower()
    assert "Did you mean" not in message
    assert [part for part in _FORMATTER_MESSAGES if part in message] == [], message
    _assert_lists_names(message)
    _assert_hint_last(message, *[repr(name) for name in _SORTED_NAMES])


@pytest.mark.parametrize(
    "placeholder", ["{adults!}", "{adults!rr}"], ids=["empty_conversion", "long_conversion"]
)
def test_an_unparseable_placeholder_is_refused_without_calling_it_a_single_brace(
    tmp_path: Path, placeholder: str
) -> None:
    """Each of {adults!} and {adults!rr} raises ValueError starting "Step '<name>':" that lists the names, ends with the brace hint, does not say "single", and does not show Formatter's own message."""
    command = "sh {script} {population} > " + placeholder

    message = _refused(_registry(tmp_path), command=command, **_NAMES)

    assert message.startswith("Step 'subset':"), message
    assert "single" not in message.lower(), message
    assert [part for part in _FORMATTER_MESSAGES if part in message] == [], message
    _assert_lists_names(message)
    _assert_hint_last(message, *[repr(name) for name in _SORTED_NAMES])


def test_an_unused_input_or_product_name_is_allowed(tmp_path: Path) -> None:
    """A command that names neither of an input and a product is accepted, and the task still has both as an input and an output."""
    registry = _registry(tmp_path)

    task = command_step(
        "true",
        inputs={"population": _POPULATION},
        produces={"adults": "adults.csv"},
        registry=registry,
    )

    assert set(task.inputs) == {"population"}
    assert set(task.outputs) == {"adults"}
    assert task.outputs["adults"] is registry.resolve_input("adults.csv")


def test_placeholders_become_quoted_absolute_paths_that_the_shell_reads_back(
    tmp_path: Path,
) -> None:
    """Under an output root whose path holds a space, a single quote and a brace, a step that copies {source} to {target} runs, and its product holds the input's content."""
    source = tmp_path / "in put's {y}" / "source.txt"
    source.parent.mkdir()
    source.write_text("copied text\n")
    registry = ArtifactRegistry(
        tmp_path / "out root's {x}", "run1", root_inputs={"source.txt": source}
    )
    task = command_step(
        "cp {source} {target}",
        inputs={"source": "source.txt"},
        produces={"target": "copies/target.txt"},
        registry=registry,
        name="copy",
    )
    target = registry.path_for("copies/target.txt")
    assert all(char in str(path) for path in (source, target) for char in " '{")

    result = run(Pipeline([task], tmp_path / "state", registry))

    assert result == {"copy": TaskOutcome.SUCCESS}
    assert target.read_text() == "copied text\n"


def test_doubled_braces_reach_the_shell_as_single_braces(tmp_path: Path) -> None:
    """The command printf '%s' '{{}}' > {out} writes the two characters "{}" to the product."""
    pipeline, product = _one_step(tmp_path, "printf '%s' '{{}}' > {out}")

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert product.read_text() == "{}"


def test_the_command_is_identical_for_equal_mappings_in_any_order(tmp_path: Path) -> None:
    """Two calls whose inputs and produces hold the same entries in a different order give the same run string."""
    command = _ORDER_COMMAND
    inputs = {"population": _POPULATION, "script": _SCRIPT}
    produces = _ORDER_PRODUCTS

    forward = command_step(
        command,
        inputs=inputs,
        produces=produces,
        registry=_registry(tmp_path),
        name="subset",
    )
    backward = command_step(
        command,
        inputs=_reversed(inputs),
        produces=_reversed(produces),
        registry=_registry(tmp_path),
        name="subset",
    )

    assert isinstance(forward.run, str)
    assert forward.run == backward.run
    # Set order changes with the hash seed, so an unsorted directory list differs here.
    assert {_compiled_with_hash_seed(tmp_path, seed) for seed in ("0", "1")} == {forward.run}


def test_the_command_changes_when_the_command_text_changes(tmp_path: Path) -> None:
    """Two calls that differ only in the command text give different run strings."""
    baseline = _compiled(tmp_path, "cp {population} {adults}")

    # A control: equal arguments compile to an equal command, so a difference is real.
    assert _compiled(tmp_path, "cp {population} {adults}") == baseline
    assert _compiled(tmp_path, "cp -p {population} {adults}") != baseline


# Running the command
#
# The two tests of product directories run the command with run_command, outside pytask,
# since pytask may create a product's parent directory itself before a step runs.


def test_the_product_parent_directory_is_created_before_the_command_runs(
    tmp_path: Path,
) -> None:
    """Two product keys in different nested directories that do not exist yet, "a/b/out.txt" and "c/out.log", are both written by a command that does not create the directories."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = command_step(
        "printf made > {table} && printf log > {log}",
        produces={"table": "a/b/out.txt", "log": "c/out.log"},
        registry=registry,
        name="two_dirs",
    )
    table = registry.path_for("a/b/out.txt")
    log = registry.path_for("c/out.log")
    assert table.parent != log.parent
    assert not table.parent.exists()
    assert not log.parent.exists()

    assert isinstance(task.run, str)
    run_command(task.run, task.env)

    assert table.read_text() == "made"
    assert log.read_text() == "log"


def test_the_command_does_not_run_when_a_product_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    """When a product's parent path is an existing file, the step fails and the author's command, which would write a marker file, did not run."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    blocker = registry.path_for("blocked")
    blocker.parent.mkdir(parents=True)
    blocker.write_text("a file, not a directory\n")
    marker = tmp_path / "author_command_ran"
    task = command_step(
        f"touch {shlex.quote(str(marker))}",
        produces={"out": "blocked/out.txt"},
        registry=registry,
    )

    assert isinstance(task.run, str)
    with pytest.raises(subprocess.CalledProcessError):
        run_command(task.run, task.env)

    assert not marker.exists()


def test_the_command_runs_under_bin_sh(tmp_path: Path) -> None:
    """A command that writes "$0" to its product writes /bin/sh."""
    pipeline, product = _one_step(tmp_path, 'printf %s "$0" > {out}')

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert product.read_text() == "/bin/sh"


def test_the_command_runs_in_the_working_directory_of_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command that writes the output of pwd to its product writes the current working directory of the process that ran the build."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    pipeline, product = _one_step(tmp_path, "pwd -P > {out}")

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert Path(product.read_text().strip()) == work.resolve()


def test_exit_in_the_command_gives_its_status(tmp_path: Path) -> None:
    """A command "exit 3" makes the step fail with a CalledProcessError of return code 3, so the command is not wrapped in a subshell that hides it."""
    pipeline, _ = _one_step(tmp_path, "exit 3")

    error = _step_failed(pipeline)

    failure = error.failures["step"]
    assert isinstance(failure, subprocess.CalledProcessError)
    assert failure.returncode == 3


def test_a_trailing_comment_and_a_heredoc_in_the_command_behave_as_written(
    tmp_path: Path,
) -> None:
    """A command that ends in a shell comment, and a command that writes its product from a heredoc, each run and write the expected product."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    commented = command_step(
        "printf done > {out} # a trailing comment",
        produces={"out": "commented.txt"},
        registry=registry,
        name="commented",
    )
    heredoc = command_step(
        "cat > {out} <<'EOF'\nfirst line\nsecond line\nEOF",
        produces={"out": "heredoc.txt"},
        registry=registry,
        name="heredoc",
    )

    result = run(Pipeline([commented, heredoc], tmp_path / "state", registry))

    assert result == {"commented": TaskOutcome.SUCCESS, "heredoc": TaskOutcome.SUCCESS}
    assert registry.path_for("commented.txt").read_text() == "done"
    assert registry.path_for("heredoc.txt").read_text() == "first line\nsecond line\n"


def test_products_from_an_earlier_run_are_not_removed_before_the_command_runs(
    tmp_path: Path,
) -> None:
    """A command that appends a line to its product, run on two builds, leaves two lines in the product."""
    for _ in range(2):
        # The step's code identity is always stale, so it runs on each build.
        pipeline, product = _one_step(tmp_path, "echo again >> {out}")
        assert run(pipeline) == {"step": TaskOutcome.SUCCESS}

    assert product.read_text() == "again\nagain\n"


def test_without_an_env_the_command_runs_with_the_path_of_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With env=None, a tool that is only in a directory put on the process's PATH is found by the command."""
    bin_dir = _make_env(tmp_path / "env") / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    pipeline, product = _one_step(tmp_path, _TOOL + " found {out}")

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert product.read_text() == "found\n"


# Failures


def test_a_failing_command_makes_run_raise_step_failed_with_the_exit_status(
    tmp_path: Path,
) -> None:
    """A step whose command exits 1 makes run() raise StepFailed with the line "Step '<name>' failed: the command exited with status 1; its output is in pytask's report for the step."."""
    pipeline, _ = _one_step(tmp_path, "exit 1")

    error = _step_failed(pipeline)

    assert str(error) == f"Step 'step' failed: the command exited with status 1; {_REPORT}"


def test_a_command_stopped_by_a_signal_is_described_by_the_signal(tmp_path: Path) -> None:
    """A step whose command kills its own shell with SIGKILL makes run() raise StepFailed whose line names SIGKILL."""
    pipeline, _ = _one_step(tmp_path, "kill -9 $$")

    error = _step_failed(pipeline)

    assert str(error) == (
        f"Step 'step' failed: the command was stopped by signal SIGKILL; {_REPORT}"
    )


def test_the_failure_line_does_not_contain_the_generated_command(tmp_path: Path) -> None:
    """The StepFailed message of a failing command step does not contain the command text, which stays in the CalledProcessError cause."""
    pipeline, _ = _one_step(tmp_path, "echo distinctive-command-text && exit 4")
    [task] = pipeline.tasks

    error = _step_failed(pipeline)

    assert "distinctive-command-text" not in str(error)
    assert isinstance(task.run, str)
    assert task.run not in str(error)
    cause = error.__cause__
    assert isinstance(cause, subprocess.CalledProcessError)
    assert cause.cmd == task.run


@pytest.mark.usefixtures("forget_definitions")
def test_dagger_run_reports_a_failing_command_step_and_exits_one(tmp_path: Path) -> None:
    """'dagger run -c <definition> --local' on a definition whose command step exits 1 exits 1 with the step's failure line on stderr."""
    definition = _write_definition(tmp_path / "definition", "exit 1", None, "fails")

    result = _dagger_run(definition)

    assert result.exit_code == 1, result.output
    expected = f"Step 'fails' failed: the command exited with status 1; {_REPORT}"
    assert expected in result.stderr, result.stderr


def test_a_command_that_writes_no_declared_product_fails_the_step_naming_it(
    tmp_path: Path,
) -> None:
    """A command that exits 0 without writing its product makes run() raise StepFailed naming the step and pytask's NodeNotFoundError."""
    pipeline, product = _one_step(tmp_path, "true")

    error = _step_failed(pipeline)

    failure = error.failures["step"]
    assert isinstance(failure, NodeNotFoundError)
    assert str(error) == f"Step 'step' failed: NodeNotFoundError: {failure}"
    assert not product.exists()


# Preview and skipping


def test_a_second_build_of_an_unchanged_command_step_is_skipped(tmp_path: Path) -> None:
    """With a stable code identity, a command step that succeeded has outcome SKIP_UNCHANGED on the next run() with nothing changed."""
    command = "cp {source} {target}"
    assert run(_copy_pipeline(tmp_path, command)) == {"copy": TaskOutcome.SUCCESS}

    result = run(_copy_pipeline(tmp_path, command))

    assert result == {"copy": TaskOutcome.SKIP_UNCHANGED}


def test_a_changed_command_makes_preview_report_the_command_of_the_step(
    tmp_path: Path,
) -> None:
    """With a stable code identity, after a successful run a changed command text makes preview() report that the step would run with "command of '<step>'" among its reasons."""
    command = "cp {source} {target}"
    assert run(_copy_pipeline(tmp_path, command)) == {"copy": TaskOutcome.SUCCESS}

    [step] = preview(_copy_pipeline(tmp_path, "cp -p {source} {target}"))

    assert step.would_run
    assert "command of 'copy'" in [reason.node for reason in step.reasons], step.reasons


def test_a_changed_input_file_makes_preview_report_the_input_artifact(tmp_path: Path) -> None:
    """With a stable code identity, after a successful run a changed root input file makes preview() report that the step would run with "artifact '<key>'" among its reasons."""
    command = "cp {source} {target}"
    assert run(_copy_pipeline(tmp_path, command)) == {"copy": TaskOutcome.SUCCESS}

    with Perturbed(tmp_path / "supplied" / "source.txt", b"2\n"):
        [step] = preview(_copy_pipeline(tmp_path, command))

    assert step.would_run
    nodes = [reason.node for reason in step.reasons]
    assert f"artifact {_SOURCE!r}" in nodes, step.reasons


# Duplicate step names


def test_two_command_steps_with_one_name_make_run_raise_the_generic_message(
    tmp_path: Path,
) -> None:
    """Two command steps named 'dup' with different products make run() raise PipelineError "Two steps are named 'dup'; give each a different name= to tell them apart." before anything runs."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        command_step("printf x > {out}", produces={"out": key}, registry=registry, name="dup")
        for key in ("first.txt", "second.txt")
    ]

    with pytest.raises(PipelineError) as info:
        run(Pipeline(tasks, tmp_path / "state", registry))

    error = info.value
    assert not isinstance(error, StepFailed)
    assert str(error) == (
        "Two steps are named 'dup'; give each a different name= to tell them apart."
    )
    assert not registry.path_for("first.txt").exists()
    assert not registry.path_for("second.txt").exists()
    assert not (tmp_path / "state").exists()


# Environments


def test_a_command_step_with_an_env_runs_the_tool_from_that_env(tmp_path: Path) -> None:
    """A step with env set to a fake env's prefix path runs the tool that exists only in that env through run(), and its product records that the tool ran."""
    env = _make_env(tmp_path / "env")
    pipeline, product = _one_step(tmp_path, _TOOL + " ran {out}", env=str(env))

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert product.read_text() == "ran\n"


def test_a_compound_command_finds_the_env_tool_in_both_parts(tmp_path: Path) -> None:
    """A command "<tool> a {out} && <tool> b {out}" under a fake env runs the tool in both parts, so the product has both lines."""
    env = _make_env(tmp_path / "env")
    command = f"{_TOOL} a {{out}} && {_TOOL} b {{out}}"
    pipeline, product = _one_step(tmp_path, command, env=str(env))

    assert run(pipeline) == {"step": TaskOutcome.SUCCESS}
    assert product.read_text() == "a\nb\n"


def test_an_env_that_does_not_resolve_makes_run_raise_a_pipeline_error_naming_the_step(
    tmp_path: Path,
) -> None:
    """A step whose env is a path with no bin/python makes run() raise PipelineError naming the step and the env, before any step runs or writes a product."""
    missing_env = str(tmp_path / "no_such_env")
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        command_step(
            "printf x > {out}",
            produces={"out": "local.txt"},
            registry=registry,
            name="local",
        ),
        command_step(
            "printf x > {out}",
            produces={"out": "elsewhere.txt"},
            registry=registry,
            env=missing_env,
            name="elsewhere",
        ),
    ]

    with pytest.raises(PipelineError) as info:
        run(Pipeline(tasks, tmp_path / "state", registry))

    error = info.value
    assert not isinstance(error, StepFailed)
    assert "elsewhere" in str(error)
    assert missing_env in str(error)
    assert not registry.path_for("local.txt").exists()
    assert not registry.path_for("elsewhere.txt").exists()


@pytest.mark.usefixtures("forget_definitions")
def test_dagger_run_runs_a_command_step_under_its_env(tmp_path: Path) -> None:
    """'dagger run -c <definition> --local' on a definition with a command step under a fake env exits 0, and the step's product records that the env's tool ran."""
    env = _make_env(tmp_path / "env")
    definition = _write_definition(
        tmp_path / "definition", _TOOL + " ran {out}", str(env), "tooled"
    )

    result = _dagger_run(definition)

    assert result.exit_code == 0, result.output
    assert _defined_product(definition).read_text() == "ran\n"


@pytest.mark.usefixtures("forget_definitions")
def test_dagger_run_with_an_env_that_does_not_resolve_exits_two_with_the_message(
    tmp_path: Path,
) -> None:
    """'dagger run -c <definition> --local' on a definition whose command step's env does not resolve exits 2 with a message naming the step, and no step runs."""
    missing_env = str(tmp_path / "no_such_env")
    definition = _write_definition(
        tmp_path / "definition", "printf x > {out}", missing_env, "elsewhere"
    )

    result = _dagger_run(definition)

    assert result.exit_code == 2, result.output
    assert "elsewhere" in result.stderr, result.stderr
    assert missing_env in result.stderr, result.stderr
    assert not _defined_product(definition).exists()


# End to end


def test_python_command_python_chain_runs_through_run_with_the_right_final_product(
    tmp_path: Path,
) -> None:
    """A python_step, then a command_step reading its product, then a python_step reading the command step's product all have outcome SUCCESS through run(), and the final product holds the expected values."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        python_step(
            _people, inputs={}, produces="people.csv", registry=registry, name="people"
        ),
        command_step(
            "awk -F, 'NR == 1 || $2 > 20' {people} > {adults}",
            inputs={"people": "people.csv"},
            produces={"adults": "adults.csv"},
            registry=registry,
            name="adults",
        ),
        python_step(
            _total_age,
            inputs={"adults": "adults.csv"},
            produces="total.csv",
            registry=registry,
            name="total",
        ),
    ]

    result = run(Pipeline(tasks, tmp_path / "state", registry))

    assert result == dict.fromkeys(["people", "adults", "total"], TaskOutcome.SUCCESS)
    total = pd.read_csv(registry.path_for("total.csv"))
    assert total["total_age"].tolist() == [34 + 56]
