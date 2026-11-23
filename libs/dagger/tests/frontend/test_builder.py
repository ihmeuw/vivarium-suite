"""Tests for the Pipeline builder (MIC-7805, MIC-7810) and freezing it into a private snapshot."""

import copy
import dataclasses
import importlib
import inspect
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import PathNode, PythonNode, TaskOutcome

import vivarium.dagger.frontend
from tests.bridge.helpers import lockfile_entries
from tests.frontend.notebooks import write_source_notebook
from tests.frontend.simulations import write_branches, write_spec
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry, WiringError
from vivarium.dagger.frontend import PipelineError, preview, run
from vivarium.dagger.frontend.builder import STATE_DIR, Pipeline, _PipelineSnapshot, freeze
from vivarium.dagger.frontend.command_step import command_step
from vivarium.dagger.frontend.execution import _preview, _run
from vivarium.dagger.frontend.notebook_step import notebook_step
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.frontend.simulation_step import simulation_step

_STEP_KINDS = ("python", "notebook", "command", "simulation")
"""The four step methods, by the kind of step each adds."""

_STEP_FUNCTIONS: dict[str, Callable[..., Task]] = {
    "python": python_step,
    "notebook": notebook_step,
    "command": command_step,
    "simulation": simulation_step,
}
"""The step function that each kind of step method calls."""

_RAW = "raw.csv"
"""The key of the root input that :func:`_arguments`' steps read."""

_NOTEBOOK_PARAMETERS = "raw = 'unset'\ntable = 'unset'\nlocation = 'unset'"
"""The parameters cell of the notebook that :func:`_arguments` writes."""


def _constant() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _increment(source: Path) -> pd.DataFrame:
    """Read a Parquet table and add one to its ``n`` column."""
    return pd.DataFrame({"n": pd.read_parquet(source)["n"] + 1})


def _read_raw(raw: Path) -> pd.DataFrame:
    """Read the root input as CSV."""
    return pd.read_csv(raw)


def _root_inputs(tmp_path: Path) -> dict[str, Path]:
    """Write the root input under ``tmp_path`` and return the root inputs that name it."""
    path = tmp_path / _RAW
    path.write_text("n\n1\n2\n")
    return {_RAW: path}


def _pipeline(tmp_path: Path) -> Pipeline:
    """Return a pipeline under ``tmp_path / "out"`` whose root inputs give ``raw.csv``."""
    return Pipeline(tmp_path / "out", "run1", root_inputs=_root_inputs(tmp_path))


def _arguments(kind: str, tmp_path: Path, stem: str, name: str) -> dict[str, Any]:
    """Return every argument but ``registry`` of a step of ``kind`` named ``name``.

    The step reads the root input ``raw.csv``, and its outputs are named after ``stem``.
    """
    resources = {"memory": 2}
    if kind == "python":
        return {
            "target": _read_raw,
            "inputs": {"raw": _RAW},
            "outputs": f"{stem}.parquet",
            "resources": resources,
            "name": name,
        }
    if kind == "notebook":
        notebook = write_source_notebook(tmp_path / "notebook.ipynb", _NOTEBOOK_PARAMETERS)
        return {
            "notebook": notebook,
            "executed": f"{stem}.ipynb",
            "env": "notebook_env",
            "inputs": {"raw": _RAW},
            "outputs": {"table": f"{stem}_table.csv"},
            "parameters": {"location": "here"},
            "resources": resources,
            "name": name,
        }
    if kind == "command":
        return {
            "command": "cp {raw} {copy}",
            "outputs": {"copy": f"{stem}.csv"},
            "inputs": {"raw": _RAW},
            "env": "command_env",
            "resources": resources,
            "name": name,
        }
    model = tmp_path / "model"
    return {
        "spec": write_spec(model / "spec.yaml"),
        "branches": write_branches(model / "branches.yaml"),
        "outputs": f"sims/{stem}",
        "env": "simulation_env",
        "options": {"project": "proj_test", "no_slack": True},
        "artifact": _RAW,
        "resources": resources,
        "name": name,
    }


def _output_keys(kind: str, arguments: Mapping[str, Any]) -> list[str]:
    """Return the output keys of a step of ``kind`` with ``arguments``."""
    if kind == "notebook":
        return [arguments["executed"], *arguments["outputs"].values()]
    if kind == "command":
        return list(arguments["outputs"].values())
    return [arguments["outputs"]]


def _add(p: Pipeline, kind: str, arguments: Mapping[str, Any]) -> object:
    """Add a step of ``kind`` to ``p``; return what its method returns."""
    method: Callable[..., object] = getattr(p, f"add_{kind}_step")
    return method(**arguments)


def _registry_of(p: Pipeline) -> FileRegistry:
    """Return the registry of ``p``'s snapshots."""
    registry = freeze(p).registry
    assert registry is not None
    return registry


def _registered(p: Pipeline, key: str) -> bool:
    """Return whether ``key``'s path is registered in ``p``'s registry."""
    return _registry_of(p).key_for(_registry_of(p).path_for(key)) is not None


# Construction and the run's paths


def test_a_pipeline_holds_its_output_root_run_name_registry_and_state(tmp_path: Path) -> None:
    """Pipeline(tmp_path / "out", "run1") has output_root tmp_path/out resolved, run_name 'run1', an FileRegistry whose path_for('x.csv') is tmp_path/out/run1/x.csv, and state tmp_path/out/.dagger/run1."""
    output_root = (tmp_path / "out").resolve()

    p = Pipeline(tmp_path / "out", "run1")

    assert p.output_root == output_root
    assert p.run_name == "run1"
    assert isinstance(freeze(p).registry, FileRegistry)
    assert _registry_of(p).path_for("x.csv") == output_root / "run1" / "x.csv"
    assert STATE_DIR == ".dagger"
    assert p.state == output_root / ".dagger" / "run1"
    assert p.state.is_absolute()


def test_a_relative_output_root_is_resolved_against_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the current directory at tmp_path, Pipeline("out", "run1") has output_root tmp_path/out and the absolute default state tmp_path/out/.dagger/run1, which stay the same after the current directory changes."""
    output_root = (tmp_path / "out").resolve()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)

    p = Pipeline("out", "run1")
    monkeypatch.chdir(elsewhere)

    assert p.output_root == output_root
    assert p.state == output_root / ".dagger" / "run1"
    assert p.state.is_absolute()
    assert _registry_of(p).path_for("x.csv") == output_root / "run1" / "x.csv"
    assert freeze(p).root == output_root / ".dagger" / "run1"


def test_an_explicit_absolute_state_is_used_as_given(tmp_path: Path) -> None:
    """Pipeline(tmp_path / "out", "run1", state=tmp_path / "elsewhere") has state tmp_path/elsewhere, and its snapshot's root is that directory."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "elsewhere")

    assert p.state == tmp_path / "elsewhere"
    assert freeze(p).root == tmp_path / "elsewhere"


def test_an_explicit_state_given_as_a_string_is_accepted(tmp_path: Path) -> None:
    """state=str(tmp_path / "elsewhere") gives the Path tmp_path/elsewhere as state."""
    p = Pipeline(tmp_path / "out", "run1", state=str(tmp_path / "elsewhere"))

    assert isinstance(p.state, Path)
    assert p.state == tmp_path / "elsewhere"


def test_a_relative_state_is_refused_naming_the_path(tmp_path: Path) -> None:
    """state=Path("relative/state") raises ValueError whose message names 'relative/state' and says the state must be absolute."""
    with pytest.raises(ValueError) as info:
        Pipeline(tmp_path / "out", "run1", state=Path("relative/state"))

    message = str(info.value)
    assert "relative/state" in message, message
    assert "absolute" in message, message


def test_the_run_name_dot_dagger_is_refused(tmp_path: Path) -> None:
    """Pipeline(tmp_path, ".dagger") raises ValueError whose message names the run name '.dagger'."""
    with pytest.raises(ValueError) as info:
        Pipeline(tmp_path, ".dagger")

    assert "'.dagger'" in str(info.value), str(info.value)


@pytest.mark.parametrize(
    "run_name", ["", ".", "..", "../outside", "/absolute/run", ".dagger/run1", "a/../.dagger"]
)
def test_a_run_name_whose_output_directory_is_not_a_separate_directory_under_the_root_is_refused(
    tmp_path: Path, run_name: str
) -> None:
    """A run name whose output directory, with . and .. collapsed, is not strictly inside the output root, or is the .dagger directory or inside it, raises ValueError naming the run name."""
    with pytest.raises(ValueError) as info:
        Pipeline(tmp_path, run_name)

    assert repr(run_name) in str(info.value), str(info.value)


def test_an_absolute_run_name_inside_the_output_root_is_refused(tmp_path: Path) -> None:
    """An absolute run name that names a directory inside the output root raises ValueError naming the run name."""
    run_name = str(tmp_path.resolve() / "run1")

    with pytest.raises(ValueError) as info:
        Pipeline(tmp_path, run_name)

    assert repr(run_name) in str(info.value), str(info.value)
    assert "absolute" in str(info.value), str(info.value)


def test_a_nested_run_name_is_accepted(tmp_path: Path) -> None:
    """Pipeline(tmp_path, "study/run1") is accepted, with outputs under tmp_path/study/run1 and state tmp_path/.dagger/study/run1."""
    root = tmp_path.resolve()

    p = Pipeline(tmp_path, "study/run1")

    assert _registry_of(p).path_for("x.csv") == root / "study" / "run1" / "x.csv"
    assert p.state == root / ".dagger" / "study" / "run1"


def test_resources_that_are_not_a_mapping_raise_type_error(tmp_path: Path) -> None:
    """resources=[("project", "proj")] raises TypeError when the pipeline is made."""
    resources: Any = [("project", "proj")]

    with pytest.raises(TypeError):
        Pipeline(tmp_path, "run1", resources=resources)


def test_root_inputs_are_readable_by_a_step(tmp_path: Path) -> None:
    """A root input given to the pipeline can be named as a step's input, and the task's input node is at the root input's file."""
    raw = _root_inputs(tmp_path)[_RAW]
    p = Pipeline(tmp_path / "out", "run1", root_inputs={_RAW: raw})

    p.add_python_step(_read_raw, inputs={"raw": _RAW}, outputs="copy.parquet")

    [task] = freeze(p).tasks
    node = task.inputs["raw"]
    assert isinstance(node, PathNode)
    assert node.path == raw.resolve()


def test_two_pipelines_with_different_run_names_write_under_different_paths(
    tmp_path: Path,
) -> None:
    """Two pipelines under one output root with run names 'a' and 'b' resolve the same key to tmp_path/a/<key> and tmp_path/b/<key>, and running both pipelines locally writes both files."""
    root = tmp_path.resolve()
    pipelines = {name: Pipeline(tmp_path, name) for name in ("a", "b")}

    for name, p in pipelines.items():
        p.add_python_step(_constant, inputs={}, outputs="table.parquet")
        assert _registry_of(p).path_for("table.parquet") == root / name / "table.parquet"
    for p in pipelines.values():
        assert run(p) == {"table.parquet": TaskOutcome.SUCCESS}

    assert (root / "a" / "table.parquet").is_file()
    assert (root / "b" / "table.parquet").is_file()


def test_two_pipelines_with_different_run_names_have_separate_state_directories(
    tmp_path: Path,
) -> None:
    """Two pipelines under one output root with run names 'a' and 'b' have states tmp_path/.dagger/a and tmp_path/.dagger/b, and after a local run of each, each state directory holds its own pytask.lock with only its own steps."""
    root = tmp_path.resolve()
    first = Pipeline(tmp_path, "a")
    second = Pipeline(tmp_path, "b")
    first.add_python_step(_constant, inputs={}, outputs="table.parquet", name="first")
    second.add_python_step(_constant, inputs={}, outputs="table.parquet", name="second")

    assert first.state == root / ".dagger" / "a"
    assert second.state == root / ".dagger" / "b"
    assert run(first) == {"first": TaskOutcome.SUCCESS}
    assert run(second) == {"second": TaskOutcome.SUCCESS}

    assert set(lockfile_entries(first.state)) == {"first"}
    assert set(lockfile_entries(second.state)) == {"second"}


# Adding steps


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_method_adds_the_task_its_step_function_compiles(
    tmp_path: Path, kind: str
) -> None:
    """Each of add_python_step, add_notebook_step, add_command_step and add_simulation_step, given every argument of its step function except registry, adds a task equal to the one the step function compiles with the same arguments against a registry made from the same output root, run name and root inputs."""
    root_inputs = _root_inputs(tmp_path)
    p = Pipeline(tmp_path / "out", "run1", root_inputs=root_inputs)
    registry = FileRegistry(tmp_path / "out", "run1", root_inputs=root_inputs)
    arguments = _arguments(kind, tmp_path, "only", name=f"{kind}_step")

    _add(p, kind, arguments)

    expected = _STEP_FUNCTIONS[kind](**arguments, registry=registry)
    assert list(freeze(p).tasks) == [expected]


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_methods_parameters_are_its_step_functions_without_registry(
    kind: str,
) -> None:
    """Each step method's parameters after self are its step function's parameters without registry, in order, with the same names, kinds, defaults and annotations."""
    function = inspect.signature(_STEP_FUNCTIONS[kind], eval_str=True)
    method = inspect.signature(getattr(Pipeline, f"add_{kind}_step"), eval_str=True)

    expected = [param for param in function.parameters.values() if param.name != "registry"]
    actual = [param for param in method.parameters.values() if param.name != "self"]
    assert actual == expected


def test_a_pipeline_of_all_four_step_kinds_matches_one_assembled_by_hand(
    tmp_path: Path,
) -> None:
    """A pipeline of a python step, a notebook step reading its output, a command step and a simulation step, each with resources and a name, gives freeze(p).tasks equal, in order, to the tasks compiled by hand against an FileRegistry and passed to _PipelineSnapshot, with the same root, resources and registry keys."""
    notebook = write_source_notebook(
        tmp_path / "report.ipynb", "population = 'unset'\nlocation = 'unset'"
    )
    model = tmp_path / "model"
    steps: list[tuple[str, dict[str, Any]]] = [
        (
            "python",
            {
                "target": _constant,
                "inputs": {},
                "outputs": "population.parquet",
                "resources": {"memory": 2},
                "name": "population",
            },
        ),
        (
            "notebook",
            {
                "notebook": notebook,
                "executed": "report.ipynb",
                "env": "report_env",
                "inputs": {"population": "population.parquet"},
                "parameters": {"location": "here"},
                "resources": {"memory": 4},
                "name": "report",
            },
        ),
        (
            "command",
            {
                "command": "cp {source} {copy}",
                "inputs": {"source": "population.parquet"},
                "outputs": {"copy": "copy.parquet"},
                "resources": {"queue": "long.q"},
                "name": "copy",
            },
        ),
        (
            "simulation",
            {
                "spec": write_spec(model / "spec.yaml"),
                "branches": write_branches(model / "branches.yaml"),
                "outputs": "sims/toy",
                "env": "simulation_env",
                "options": {"project": "proj_test", "no_slack": True},
                "resources": {"runtime": 7200},
                "name": "simulate",
            },
        ),
    ]
    resources = {"project": "proj_test"}
    p = Pipeline(tmp_path / "out", "run1", resources=resources)
    for kind, arguments in steps:
        _add(p, kind, arguments)

    built = freeze(p)

    registry = FileRegistry(tmp_path / "out", "run1")
    tasks = [
        _STEP_FUNCTIONS[kind](**arguments, registry=registry) for kind, arguments in steps
    ]
    state = (tmp_path / "out").resolve() / ".dagger" / "run1"
    by_hand = _PipelineSnapshot(tasks, state, registry, resources=resources)
    assert list(built.tasks) == list(by_hand.tasks)
    names = [task.name for task in built.tasks]
    assert names == ["population", "report", "copy", "simulate"]
    assert built.root == by_hand.root
    assert dict(built.resources) == dict(by_hand.resources)
    built_registry = built.registry
    assert built_registry is not None
    assert built_registry is freeze(p).registry
    for task in built.tasks:
        for node in (*task.inputs.values(), *task.outputs.values()):
            if isinstance(node, PathNode):
                path = node.path
                assert isinstance(path, Path), node
                assert built_registry.key_for(path) == registry.key_for(path), node


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_method_returns_none(tmp_path: Path, kind: str) -> None:
    """Each step method returns None."""
    p = _pipeline(tmp_path)

    returned = _add(p, kind, _arguments(kind, tmp_path, "only", name="only"))

    assert returned is None


def test_a_step_method_keeps_the_step_functions_messages(tmp_path: Path) -> None:
    """p.add_python_step with an input key no step produces raises the same WiringError message that python_step raises for the same arguments against an equal registry."""
    producer: dict[str, Any] = {"inputs": {}, "outputs": "population.parquet"}
    consumer: dict[str, Any] = {
        "inputs": {"source": "populaton.parquet"},
        "outputs": "b.parquet",
    }
    registry = FileRegistry(tmp_path / "out", "run1")
    python_step(_constant, registry=registry, **producer)
    with pytest.raises(WiringError) as expected:
        python_step(_increment, registry=registry, **consumer)
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, **producer)

    with pytest.raises(WiringError) as raised:
        p.add_python_step(_increment, **consumer)

    assert str(raised.value) == str(expected.value)


def test_a_consumer_added_before_its_producer_is_refused(tmp_path: Path) -> None:
    """Adding a python step that reads 'a.parquet' before the step producing 'a.parquet' raises WiringError naming the step and the key as unresolved."""
    p = Pipeline(tmp_path / "out", "run1")

    with pytest.raises(WiringError) as info:
        p.add_python_step(
            _increment, inputs={"source": "a.parquet"}, outputs="b.parquet", name="b"
        )

    message = str(info.value)
    assert message.startswith("Step 'b':"), message
    assert "input 'a.parquet' is unresolved" in message, message


def test_a_command_step_with_several_outputs_and_no_name_raises_the_step_functions_error(
    tmp_path: Path,
) -> None:
    """p.add_command_step with two outputs keys and no name raises a ValueError that starts with 'add_command_step:' and says to pass name=."""
    p = Pipeline(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        p.add_command_step("touch {a} {b}", outputs={"a": "a.csv", "b": "b.csv"})

    message = str(info.value)
    assert message.startswith("add_command_step:"), message
    assert "pass name=" in message, message
    assert freeze(p).tasks == ()


def _split() -> dict[str, pd.DataFrame]:
    """Return a population table and a deaths table by output name."""
    return {
        "population": pd.DataFrame({"n": [10, 20]}),
        "deaths": pd.DataFrame({"n": [1, 2]}),
    }


def _read_csv(source: Path) -> pd.DataFrame:
    """Read a CSV table."""
    return pd.read_csv(source)


def _only() -> dict[str, pd.DataFrame]:
    """Return the fixed table under the output name 'out'."""
    return {"out": _constant()}


def test_a_python_step_with_several_outputs_registers_each_and_runs(tmp_path: Path) -> None:
    """p.add_python_step with outputs mapping 'population' and 'deaths' registers both keys; steps added later read each of them, and the pipeline runs and writes every output."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(
        _split,
        inputs={},
        outputs={"population": "population.parquet", "deaths": "deaths.csv"},
        name="split",
    )
    p.add_python_step(
        _increment, inputs={"source": "population.parquet"}, outputs="more.parquet"
    )
    p.add_python_step(_read_csv, inputs={"source": "deaths.csv"}, outputs="copy.csv")

    assert _registered(p, "population.parquet") and _registered(p, "deaths.csv")
    result = run(p)

    steps = ["split", "more.parquet", "copy.csv"]
    assert result == dict.fromkeys(steps, TaskOutcome.SUCCESS)
    more = pd.read_parquet(_registry_of(p).path_for("more.parquet"))
    assert more["n"].tolist() == [11, 21]
    assert pd.read_csv(_registry_of(p).path_for("copy.csv"))["n"].tolist() == [1, 2]


def test_a_second_producer_of_a_key_of_a_python_step_with_several_outputs_is_refused(
    tmp_path: Path,
) -> None:
    """After a python step 'split' producing 'population.parquet' and 'deaths.csv', a python step producing 'deaths.csv' raises WiringError naming 'split' and the key, and is not added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(
        _split,
        inputs={},
        outputs={"population": "population.parquet", "deaths": "deaths.csv"},
        name="split",
    )

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="deaths.csv", name="other")

    assert str(info.value) == (
        "Step 'other': output 'deaths.csv' is already produced by step 'split'; each file "
        "key can have only one producing step."
    )
    assert [task.name for task in freeze(p).tasks] == ["split"]


def test_a_python_step_with_several_outputs_and_no_name_raises_the_step_functions_error(
    tmp_path: Path,
) -> None:
    """p.add_python_step with two outputs keys and no name raises a ValueError that starts with 'add_python_step:' and says to pass name=, and adds nothing."""
    p = Pipeline(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        p.add_python_step(_split, inputs={}, outputs={"a": "a.csv", "b": "b.csv"})

    message = str(info.value)
    assert message.startswith("add_python_step:"), message
    assert "pass name=" in message, message
    assert freeze(p).tasks == ()
    assert not _registered(p, "a.csv")


def test_a_python_step_with_several_outputs_whose_later_key_is_already_produced_is_refused(
    tmp_path: Path,
) -> None:
    """After a step 'first' producing 'deaths.csv', a python step 'split' producing 'population.parquet' and 'deaths.csv' raises WiringError naming 'first', and neither 'split' nor 'population.parquet' is added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="deaths.csv", name="first")

    with pytest.raises(WiringError) as info:
        p.add_python_step(
            _split,
            inputs={},
            outputs={"population": "population.parquet", "deaths": "deaths.csv"},
            name="split",
        )

    assert str(info.value) == (
        "Step 'split': output 'deaths.csv' is already produced by step 'first'; each file "
        "key can have only one producing step."
    )
    assert [task.name for task in freeze(p).tasks] == ["first"]
    assert not _registered(p, "population.parquet")


def test_a_python_step_with_several_outputs_and_no_name_is_not_named_after_its_first_key(
    tmp_path: Path,
) -> None:
    """With a step already named 'a.csv' that does not produce it, a python step producing 'a.csv' and 'b.csv' with no name raises the no-default-name ValueError starting 'add_python_step:', not the error for a name already added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="z.csv", name="a.csv")

    with pytest.raises(ValueError) as info:
        p.add_python_step(_split, inputs={}, outputs={"a": "a.csv", "b": "b.csv"})

    message = str(info.value)
    assert message.startswith("add_python_step:"), message
    assert "has no default name" in message, message
    assert [task.name for task in freeze(p).tasks] == ["a.csv"]


def test_a_python_step_with_one_mapped_output_and_no_name_is_named_after_its_key(
    tmp_path: Path,
) -> None:
    """A python step whose outputs maps one name to 'x.csv' and has no name is named 'x.csv', and a second step whose name is 'x.csv' is refused as a name already added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_only, inputs={}, outputs={"out": "x.csv"})

    with pytest.raises(ValueError) as info:
        p.add_python_step(_constant, inputs={}, outputs="y.csv", name="x.csv")

    assert [task.name for task in freeze(p).tasks] == ["x.csv"]
    message = str(info.value)
    assert message.startswith("Step 'x.csv': a step named 'x.csv' was already added"), message


# Refusing a second step with a name or an output already added


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_a_second_step_with_a_name_already_added_is_refused(
    tmp_path: Path, kind: str
) -> None:
    """After a python step named 'dup', adding a step of each kind named 'dup' with different outputs raises ValueError starting with "Step 'dup':" that says to give each step a different name=."""
    p = _pipeline(tmp_path)
    p.add_python_step(_constant, inputs={}, outputs="first.parquet", name="dup")
    arguments = _arguments(kind, tmp_path, "second", name="dup")

    with pytest.raises(ValueError) as info:
        _add(p, kind, arguments)

    message = str(info.value)
    assert message.startswith("Step 'dup':"), message
    assert "different" in message, message
    assert "name=" in message, message
    for key in _output_keys(kind, arguments):
        assert not _registered(p, key), key
    assert [task.name for task in freeze(p).tasks] == ["dup"]


def test_a_step_whose_default_name_is_a_name_already_added_is_refused(tmp_path: Path) -> None:
    """After a step named 'x.csv' that produces 'other.csv', a python step producing 'x.csv' with no name, whose default name is 'x.csv', raises ValueError naming 'x.csv' as a name already added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="other.csv", name="x.csv")

    with pytest.raises(ValueError) as info:
        p.add_python_step(_constant, inputs={}, outputs="x.csv")

    message = str(info.value)
    assert message.startswith("Step 'x.csv':"), message
    assert "name=" in message, message


def test_a_step_with_a_name_and_an_output_already_added_is_refused_for_its_output(
    tmp_path: Path,
) -> None:
    """A python step whose name and output are both those of a step already added raises the WiringError for its output, not the ValueError for its name, and the message names the step, the step that produces the key, and the key."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")

    message = str(info.value)
    assert message.startswith("Step 'a':"), message
    # Once in the prefix as the refused step, once more as the step that produces the key.
    assert message.count("'a'") >= 2, message
    assert "'a.parquet'" in message, message
    assert [task.name for task in freeze(p).tasks] == ["a"]


def test_two_unnamed_steps_producing_one_key_are_refused_for_the_output(
    tmp_path: Path,
) -> None:
    """After an unnamed python step producing 'a.parquet', a second unnamed python step producing 'a.parquet', whose default name is also 'a.parquet', raises WiringError naming 'a.parquet' as already produced, not the ValueError that asks for a different name=."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet")

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="a.parquet")

    message = str(info.value)
    assert message.startswith("Step 'a.parquet':"), message
    # The default name is the key, so the prefix alone does not show the key is named.
    assert message.count("'a.parquet'") >= 2, message
    assert [task.name for task in freeze(p).tasks] == ["a.parquet"]


def test_a_second_producer_of_a_key_is_refused_naming_both_steps_and_the_key(
    tmp_path: Path,
) -> None:
    """After step 'first' produces 'shared.parquet', a python step 'second' producing 'shared.parquet' raises WiringError whose message names 'second', 'first' and 'shared.parquet'."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="shared.parquet", name="first")

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="shared.parquet", name="second")

    message = str(info.value)
    assert message.startswith("Step 'second':"), message
    assert "'first'" in message, message
    assert "'shared.parquet'" in message, message
    assert [task.name for task in freeze(p).tasks] == ["first"]


def test_a_notebook_steps_executed_notebook_counts_as_an_output(tmp_path: Path) -> None:
    """After a notebook step whose executed key is 'run.ipynb', a command step producing 'run.ipynb' is refused naming the notebook step and the key."""
    notebook = write_source_notebook(tmp_path / "report.ipynb")
    p = Pipeline(tmp_path / "out", "run1")
    p.add_notebook_step(notebook, executed="run.ipynb", env="notebook_env", name="report")

    with pytest.raises(WiringError) as info:
        p.add_command_step("touch {out}", outputs={"out": "run.ipynb"}, name="copy")

    message = str(info.value)
    assert message.startswith("Step 'copy':"), message
    assert "'report'" in message, message
    assert "'run.ipynb'" in message, message


def test_a_notebook_steps_outputs_keys_count_as_outputs(tmp_path: Path) -> None:
    """A notebook step whose outputs includes a key a python step already produces is refused naming the python step and the key."""
    notebook = write_source_notebook(tmp_path / "report.ipynb", "table = 'unset'")
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="table.csv", name="make_table")

    with pytest.raises(WiringError) as info:
        p.add_notebook_step(
            notebook,
            executed="report.ipynb",
            env="notebook_env",
            outputs={"table": "table.csv"},
            name="report",
        )

    message = str(info.value)
    assert message.startswith("Step 'report':"), message
    assert "'make_table'" in message, message
    assert "'table.csv'" in message, message


def test_every_outputs_key_of_a_command_step_counts_as_an_output(tmp_path: Path) -> None:
    """After a command step producing 'a.csv' and 'b.csv', a python step producing 'b.csv' is refused naming the command step and 'b.csv'."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_command_step("touch {a} {b}", outputs={"a": "a.csv", "b": "b.csv"}, name="pair")

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="b.csv", name="late")

    message = str(info.value)
    assert message.startswith("Step 'late':"), message
    assert "'pair'" in message, message
    assert "'b.csv'" in message, message


def test_a_simulation_steps_run_directory_key_counts_as_an_output(tmp_path: Path) -> None:
    """After a simulation step producing 'sims/toy', a second simulation step producing 'sims/toy' with another name is refused naming the first step and 'sims/toy'."""
    model = tmp_path / "model"
    arguments: dict[str, Any] = {
        "spec": write_spec(model / "spec.yaml"),
        "branches": write_branches(model / "branches.yaml"),
        "outputs": "sims/toy",
        "env": "simulation_env",
        "options": {"project": "proj_test", "no_slack": True},
    }
    p = Pipeline(tmp_path / "out", "run1")
    p.add_simulation_step(**arguments, name="toy")

    with pytest.raises(WiringError) as info:
        p.add_simulation_step(**arguments, name="again")

    message = str(info.value)
    assert message.startswith("Step 'again':"), message
    assert "'toy'" in message, message
    assert "'sims/toy'" in message, message


def test_a_refused_step_registers_nothing_and_is_not_added(tmp_path: Path) -> None:
    """A command step refused because one of its two outputs is already produced leaves its other output unregistered (registry.key_for of its path is None) and is not in the next pipeline's tasks."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.csv", name="first")

    with pytest.raises(WiringError):
        p.add_command_step("touch {a} {b}", outputs={"a": "a.csv", "b": "b.csv"}, name="pair")

    assert _registry_of(p).key_for(_registry_of(p).path_for("b.csv")) is None
    assert [task.name for task in freeze(p).tasks] == ["first"]


def test_a_step_its_step_function_rejects_is_not_added_so_its_name_stays_free(
    tmp_path: Path,
) -> None:
    """A python step named 'x' that raises for an unresolved input is not added, and a corrected python step named 'x' added afterwards is accepted and is the only task in the pipeline."""
    p = Pipeline(tmp_path / "out", "run1")
    with pytest.raises(WiringError):
        p.add_python_step(
            _increment, inputs={"source": "missing.parquet"}, outputs="x.parquet", name="x"
        )

    p.add_python_step(_constant, inputs={}, outputs="x.parquet", name="x")

    [task] = freeze(p).tasks
    assert task.name == "x"
    assert task.inputs == {}


# Freezing the pipeline into a snapshot


def test_freeze_returns_the_tasks_in_the_order_added_as_a_tuple(tmp_path: Path) -> None:
    """freeze(p).tasks is a tuple of the added steps' tasks in the order they were added."""
    p = Pipeline(tmp_path / "out", "run1")
    for name in ("c", "a", "b"):
        p.add_python_step(_constant, inputs={}, outputs=f"{name}.parquet", name=name)

    tasks = freeze(p).tasks

    assert isinstance(tasks, tuple)
    assert [task.name for task in tasks] == ["c", "a", "b"]


def test_the_snapshots_root_registry_and_resources_come_from_the_pipeline(
    tmp_path: Path,
) -> None:
    """freeze(p) has root p.state, the same registry as every other snapshot of p, and resources equal to the resources given to the pipeline."""
    resources = {"project": "proj_test", "memory": 8}
    p = Pipeline(tmp_path / "out", "run1", resources=resources)

    pipeline = freeze(p)

    assert pipeline.root == p.state
    assert pipeline.registry is freeze(p).registry
    assert dict(pipeline.resources) == {"project": "proj_test", "memory": 8}
    assert dict(p.resources) == {"project": "proj_test", "memory": 8}


def test_the_pipeline_does_not_hand_out_its_registry(tmp_path: Path) -> None:
    """A Pipeline has no public registry attribute, so a step function cannot be wired to it by hand without the pipeline knowing."""
    assert not hasattr(Pipeline(tmp_path / "out", "run1"), "registry")


def test_a_pipeline_without_resources_freezes_to_a_snapshot_with_empty_resources(
    tmp_path: Path,
) -> None:
    """A pipeline made without resources freezes to a snapshot whose resources are an empty mapping."""
    p = Pipeline(tmp_path / "out", "run1")

    pipeline = freeze(p)

    assert isinstance(pipeline.resources, Mapping)
    assert dict(pipeline.resources) == {}
    assert dict(p.resources) == {}


def test_a_step_added_after_freeze_does_not_reach_the_returned_snapshot(
    tmp_path: Path,
) -> None:
    """A snapshot returned before a third step is added still has two tasks, and a second call of freeze(p) has all three."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")
    p.add_python_step(_constant, inputs={}, outputs="b.parquet", name="b")
    earlier = freeze(p)

    p.add_python_step(_constant, inputs={}, outputs="c.parquet", name="c")

    assert [task.name for task in earlier.tasks] == ["a", "b"]
    assert [task.name for task in freeze(p).tasks] == ["a", "b", "c"]


def test_each_call_of_freeze_returns_a_new_equal_snapshot(tmp_path: Path) -> None:
    """Two calls of freeze(p) with no step added between them return two distinct _PipelineSnapshot objects with equal tasks, root, registry and resources."""
    p = Pipeline(tmp_path / "out", "run1", resources={"project": "proj_test"})
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")

    first = freeze(p)
    second = freeze(p)

    assert first is not second
    assert first.tasks == second.tasks
    assert first.root == second.root
    assert first.registry is second.registry
    assert dict(first.resources) == dict(second.resources)


def test_a_later_change_to_the_resources_passed_in_does_not_reach_a_snapshot(
    tmp_path: Path,
) -> None:
    """Changing the dict passed as resources after the pipeline is made changes neither p.resources nor the resources of a snapshot made before or after the change."""
    given: dict[str, Any] = {"project": "proj_a"}
    p = Pipeline(tmp_path / "out", "run1", resources=given)
    before = freeze(p)

    given["project"] = "proj_b"
    given["memory"] = 8
    after = freeze(p)

    for resources in (p.resources, before.resources, after.resources):
        assert dict(resources) == {"project": "proj_a"}


def test_changing_the_resources_a_pipeline_returns_does_not_reach_it(tmp_path: Path) -> None:
    """Changing the mapping p.resources returns, or the resources of a snapshot that freeze(p) returns, changes neither a later p.resources nor a later snapshot's resources."""
    p = Pipeline(tmp_path / "out", "run1", resources={"project": "proj_a"})

    returned = p.resources
    assert isinstance(returned, dict)
    returned["project"] = "proj_b"
    frozen = freeze(p).resources
    assert isinstance(frozen, dict)
    frozen["project"] = "proj_c"

    assert dict(p.resources) == {"project": "proj_a"}
    assert dict(freeze(p).resources) == {"project": "proj_a"}


def test_a_pipeline_with_no_steps_freezes_to_a_snapshot_with_no_tasks(tmp_path: Path) -> None:
    """freeze(p) before any step is added returns a _PipelineSnapshot whose tasks are an empty tuple."""
    pipeline = freeze(Pipeline(tmp_path / "out", "run1"))

    assert isinstance(pipeline, _PipelineSnapshot)
    assert pipeline.tasks == ()


def test_a_pipeline_runs_locally_and_writes_under_the_run_directory(
    tmp_path: Path,
) -> None:
    """run(p) of a two-step pipeline succeeds for both steps, writes the outputs under output_root/run_name, and writes pytask's state under output_root/.dagger/run_name."""
    output_root = (tmp_path / "out").resolve()
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")
    p.add_python_step(_increment, inputs={"source": "a.parquet"}, outputs="b.csv", name="b")

    result = run(p)

    assert result == {"a": TaskOutcome.SUCCESS, "b": TaskOutcome.SUCCESS}
    assert (output_root / "run1" / "a.parquet").is_file()
    assert (output_root / "run1" / "b.csv").is_file()
    assert (output_root / ".dagger" / "run1" / "pytask.lock").is_file()
    assert not (output_root / "run1" / "pytask.lock").exists()


# The frontend's exports


def test_the_frontend_exports_the_pipeline_builder_and_not_the_lower_layer() -> None:
    """vivarium.dagger.frontend.Pipeline is the builder class in vivarium.dagger.frontend.builder and is in __all__, and none of FileRegistry, python_step, notebook_step, command_step, simulation_step, _PipelineSnapshot and freeze is in __all__."""
    exported = vivarium.dagger.frontend.__all__

    assert vivarium.dagger.frontend.Pipeline is Pipeline
    assert Pipeline.__module__ == "vivarium.dagger.frontend.builder"
    assert "Pipeline" in exported
    lower_layer = (
        "FileRegistry",
        "python_step",
        "notebook_step",
        "command_step",
        "simulation_step",
        "_PipelineSnapshot",
        "freeze",
    )
    assert [name for name in lower_layer if name in exported] == []


def test_the_step_functions_and_the_registry_stay_importable_from_their_modules() -> None:
    """python_step, notebook_step, command_step and simulation_step import from their own frontend modules, and FileRegistry from vivarium.dagger.backend.wiring."""
    locations = {
        "python_step": "vivarium.dagger.frontend.python_step",
        "notebook_step": "vivarium.dagger.frontend.notebook_step",
        "command_step": "vivarium.dagger.frontend.command_step",
        "simulation_step": "vivarium.dagger.frontend.simulation_step",
        "FileRegistry": "vivarium.dagger.backend.wiring",
    }

    for name, module in locations.items():
        found = getattr(importlib.import_module(module), name)
        assert callable(found), (module, name)
        assert found.__module__ == module, (module, name)


# MIC-7810: one public Pipeline, frozen by each run

_GROWING: list[Pipeline] = []
"""The pipeline that :func:`_add_a_step` adds a step to, while a test puts one here."""


def _add_a_step() -> pd.DataFrame:
    """Add step 'added' to the pipeline in :data:`_GROWING` once; return a fixed table."""
    if _GROWING:
        _GROWING.pop().add_python_step(
            _constant, inputs={}, outputs="added.parquet", name="added"
        )
    return _constant()


def _two_steps(tmp_path: Path) -> Pipeline:
    """Return a pipeline of step 'a', producing 'a.parquet', and step 'b', which reads it."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")
    p.add_python_step(
        _increment, inputs={"source": "a.parquet"}, outputs="b.parquet", name="b"
    )
    return p


def test_freeze_of_a_snapshot_raises_type_error(tmp_path: Path) -> None:
    """freeze() takes only a Pipeline, so freeze() of a _PipelineSnapshot raises TypeError naming _PipelineSnapshot and saying a Pipeline is expected."""
    snapshot = freeze(Pipeline(tmp_path / "out", "run1"))
    given: Any = snapshot

    with pytest.raises(TypeError) as info:
        freeze(given)

    assert str(info.value) == "Expected a Pipeline, got _PipelineSnapshot."


@pytest.mark.parametrize("value", ["pipeline.py", None, ()])
def test_freeze_of_something_other_than_a_pipeline_raises_type_error_naming_its_type(
    value: object,
) -> None:
    """freeze() of a str, None or a tuple raises TypeError whose message names the type it got and says a Pipeline is expected."""
    given: Any = value

    with pytest.raises(TypeError) as info:
        freeze(given)

    message = str(info.value)
    assert type(value).__name__ in message, message
    assert "Pipeline" in message, message


def test_the_pipeline_has_no_pipeline_method_and_no_public_tasks(tmp_path: Path) -> None:
    """A Pipeline has no pipeline() method, so a definition returns the Pipeline itself, and exposes no public tasks attribute; its steps are read only through freeze()."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")

    assert not hasattr(Pipeline, "pipeline")
    assert not hasattr(p, "pipeline")
    assert not hasattr(p, "tasks")
    assert [task.name for task in freeze(p).tasks] == ["a"]


def test_workflow_no_longer_exists() -> None:
    """vivarium.dagger.frontend has no attribute Workflow, and the module vivarium.dagger.frontend.workflow cannot be imported."""
    assert not hasattr(vivarium.dagger.frontend, "Workflow")
    assert "Workflow" not in vivarium.dagger.frontend.__all__
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("vivarium.dagger.frontend.workflow")


def test_the_package_exports_no_snapshot_class() -> None:
    """vivarium.dagger.frontend has no attribute and no __all__ entry whose name holds 'Snapshot', the module vivarium.dagger.frontend.snapshot cannot be imported, and the private _PipelineSnapshot and freeze live in vivarium.dagger.frontend.builder, outside its __all__."""
    builder_module = importlib.import_module("vivarium.dagger.frontend.builder")

    assert [name for name in dir(vivarium.dagger.frontend) if "Snapshot" in name] == []
    assert [name for name in vivarium.dagger.frontend.__all__ if "Snapshot" in name] == []
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("vivarium.dagger.frontend.snapshot")
    assert _PipelineSnapshot.__module__ == builder_module.__name__
    assert freeze.__module__ == builder_module.__name__
    assert builder_module.__all__ == ["STATE_DIR", "Pipeline"]


def test_a_step_added_after_run_is_in_the_next_run_and_not_the_finished_one(
    tmp_path: Path,
) -> None:
    """run(p) of a two-step pipeline returns outcomes for those two steps; a third step added to p afterwards is not in that result, and a second run(p) returns SUCCESS for all three and writes the third step's output."""
    p = _two_steps(tmp_path)
    third = p.output_root / "run1" / "c.csv"

    finished = run(p)
    p.add_python_step(_increment, inputs={"source": "b.parquet"}, outputs="c.csv", name="c")

    assert finished == {"a": TaskOutcome.SUCCESS, "b": TaskOutcome.SUCCESS}
    assert not third.exists()
    assert run(p) == dict.fromkeys(("a", "b", "c"), TaskOutcome.SUCCESS)
    assert third.is_file()


def test_a_step_added_while_run_is_building_is_not_in_that_run(tmp_path: Path) -> None:
    """A python step whose function adds another step to the same module-level Pipeline while run(p) builds it does not change that run: its outcomes have only the steps p had when run(p) was called, the added step's output is not written, and the next run(p) runs the added step."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_add_a_step, inputs={}, outputs="grow.parquet", name="grow")
    added = p.output_root / "run1" / "added.parquet"
    _GROWING.append(p)
    try:
        finished = run(p)
    finally:
        _GROWING.clear()

    assert finished == {"grow": TaskOutcome.SUCCESS}
    # The step body added the step to p during the run.
    assert [task.name for task in freeze(p).tasks] == ["grow", "added"]
    assert not added.exists()
    assert run(p) == {"grow": TaskOutcome.SUCCESS, "added": TaskOutcome.SUCCESS}
    assert added.is_file()


def test_preview_of_a_pipeline_plans_the_steps_added_so_far(tmp_path: Path) -> None:
    """preview(p) returns one StepPreview per step added to p so far, in order, each would-run before the first run, with reasons naming file keys as _preview() does for a snapshot with the same registry."""
    root_inputs = _root_inputs(tmp_path)
    steps: list[tuple[Callable[..., pd.DataFrame], dict[str, Any]]] = [
        (_read_raw, {"inputs": {"raw": _RAW}, "outputs": "a.parquet", "name": "a"}),
        (
            _increment,
            {"inputs": {"source": "a.parquet"}, "outputs": "b.parquet", "name": "b"},
        ),
    ]
    p = Pipeline(tmp_path / "out", "run1", root_inputs=root_inputs)
    for target, arguments in steps:
        p.add_python_step(target, **arguments)
    registry = FileRegistry(tmp_path / "out", "run1", root_inputs=root_inputs)
    tasks = [
        python_step(target, **arguments, registry=registry) for target, arguments in steps
    ]
    by_hand = _PipelineSnapshot(tasks, tmp_path / "by_hand", registry)

    plan = preview(p)

    assert [step.name for step in plan] == ["a", "b"]
    assert all(step.would_run for step in plan), plan
    assert plan == _preview(by_hand)
    nodes = {reason.node for reason in plan[0].reasons}
    assert {"file 'raw.csv'", "file 'a.parquet'"} <= nodes, plan[0].reasons


def test_a_step_added_after_preview_is_in_the_next_preview_only(tmp_path: Path) -> None:
    """A plan returned by preview(p) before a step is added does not change when the step is added, and the next preview(p) has a StepPreview for the added step."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")
    plan = preview(p)
    before = copy.deepcopy(plan)

    p.add_python_step(
        _increment, inputs={"source": "a.parquet"}, outputs="b.parquet", name="b"
    )

    assert plan == before
    assert [step.name for step in plan] == ["a"]
    after = {step.name: step for step in preview(p)}
    assert list(after) == ["a", "b"]
    assert after["b"].would_run


def test_run_and_preview_of_a_pipeline_match_run_and_preview_of_its_snapshot(
    tmp_path: Path,
) -> None:
    """preview(p) and _preview(freeze(p)) return the same plan, and after run(p) a _run(freeze(p)) with nothing changed reports the same step names, so the public verbs read the snapshot that freeze() makes."""
    p = _two_steps(tmp_path)

    assert preview(p) == _preview(freeze(p))
    ran = run(p)
    again = _run(freeze(p))

    assert set(ran) == {"a", "b"}
    assert list(again) == list(ran)


@pytest.mark.parametrize("verb", ["run", "preview"])
def test_run_and_preview_of_something_other_than_a_pipeline_raise_type_error_and_write_nothing(
    tmp_path: Path, verb: str
) -> None:
    """run() and preview() of a list of tasks raise TypeError, as freeze() does, before anything is built, and leave no pytask state under tmp_path."""
    registry = FileRegistry(tmp_path / "out", "run1")
    tasks: Any = [python_step(_constant, inputs={}, outputs="a.parquet", registry=registry)]
    verbs: dict[str, Callable[..., object]] = {"run": run, "preview": preview}
    with pytest.raises(TypeError) as expected:
        freeze(tasks)

    with pytest.raises(TypeError) as raised:
        verbs[verb](tasks)

    assert str(raised.value) == str(expected.value)
    assert "list" in str(raised.value), str(raised.value)
    leftovers = [path for path in tmp_path.rglob("*") if "pytask" in path.name]
    assert leftovers == []
    assert not registry.path_for("a.parquet").exists()


def test_only_and_force_on_a_pipeline_name_the_steps_added_so_far(tmp_path: Path) -> None:
    """run(p, only=...) and run(p, force=...) accept the name of a step added to p before the call, and a step name added only after an earlier call is accepted by the next call; an unknown name raises PipelineError listing p's step names."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="a.parquet", name="a")
    assert run(p, only="a") == {"a": TaskOutcome.SUCCESS}
    with pytest.raises(PipelineError):
        run(p, force="b")

    p.add_python_step(
        _increment, inputs={"source": "a.parquet"}, outputs="b.parquet", name="b"
    )

    assert run(p, only="b")["b"] == TaskOutcome.SUCCESS
    assert run(p, force="b")["b"] == TaskOutcome.SUCCESS
    with pytest.raises(PipelineError) as info:
        run(p, only="c")
    message = str(info.value)
    assert "'a'" in message, message
    assert "'b'" in message, message


# The private snapshot


def test_snapshot_is_frozen_and_its_registry_defaults_to_none(tmp_path: Path) -> None:
    """_PipelineSnapshot's fields cannot be reassigned, and registry defaults to None."""
    registry = FileRegistry(tmp_path / "out", "run1")
    task = python_step(_constant, inputs={}, outputs="a.parquet", registry=registry)
    snapshot = _PipelineSnapshot([task], tmp_path / "state")

    assert snapshot.registry is None
    replacements: dict[str, object] = {
        "tasks": [],
        "root": tmp_path,
        "registry": registry,
    }
    for name, value in replacements.items():
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(snapshot, name, value)


def test_snapshot_stores_tasks_as_a_tuple_that_a_change_to_the_passed_list_does_not_reach(
    tmp_path: Path,
) -> None:
    """_PipelineSnapshot(tasks_list, root).tasks is a tuple equal to tuple(tasks_list), and appending to or clearing tasks_list afterwards leaves it unchanged."""
    registry = FileRegistry(tmp_path / "out", "run1")
    tasks_list = [
        python_step(_constant, inputs={}, outputs=key, registry=registry)
        for key in ("a.parquet", "b.parquet")
    ]
    expected = tuple(tasks_list)

    snapshot = _PipelineSnapshot(tasks_list, tmp_path / "state")

    assert isinstance(snapshot.tasks, tuple)
    assert snapshot.tasks == expected
    tasks_list.append(tasks_list[0])
    assert snapshot.tasks == expected
    tasks_list.clear()
    assert snapshot.tasks == expected


def test_snapshot_rejects_a_root_that_is_not_a_path(tmp_path: Path) -> None:
    """_PipelineSnapshot(tasks, str(tmp_path / 'state')) raises TypeError at construction, naming the type it got."""
    root: Any = str(tmp_path / "state")

    with pytest.raises(TypeError, match=r"\bstr\b"):
        _PipelineSnapshot([], root)


def test_snapshot_rejects_a_relative_root() -> None:
    """_PipelineSnapshot(tasks, Path('state')) raises ValueError at construction, saying the root must be absolute."""
    with pytest.raises(ValueError, match="(?i)absolute"):
        _PipelineSnapshot([], Path("state"))


def test_snapshot_resources_default_to_an_empty_mapping(tmp_path: Path) -> None:
    """_PipelineSnapshot(tasks, root) has resources equal to {}, and two snapshots made that way do not share one mapping object."""
    first = _PipelineSnapshot([], tmp_path / "first")
    second = _PipelineSnapshot([], tmp_path / "second")

    assert first.resources == {}
    assert second.resources == {}
    assert first.resources is not second.resources


def test_snapshot_keeps_the_resources_it_is_given(tmp_path: Path) -> None:
    """_PipelineSnapshot(tasks, root, registry, resources={'project': 'proj_a'}) has resources equal to {'project': 'proj_a'} and that registry."""
    registry = FileRegistry(tmp_path / "out", "run1")

    snapshot = _PipelineSnapshot(
        [], tmp_path / "state", registry, resources={"project": "proj_a"}
    )

    assert snapshot.resources == {"project": "proj_a"}
    assert snapshot.registry is registry


def test_snapshot_rejects_resources_that_are_not_a_mapping(tmp_path: Path) -> None:
    """_PipelineSnapshot(tasks, root, resources=[('project', 'proj_a')]) raises TypeError naming the type it got."""
    pairs: Any = [("project", "proj_a")]

    with pytest.raises(TypeError, match=r"\blist\b"):
        _PipelineSnapshot([], tmp_path / "state", resources=pairs)


# MIC-7810: add_task, the lower layer under the step methods


def _command_task(name: str, out: Path, command: str | None = None) -> Task:
    """Return a hand-built command task ``name`` whose one output is the file ``out``."""
    return Task(
        name=name,
        run=f"echo {name} > {out}" if command is None else command,
        inputs={},
        outputs={"out": file_node(out)},
        resources={},
        env=None,
    )


def test_add_task_appends_the_tasks_in_order_and_they_run(tmp_path: Path) -> None:
    """A python step added with the method, then two tasks compiled with python_step against another registry and added with add_task, the second reading the first's output, freeze in that order and all run with SUCCESS."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="method.parquet", name="method")
    registry = FileRegistry(tmp_path / "out", "run1")
    first = python_step(_constant, inputs={}, outputs="a.parquet", registry=registry)
    second = python_step(
        _increment, inputs={"source": "a.parquet"}, outputs="b.parquet", registry=registry
    )

    p.add_task(first)
    p.add_task(second)

    assert freeze(p).tasks[1:] == (first, second)
    assert [task.name for task in freeze(p).tasks] == ["method", "a.parquet", "b.parquet"]
    assert run(p) == dict.fromkeys(("method", "a.parquet", "b.parquet"), TaskOutcome.SUCCESS)
    frame = pd.read_parquet(registry.path_for("b.parquet"))
    assert frame["n"].tolist() == [2, 3, 4]


def test_add_task_refuses_a_name_a_step_method_added(tmp_path: Path) -> None:
    """After a python step named 'dup', add_task of a task named 'dup' with another output raises ValueError with the step methods' message, and the task is not added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="first.parquet", name="dup")

    with pytest.raises(ValueError) as info:
        p.add_task(_command_task("dup", tmp_path / "other.txt"))

    assert str(info.value) == (
        "Step 'dup': a step named 'dup' was already added to the pipeline; give each "
        "step a different name= to tell them apart."
    )
    assert [task.name for task in freeze(p).tasks] == ["dup"]


def test_a_step_method_refuses_a_name_add_task_added(tmp_path: Path) -> None:
    """After add_task of a task named 'dup', a python step named 'dup' with another output raises ValueError starting "Step 'dup':" that says to give each step a different name=, and is not added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_task(_command_task("dup", tmp_path / "first.txt"))

    with pytest.raises(ValueError) as info:
        p.add_python_step(_constant, inputs={}, outputs="second.parquet", name="dup")

    message = str(info.value)
    assert message.startswith("Step 'dup':"), message
    assert "name=" in message, message
    assert [task.name for task in freeze(p).tasks] == ["dup"]
    assert not _registered(p, "second.parquet")


def test_add_task_refuses_an_output_a_step_method_produces_naming_its_key(
    tmp_path: Path,
) -> None:
    """After a python step 'first' produces 'shared.parquet', add_task of a task 'second' whose output is that file, compiled against another registry, raises WiringError starting "Step 'second':" that names the key 'shared.parquet' and the step 'first', and the task is not added."""
    p = Pipeline(tmp_path / "out", "run1")
    p.add_python_step(_constant, inputs={}, outputs="shared.parquet", name="first")
    registry = FileRegistry(tmp_path / "out", "run1")
    task = python_step(
        _constant, inputs={}, outputs="shared.parquet", registry=registry, name="second"
    )

    with pytest.raises(WiringError) as info:
        p.add_task(task)

    message = str(info.value)
    assert message.startswith("Step 'second':"), message
    assert "output 'shared.parquet'" in message, message
    assert "'first'" in message, message
    assert [task.name for task in freeze(p).tasks] == ["first"]


def test_a_step_method_refuses_an_output_add_task_produces(tmp_path: Path) -> None:
    """After add_task of a task 'first' whose output is the file of the key 'shared.parquet', a python step 'second' producing 'shared.parquet' raises WiringError starting "Step 'second':" that names the key and 'first', and is not added."""
    p = Pipeline(tmp_path / "out", "run1")
    shared = p.output_root / "run1" / "shared.parquet"
    p.add_task(_command_task("first", shared))

    with pytest.raises(WiringError) as info:
        p.add_python_step(_constant, inputs={}, outputs="shared.parquet", name="second")

    message = str(info.value)
    assert message.startswith("Step 'second':"), message
    assert "'shared.parquet'" in message, message
    assert "'first'" in message, message
    assert [task.name for task in freeze(p).tasks] == ["first"]
    assert not _registered(p, "shared.parquet")


def test_add_task_refuses_an_output_another_added_task_produces_naming_its_path(
    tmp_path: Path,
) -> None:
    """add_task of a task 'second' whose output is an unresolved PathNode on another spelling of the file of an added task 'first' raises WiringError starting "Step 'second':" that names the resolved path and 'first'; with its name also taken, the error is still the WiringError."""
    p = Pipeline(tmp_path / "out", "run1")
    out = tmp_path / "out.txt"
    p.add_task(_command_task("first", out))
    (tmp_path / "sub").mkdir()
    other_spelling = PathNode(path=tmp_path / "sub" / ".." / "out.txt")

    for name in ("second", "first"):
        task = Task(
            name=name,
            run=f"echo {name} > {out}",
            inputs={},
            outputs={"out": other_spelling},
            resources={},
            env=None,
        )
        with pytest.raises(WiringError) as info:
            p.add_task(task)

        message = str(info.value)
        assert message.startswith(f"Step {name!r}:"), message
        assert repr(str(out.resolve())) in message, message
        assert "step 'first'" in message, message
    assert [task.name for task in freeze(p).tasks] == ["first"]


def test_add_task_does_not_compare_outputs_that_are_not_path_nodes(tmp_path: Path) -> None:
    """Two tasks whose only outputs are PythonNodes of one name are both added."""
    p = Pipeline(tmp_path / "out", "run1")
    tasks = [
        Task(
            name=name,
            run="true",
            inputs={},
            outputs={"value": PythonNode(name="value")},
            resources={},
            env=None,
        )
        for name in ("a", "b")
    ]

    for task in tasks:
        p.add_task(task)

    assert freeze(p).tasks == tuple(tasks)


def test_a_hand_built_task_with_a_shell_command_runs_through_run(tmp_path: Path) -> None:
    """A backend Task built by hand, whose shell command copies a file node it reads to the file node it writes, added with add_task, runs with SUCCESS through run() and writes its output."""
    source = tmp_path / "source.txt"
    source.write_text("hello\n")
    target = tmp_path / "target.txt"
    task = Task(
        name="copy",
        run=f"cp {source} {target}",
        inputs={"source": file_node(source)},
        outputs={"target": file_node(target)},
        resources={},
        env=None,
    )
    p = Pipeline(tmp_path / "out", "run1")
    p.add_task(task)

    assert run(p) == {"copy": TaskOutcome.SUCCESS}
    assert target.read_text() == "hello\n"


def test_preview_names_the_files_of_an_added_task_by_path(tmp_path: Path) -> None:
    """preview(p) of a hand-built task added with add_task names its output by the file's path, not as "file '<key>'", and still names the step's command in step terms."""
    out = tmp_path / "out.txt"
    p = Pipeline(tmp_path / "out", "run1")
    p.add_task(_command_task("touch", out))

    [step] = preview(p)

    nodes = {reason.node for reason in step.reasons}
    assert out.resolve().as_posix() in nodes, step.reasons
    assert "command of 'touch'" in nodes, step.reasons
    assert not any(node.startswith("file ") for node in nodes), step.reasons


# --- Messages (MIC-7848) ---


def test_add_command_step_and_add_python_step_with_no_name_and_no_outputs_report_their_own_names(
    tmp_path: Path,
) -> None:
    """Pipeline.add_command_step and add_python_step (mapping form) with no name and empty outputs raise ValueErrors starting 'add_command_step:' and 'add_python_step:'."""
    p = Pipeline(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as command:
        p.add_command_step("true", outputs={})
    with pytest.raises(ValueError) as python:
        p.add_python_step(_split, inputs={}, outputs={})

    assert str(command.value).startswith("add_command_step:"), str(command.value)
    assert str(python.value).startswith("add_python_step:"), str(python.value)


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_every_add_method_reports_an_empty_name_in_one_form(
    tmp_path: Path, kind: str
) -> None:
    """Each of add_python_step, add_command_step, add_notebook_step and add_simulation_step called with name='' raises ValueError with the message "add_<kind>_step: name must be a non-empty string, got ''.", and no message contains 'Task name' or "Step ''"; nothing is added to the pipeline."""
    p = _pipeline(tmp_path)
    arguments = _arguments(kind, tmp_path, "only", name="")

    with pytest.raises(ValueError) as info:
        _add(p, kind, arguments)

    message = str(info.value)
    assert message == f"add_{kind}_step: name must be a non-empty string, got ''.", message
    assert "Task name" not in message, message
    assert "Step ''" not in message, message
    assert freeze(p).tasks == ()
    for key in _output_keys(kind, arguments):
        assert not _registered(p, key), key


# A run directory behind a symbolic link


_LINKS_FOLLOWED = "with symbolic links followed"
"""The words of a refusal that names a run directory's real location."""


def _directory(path: Path) -> Path:
    """Create the directory ``path`` and its parents, and return it."""
    path.mkdir(parents=True)
    return path


def _link(link: Path, target: Path | str) -> None:
    """Make ``link`` a symbolic link to ``target``, creating the directory that holds it."""
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)


def _refusal(tmp_path: Path, run_name: str) -> str:
    """Return the message of the ValueError that Pipeline(tmp_path / "out", run_name) raises."""
    with pytest.raises(ValueError) as info:
        Pipeline(tmp_path / "out", run_name)
    return str(info.value)


def test_a_run_directory_that_links_outside_the_output_root_is_refused(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/run1 a symbolic link to the existing directory tmp_path/elsewhere, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1'), the path tmp_path.resolve()/out/run1, the path tmp_path.resolve()/elsewhere and the words 'with symbolic links followed'."""
    root = tmp_path.resolve()
    _link(tmp_path / "out" / "run1", _directory(tmp_path / "elsewhere"))

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert str(root / "out" / "run1") in message, message
    assert str(root / "elsewhere") in message, message
    assert _LINKS_FOLLOWED in message, message


def test_a_run_directory_that_links_into_the_state_folder_is_refused(tmp_path: Path) -> None:
    """With tmp_path/out/.dagger/x a directory and tmp_path/out/run1 a symbolic link to it, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1'), the path tmp_path.resolve()/out/.dagger/x and the words 'with symbolic links followed'."""
    _link(tmp_path / "out" / "run1", _directory(tmp_path / "out" / ".dagger" / "x"))

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert str(tmp_path.resolve() / "out" / ".dagger" / "x") in message, message
    assert _LINKS_FOLLOWED in message, message


def test_a_run_directory_that_links_to_the_state_folder_itself_is_refused(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/.dagger a directory and tmp_path/out/run1 a symbolic link to it, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1') and the path tmp_path.resolve()/out/.dagger."""
    _link(tmp_path / "out" / "run1", _directory(tmp_path / "out" / ".dagger"))

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert str(tmp_path.resolve() / "out" / ".dagger") in message, message


def test_a_run_directory_that_links_inside_the_output_root_is_accepted(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/real a directory and tmp_path/out/run1 a symbolic link to it, made in one case with the absolute target and in another with the relative target 'real', Pipeline(tmp_path / 'out', 'run1') is made; its run_name is 'run1', its state is tmp_path.resolve()/out/.dagger/run1, and its registry puts the output key 'x.csv' at tmp_path.resolve()/out/real/x.csv."""
    out = tmp_path.resolve() / "out"
    real = _directory(tmp_path / "out" / "real")
    link = tmp_path / "out" / "run1"

    for target in (real, "real"):
        _link(link, target)

        p = Pipeline(tmp_path / "out", "run1")

        assert p.run_name == "run1"
        assert p.state == out / ".dagger" / "run1"
        assert _registry_of(p).path_for("x.csv") == out / "real" / "x.csv"
        link.unlink()


def test_a_run_name_whose_parent_links_outside_the_output_root_is_refused(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/study a symbolic link to the existing directory tmp_path/elsewhere, Pipeline(tmp_path / 'out', 'study/run1') raises ValueError whose message contains repr('study/run1') and the path tmp_path.resolve()/elsewhere/run1."""
    _link(tmp_path / "out" / "study", _directory(tmp_path / "elsewhere"))

    message = _refusal(tmp_path, "study/run1")

    assert repr("study/run1") in message, message
    assert str(tmp_path.resolve() / "elsewhere" / "run1") in message, message


def test_a_link_before_dot_dot_in_the_run_name_is_followed_as_the_os_follows_it(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/link a symbolic link to the existing directory tmp_path/elsewhere/deep, Pipeline(tmp_path / 'out', 'link/../x') raises ValueError whose message contains repr('link/../x') and the path tmp_path.resolve()/elsewhere/x, although tmp_path/out/x, the run directory with '..' collapsed, is inside the output root."""
    _link(tmp_path / "out" / "link", _directory(tmp_path / "elsewhere" / "deep"))

    message = _refusal(tmp_path, "link/../x")

    assert repr("link/../x") in message, message
    assert str(tmp_path.resolve() / "elsewhere" / "x") in message, message


def test_a_chain_of_links_is_followed_to_its_end(tmp_path: Path) -> None:
    """With tmp_path/out/run1 a symbolic link to tmp_path/out/hop, and tmp_path/out/hop a symbolic link to the existing directory tmp_path/elsewhere, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1') and the path tmp_path.resolve()/elsewhere."""
    _link(tmp_path / "out" / "hop", _directory(tmp_path / "elsewhere"))
    _link(tmp_path / "out" / "run1", tmp_path / "out" / "hop")

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert str(tmp_path.resolve() / "elsewhere") in message, message


def test_a_dangling_link_outside_the_output_root_is_refused(tmp_path: Path) -> None:
    """With tmp_path/out/run1 a symbolic link to tmp_path/missing, which does not exist, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1') and the path tmp_path.resolve()/missing."""
    _link(tmp_path / "out" / "run1", tmp_path / "missing")

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert str(tmp_path.resolve() / "missing") in message, message


def test_a_run_directory_that_links_to_the_output_root_itself_is_refused(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/run1 a symbolic link whose target is '.', so that it is the output root itself, Pipeline(tmp_path / 'out', 'run1') raises ValueError whose message contains repr('run1') and the words 'with symbolic links followed'."""
    _link(tmp_path / "out" / "run1", ".")

    message = _refusal(tmp_path, "run1")

    assert repr("run1") in message, message
    assert _LINKS_FOLLOWED in message, message


def test_a_dangling_link_inside_the_output_root_is_accepted(tmp_path: Path) -> None:
    """With tmp_path/out/run1 a symbolic link to tmp_path/out/missing, which does not exist, Pipeline(tmp_path / 'out', 'run1') is made, and its registry puts the output key 'x.csv' at tmp_path.resolve()/out/missing/x.csv."""
    _link(tmp_path / "out" / "run1", tmp_path / "out" / "missing")

    p = Pipeline(tmp_path / "out", "run1")

    expected = tmp_path.resolve() / "out" / "missing" / "x.csv"
    assert _registry_of(p).path_for("x.csv") == expected


def test_a_run_directory_that_is_a_link_loop_is_accepted(tmp_path: Path) -> None:
    """With tmp_path/out a directory and tmp_path/out/run1 a symbolic link to itself (os.symlink(tmp_path / 'out' / 'run1', tmp_path / 'out' / 'run1')), Pipeline(tmp_path / 'out', 'run1') is accepted when the Pipeline is made: it raises nothing, its run_name is 'run1' and its state is tmp_path.resolve()/out/.dagger/run1. The same holds for the loop made with the relative target 'run1'."""
    link = tmp_path / "out" / "run1"

    for target in (link, "run1"):
        _link(link, target)

        p = Pipeline(tmp_path / "out", "run1")

        assert p.run_name == "run1"
        assert p.state == tmp_path.resolve() / "out" / ".dagger" / "run1"
        link.unlink()


def test_an_output_root_given_through_a_link_is_accepted(tmp_path: Path) -> None:
    """With tmp_path/rootlink a symbolic link to the directory tmp_path/out, tmp_path/out/real a directory and tmp_path/out/run1 a symbolic link to tmp_path/rootlink/real, Pipeline(tmp_path / 'rootlink', 'run1') is made and its output_root is tmp_path.resolve()/out."""
    _directory(tmp_path / "out" / "real")
    _link(tmp_path / "rootlink", tmp_path / "out")
    _link(tmp_path / "out" / "run1", tmp_path / "rootlink" / "real")

    p = Pipeline(tmp_path / "rootlink", "run1")

    assert p.output_root == tmp_path.resolve() / "out"


def test_a_run_name_inside_the_target_of_a_linked_state_folder_is_refused(
    tmp_path: Path,
) -> None:
    """With tmp_path/out/statedir a directory and tmp_path/out/.dagger a symbolic link to it, Pipeline(tmp_path / 'out', 'statedir/run1'), whose run name has no link in it, raises ValueError whose message contains repr('statedir/run1'), the path tmp_path.resolve()/out/statedir and the words 'with symbolic links followed', and does not contain 'through a symbolic link'."""
    _link(tmp_path / "out" / ".dagger", _directory(tmp_path / "out" / "statedir"))

    message = _refusal(tmp_path, "statedir/run1")

    assert repr("statedir/run1") in message, message
    assert str(tmp_path.resolve() / "out" / "statedir") in message, message
    assert _LINKS_FOLLOWED in message, message
    assert "through a symbolic link" not in message, message


# Step order is not checked when a step is added


def test_add_task_accepts_a_consumer_before_its_producer_when_it_is_added(
    tmp_path: Path,
) -> None:
    """p.add_task of the hand-built command task 'consumer', which reads tmp_path/a.txt, and then of 'producer', which writes that file, raises nothing, and freeze(p).tasks names consumer and then producer: the order is checked when the pipeline is built, not when a step is added."""
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    consumer = Task(
        name="consumer",
        run=f"cp {a} {b}",
        inputs={"source": file_node(a)},
        outputs={"out": file_node(b)},
        resources={},
        env=None,
    )
    p = Pipeline(tmp_path / "out", "run1")

    p.add_task(consumer)
    p.add_task(_command_task("producer", a))

    assert [task.name for task in freeze(p).tasks] == ["consumer", "producer"]
