"""Tests for the Workflow authoring object (MIC-7805)."""

import importlib
import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import PathNode, TaskOutcome

import vivarium.dagger.frontend
from tests.bridge.helpers import lockfile_entries
from tests.frontend.notebooks import write_source_notebook
from tests.frontend.simulations import write_branches, write_spec
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry, WiringError
from vivarium.dagger.frontend import Pipeline, run
from vivarium.dagger.frontend.command_step import command_step
from vivarium.dagger.frontend.notebook_step import notebook_step
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.frontend.simulation_step import simulation_step
from vivarium.dagger.frontend.workflow import STATE_DIR, Workflow

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


def _workflow(tmp_path: Path) -> Workflow:
    """Return a workflow under ``tmp_path / "out"`` whose root inputs give ``raw.csv``."""
    return Workflow(tmp_path / "out", "run1", root_inputs=_root_inputs(tmp_path))


def _arguments(kind: str, tmp_path: Path, stem: str, name: str) -> dict[str, Any]:
    """Return every argument but ``registry`` of a step of ``kind`` named ``name``.

    The step reads the root input ``raw.csv``, and its products are named after ``stem``.
    """
    resources = {"memory": 2}
    if kind == "python":
        return {
            "target": _read_raw,
            "inputs": {"raw": _RAW},
            "produces": f"{stem}.parquet",
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
            "produces": {"table": f"{stem}_table.csv"},
            "parameters": {"location": "here"},
            "resources": resources,
            "name": name,
        }
    if kind == "command":
        return {
            "command": "cp {raw} {copy}",
            "produces": {"copy": f"{stem}.csv"},
            "inputs": {"raw": _RAW},
            "env": "command_env",
            "resources": resources,
            "name": name,
        }
    model = tmp_path / "model"
    return {
        "spec": write_spec(model / "spec.yaml"),
        "branches": write_branches(model / "branches.yaml"),
        "produces": f"sims/{stem}",
        "env": "simulation_env",
        "options": {"project": "proj_test", "no_slack": True},
        "artifact": _RAW,
        "resources": resources,
        "name": name,
    }


def _products(kind: str, arguments: Mapping[str, Any]) -> list[str]:
    """Return the product keys of a step of ``kind`` with ``arguments``."""
    if kind == "notebook":
        return [arguments["executed"], *arguments["produces"].values()]
    if kind == "command":
        return list(arguments["produces"].values())
    return [arguments["produces"]]


def _add(wf: Workflow, kind: str, arguments: Mapping[str, Any]) -> object:
    """Add a step of ``kind`` to ``wf``; return what its method returns."""
    method: Callable[..., object] = getattr(wf, f"{kind}_step")
    return method(**arguments)


def _registered(wf: Workflow, key: str) -> bool:
    """Return whether ``key``'s path is registered in ``wf``'s registry."""
    return wf.registry.key_for(wf.registry.path_for(key)) is not None


# Construction and the run's paths


def test_a_workflow_holds_its_output_root_run_name_registry_and_state(tmp_path: Path) -> None:
    """Workflow(tmp_path / "out", "run1") has output_root tmp_path/out resolved, run_name 'run1', an ArtifactRegistry whose path_for('x.csv') is tmp_path/out/run1/x.csv, and state tmp_path/out/.dagger/run1."""
    output_root = (tmp_path / "out").resolve()

    wf = Workflow(tmp_path / "out", "run1")

    assert wf.output_root == output_root
    assert wf.run_name == "run1"
    assert isinstance(wf.registry, ArtifactRegistry)
    assert wf.registry.path_for("x.csv") == output_root / "run1" / "x.csv"
    assert STATE_DIR == ".dagger"
    assert wf.state == output_root / ".dagger" / "run1"
    assert wf.state.is_absolute()


def test_a_relative_output_root_is_resolved_against_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the current directory at tmp_path, Workflow("out", "run1") has output_root tmp_path/out and the absolute default state tmp_path/out/.dagger/run1, which stay the same after the current directory changes."""
    output_root = (tmp_path / "out").resolve()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)

    wf = Workflow("out", "run1")
    monkeypatch.chdir(elsewhere)

    assert wf.output_root == output_root
    assert wf.state == output_root / ".dagger" / "run1"
    assert wf.state.is_absolute()
    assert wf.registry.path_for("x.csv") == output_root / "run1" / "x.csv"
    assert wf.pipeline().root == output_root / ".dagger" / "run1"


def test_an_explicit_absolute_state_is_used_as_given(tmp_path: Path) -> None:
    """Workflow(tmp_path / "out", "run1", state=tmp_path / "elsewhere") has state tmp_path/elsewhere, and its pipeline's root is that directory."""
    wf = Workflow(tmp_path / "out", "run1", state=tmp_path / "elsewhere")

    assert wf.state == tmp_path / "elsewhere"
    assert wf.pipeline().root == tmp_path / "elsewhere"


def test_an_explicit_state_given_as_a_string_is_accepted(tmp_path: Path) -> None:
    """state=str(tmp_path / "elsewhere") gives the Path tmp_path/elsewhere as state."""
    wf = Workflow(tmp_path / "out", "run1", state=str(tmp_path / "elsewhere"))

    assert isinstance(wf.state, Path)
    assert wf.state == tmp_path / "elsewhere"


def test_a_relative_state_is_refused_naming_the_path(tmp_path: Path) -> None:
    """state=Path("relative/state") raises ValueError whose message names 'relative/state' and says the state must be absolute."""
    with pytest.raises(ValueError) as info:
        Workflow(tmp_path / "out", "run1", state=Path("relative/state"))

    message = str(info.value)
    assert "relative/state" in message, message
    assert "absolute" in message, message


def test_the_run_name_dot_dagger_is_refused(tmp_path: Path) -> None:
    """Workflow(tmp_path, ".dagger") raises ValueError whose message names the run name '.dagger'."""
    with pytest.raises(ValueError) as info:
        Workflow(tmp_path, ".dagger")

    assert "'.dagger'" in str(info.value), str(info.value)


@pytest.mark.parametrize(
    "run_name", ["", ".", "..", "../outside", "/absolute/run", ".dagger/run1", "a/../.dagger"]
)
def test_a_run_name_whose_output_directory_is_not_a_separate_directory_under_the_root_is_refused(
    tmp_path: Path, run_name: str
) -> None:
    """A run name whose output directory, with . and .. collapsed (symlinks are not followed), is not strictly inside the output root, or is the .dagger directory or inside it, raises ValueError naming the run name."""
    with pytest.raises(ValueError) as info:
        Workflow(tmp_path, run_name)

    assert repr(run_name) in str(info.value), str(info.value)


def test_a_nested_run_name_is_accepted(tmp_path: Path) -> None:
    """Workflow(tmp_path, "study/run1") is accepted, with products under tmp_path/study/run1 and state tmp_path/.dagger/study/run1."""
    root = tmp_path.resolve()

    wf = Workflow(tmp_path, "study/run1")

    assert wf.registry.path_for("x.csv") == root / "study" / "run1" / "x.csv"
    assert wf.state == root / ".dagger" / "study" / "run1"


def test_a_run_directory_that_is_a_symlink_is_accepted(tmp_path: Path) -> None:
    """With tmp_path/out/run1 a symlink to tmp_path/elsewhere, Workflow(tmp_path / "out", "run1") is accepted, its state is tmp_path/out/.dagger/run1 and its products resolve under tmp_path/elsewhere."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "run1").symlink_to(elsewhere, target_is_directory=True)

    wf = Workflow(tmp_path / "out", "run1")

    assert wf.run_name == "run1"
    assert wf.state == (tmp_path / "out").resolve() / ".dagger" / "run1"
    assert wf.registry.path_for("x.csv") == elsewhere.resolve() / "x.csv"


def test_resources_that_are_not_a_mapping_raise_type_error(tmp_path: Path) -> None:
    """resources=[("project", "proj")] raises TypeError when the workflow is made."""
    resources: Any = [("project", "proj")]

    with pytest.raises(TypeError):
        Workflow(tmp_path, "run1", resources=resources)


def test_root_inputs_are_readable_by_a_step(tmp_path: Path) -> None:
    """A root input given to the workflow can be named as a step's input, and the task's input node is at the root input's file."""
    raw = _root_inputs(tmp_path)[_RAW]
    wf = Workflow(tmp_path / "out", "run1", root_inputs={_RAW: raw})

    wf.python_step(_read_raw, inputs={"raw": _RAW}, produces="copy.parquet")

    [task] = wf.pipeline().tasks
    node = task.inputs["raw"]
    assert isinstance(node, PathNode)
    assert node.path == raw.resolve()


def test_two_workflows_with_different_run_names_write_under_different_paths(
    tmp_path: Path,
) -> None:
    """Two workflows under one output root with run names 'a' and 'b' resolve the same key to tmp_path/a/<key> and tmp_path/b/<key>, and running both pipelines locally writes both files."""
    root = tmp_path.resolve()
    workflows = {name: Workflow(tmp_path, name) for name in ("a", "b")}

    for name, wf in workflows.items():
        wf.python_step(_constant, inputs={}, produces="table.parquet")
        assert wf.registry.path_for("table.parquet") == root / name / "table.parquet"
    for wf in workflows.values():
        assert run(wf.pipeline()) == {"table.parquet": TaskOutcome.SUCCESS}

    assert (root / "a" / "table.parquet").is_file()
    assert (root / "b" / "table.parquet").is_file()


def test_two_workflows_with_different_run_names_have_separate_state_directories(
    tmp_path: Path,
) -> None:
    """Two workflows under one output root with run names 'a' and 'b' have states tmp_path/.dagger/a and tmp_path/.dagger/b, and after a local run of each, each state directory holds its own pytask.lock with only its own steps."""
    root = tmp_path.resolve()
    first = Workflow(tmp_path, "a")
    second = Workflow(tmp_path, "b")
    first.python_step(_constant, inputs={}, produces="table.parquet", name="first")
    second.python_step(_constant, inputs={}, produces="table.parquet", name="second")

    assert first.state == root / ".dagger" / "a"
    assert second.state == root / ".dagger" / "b"
    assert run(first.pipeline()) == {"first": TaskOutcome.SUCCESS}
    assert run(second.pipeline()) == {"second": TaskOutcome.SUCCESS}

    assert set(lockfile_entries(first.state)) == {"first"}
    assert set(lockfile_entries(second.state)) == {"second"}


# Adding steps


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_method_adds_the_task_its_step_function_compiles(
    tmp_path: Path, kind: str
) -> None:
    """Each of python_step, notebook_step, command_step and simulation_step, given every argument of its step function except registry, adds a task equal to the one the step function compiles with the same arguments against a registry made from the same output root, run name and root inputs."""
    root_inputs = _root_inputs(tmp_path)
    wf = Workflow(tmp_path / "out", "run1", root_inputs=root_inputs)
    registry = ArtifactRegistry(tmp_path / "out", "run1", root_inputs=root_inputs)
    arguments = _arguments(kind, tmp_path, "only", name=f"{kind}_step")

    _add(wf, kind, arguments)

    expected = _STEP_FUNCTIONS[kind](**arguments, registry=registry)
    assert list(wf.pipeline().tasks) == [expected]


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_methods_parameters_are_its_step_functions_without_registry(
    kind: str,
) -> None:
    """Each step method's parameters after self are its step function's parameters without registry, in order, with the same names, kinds, defaults and annotations."""
    function = inspect.signature(_STEP_FUNCTIONS[kind], eval_str=True)
    method = inspect.signature(getattr(Workflow, f"{kind}_step"), eval_str=True)

    expected = [param for param in function.parameters.values() if param.name != "registry"]
    actual = [param for param in method.parameters.values() if param.name != "self"]
    assert actual == expected


def test_a_pipeline_of_all_four_step_kinds_matches_one_assembled_by_hand(
    tmp_path: Path,
) -> None:
    """A workflow of a python step, a notebook step reading its product, a command step and a simulation step, each with resources and a name, gives wf.pipeline().tasks equal, in order, to the tasks compiled by hand against an ArtifactRegistry and passed to Pipeline, with the same root, resources and registry keys."""
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
                "produces": "population.parquet",
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
                "produces": {"copy": "copy.parquet"},
                "resources": {"queue": "long.q"},
                "name": "copy",
            },
        ),
        (
            "simulation",
            {
                "spec": write_spec(model / "spec.yaml"),
                "branches": write_branches(model / "branches.yaml"),
                "produces": "sims/toy",
                "env": "simulation_env",
                "options": {"project": "proj_test", "no_slack": True},
                "resources": {"runtime": 7200},
                "name": "simulate",
            },
        ),
    ]
    resources = {"project": "proj_test"}
    wf = Workflow(tmp_path / "out", "run1", resources=resources)
    for kind, arguments in steps:
        _add(wf, kind, arguments)

    built = wf.pipeline()

    registry = ArtifactRegistry(tmp_path / "out", "run1")
    tasks = [
        _STEP_FUNCTIONS[kind](**arguments, registry=registry) for kind, arguments in steps
    ]
    state = (tmp_path / "out").resolve() / ".dagger" / "run1"
    by_hand = Pipeline(tasks, state, registry, resources=resources)
    assert list(built.tasks) == list(by_hand.tasks)
    names = [task.name for task in built.tasks]
    assert names == ["population", "report", "copy", "simulate"]
    assert built.root == by_hand.root
    assert dict(built.resources) == dict(by_hand.resources)
    built_registry = built.registry
    assert built_registry is not None
    assert built_registry is wf.registry
    for task in built.tasks:
        for node in (*task.inputs.values(), *task.outputs.values()):
            if isinstance(node, PathNode):
                path = node.path
                assert isinstance(path, Path), node
                assert built_registry.key_for(path) == registry.key_for(path), node


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_each_step_method_returns_none(tmp_path: Path, kind: str) -> None:
    """Each step method returns None."""
    wf = _workflow(tmp_path)

    returned = _add(wf, kind, _arguments(kind, tmp_path, "only", name="only"))

    assert returned is None


def test_a_step_method_keeps_the_step_functions_messages(tmp_path: Path) -> None:
    """wf.python_step with an input key no step produces raises the same WiringError message that python_step raises for the same arguments against an equal registry."""
    producer: dict[str, Any] = {"inputs": {}, "produces": "population.parquet"}
    consumer: dict[str, Any] = {
        "inputs": {"source": "populaton.parquet"},
        "produces": "b.parquet",
    }
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    python_step(_constant, registry=registry, **producer)
    with pytest.raises(WiringError) as expected:
        python_step(_increment, registry=registry, **consumer)
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, **producer)

    with pytest.raises(WiringError) as raised:
        wf.python_step(_increment, **consumer)

    assert str(raised.value) == str(expected.value)


def test_a_consumer_added_before_its_producer_is_refused(tmp_path: Path) -> None:
    """Adding a python step that reads 'a.parquet' before the step producing 'a.parquet' raises WiringError naming the step and the key as unresolved."""
    wf = Workflow(tmp_path / "out", "run1")

    with pytest.raises(WiringError) as info:
        wf.python_step(
            _increment, inputs={"source": "a.parquet"}, produces="b.parquet", name="b"
        )

    message = str(info.value)
    assert message.startswith("Step 'b':"), message
    assert "input 'a.parquet' is unresolved" in message, message


def test_a_command_step_with_several_products_and_no_name_raises_the_step_functions_error(
    tmp_path: Path,
) -> None:
    """wf.command_step with two produces keys and no name raises command_step's ValueError, which starts with 'command_step:' and says to pass name=."""
    wf = Workflow(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        wf.command_step("touch {a} {b}", produces={"a": "a.csv", "b": "b.csv"})

    message = str(info.value)
    assert message.startswith("command_step:"), message
    assert "pass name=" in message, message
    assert wf.pipeline().tasks == ()


# Refusing a second step with a name or a product already added


@pytest.mark.parametrize("kind", _STEP_KINDS)
def test_a_second_step_with_a_name_already_added_is_refused(
    tmp_path: Path, kind: str
) -> None:
    """After a python step named 'dup', adding a step of each kind named 'dup' with different products raises ValueError starting with "Step 'dup':" that says to give each step a different name=."""
    wf = _workflow(tmp_path)
    wf.python_step(_constant, inputs={}, produces="first.parquet", name="dup")
    arguments = _arguments(kind, tmp_path, "second", name="dup")

    with pytest.raises(ValueError) as info:
        _add(wf, kind, arguments)

    message = str(info.value)
    assert message.startswith("Step 'dup':"), message
    assert "different" in message, message
    assert "name=" in message, message
    for key in _products(kind, arguments):
        assert not _registered(wf, key), key
    assert [task.name for task in wf.pipeline().tasks] == ["dup"]


def test_a_step_whose_default_name_is_a_name_already_added_is_refused(tmp_path: Path) -> None:
    """After a step named 'x.csv' that produces 'other.csv', a python step producing 'x.csv' with no name, whose default name is 'x.csv', raises ValueError naming 'x.csv' as a name already added."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="other.csv", name="x.csv")

    with pytest.raises(ValueError) as info:
        wf.python_step(_constant, inputs={}, produces="x.csv")

    message = str(info.value)
    assert message.startswith("Step 'x.csv':"), message
    assert "name=" in message, message


def test_a_step_with_a_name_and_a_product_already_added_is_refused_for_its_product(
    tmp_path: Path,
) -> None:
    """A python step whose name and product are both those of a step already added raises the WiringError for its product, not the ValueError for its name, and the message names the step, the step that produces the key, and the key."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="a.parquet", name="a")

    with pytest.raises(WiringError) as info:
        wf.python_step(_constant, inputs={}, produces="a.parquet", name="a")

    message = str(info.value)
    assert message.startswith("Step 'a':"), message
    # Once in the prefix as the refused step, once more as the step that produces the key.
    assert message.count("'a'") >= 2, message
    assert "'a.parquet'" in message, message
    assert [task.name for task in wf.pipeline().tasks] == ["a"]


def test_two_unnamed_steps_producing_one_key_are_refused_for_the_product(
    tmp_path: Path,
) -> None:
    """After an unnamed python step producing 'a.parquet', a second unnamed python step producing 'a.parquet', whose default name is also 'a.parquet', raises WiringError naming 'a.parquet' as already produced, not the ValueError that asks for a different name=."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="a.parquet")

    with pytest.raises(WiringError) as info:
        wf.python_step(_constant, inputs={}, produces="a.parquet")

    message = str(info.value)
    assert message.startswith("Step 'a.parquet':"), message
    # The default name is the key, so the prefix alone does not show the key is named.
    assert message.count("'a.parquet'") >= 2, message
    assert [task.name for task in wf.pipeline().tasks] == ["a.parquet"]


def test_a_second_producer_of_a_key_is_refused_naming_both_steps_and_the_key(
    tmp_path: Path,
) -> None:
    """After step 'first' produces 'shared.parquet', a python step 'second' producing 'shared.parquet' raises WiringError whose message names 'second', 'first' and 'shared.parquet'."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="shared.parquet", name="first")

    with pytest.raises(WiringError) as info:
        wf.python_step(_constant, inputs={}, produces="shared.parquet", name="second")

    message = str(info.value)
    assert message.startswith("Step 'second':"), message
    assert "'first'" in message, message
    assert "'shared.parquet'" in message, message
    assert [task.name for task in wf.pipeline().tasks] == ["first"]


def test_a_notebook_steps_executed_notebook_counts_as_a_product(tmp_path: Path) -> None:
    """After a notebook step whose executed key is 'run.ipynb', a command step producing 'run.ipynb' is refused naming the notebook step and the key."""
    notebook = write_source_notebook(tmp_path / "report.ipynb")
    wf = Workflow(tmp_path / "out", "run1")
    wf.notebook_step(notebook, executed="run.ipynb", env="notebook_env", name="report")

    with pytest.raises(WiringError) as info:
        wf.command_step("touch {out}", produces={"out": "run.ipynb"}, name="copy")

    message = str(info.value)
    assert message.startswith("Step 'copy':"), message
    assert "'report'" in message, message
    assert "'run.ipynb'" in message, message


def test_a_notebook_steps_produces_keys_count_as_products(tmp_path: Path) -> None:
    """A notebook step whose produces includes a key a python step already produces is refused naming the python step and the key."""
    notebook = write_source_notebook(tmp_path / "report.ipynb", "table = 'unset'")
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="table.csv", name="make_table")

    with pytest.raises(WiringError) as info:
        wf.notebook_step(
            notebook,
            executed="report.ipynb",
            env="notebook_env",
            produces={"table": "table.csv"},
            name="report",
        )

    message = str(info.value)
    assert message.startswith("Step 'report':"), message
    assert "'make_table'" in message, message
    assert "'table.csv'" in message, message


def test_every_produces_key_of_a_command_step_counts_as_a_product(tmp_path: Path) -> None:
    """After a command step producing 'a.csv' and 'b.csv', a python step producing 'b.csv' is refused naming the command step and 'b.csv'."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.command_step("touch {a} {b}", produces={"a": "a.csv", "b": "b.csv"}, name="pair")

    with pytest.raises(WiringError) as info:
        wf.python_step(_constant, inputs={}, produces="b.csv", name="late")

    message = str(info.value)
    assert message.startswith("Step 'late':"), message
    assert "'pair'" in message, message
    assert "'b.csv'" in message, message


def test_a_simulation_steps_run_directory_key_counts_as_a_product(tmp_path: Path) -> None:
    """After a simulation step producing 'sims/toy', a second simulation step producing 'sims/toy' with another name is refused naming the first step and 'sims/toy'."""
    model = tmp_path / "model"
    arguments: dict[str, Any] = {
        "spec": write_spec(model / "spec.yaml"),
        "branches": write_branches(model / "branches.yaml"),
        "produces": "sims/toy",
        "env": "simulation_env",
        "options": {"project": "proj_test", "no_slack": True},
    }
    wf = Workflow(tmp_path / "out", "run1")
    wf.simulation_step(**arguments, name="toy")

    with pytest.raises(WiringError) as info:
        wf.simulation_step(**arguments, name="again")

    message = str(info.value)
    assert message.startswith("Step 'again':"), message
    assert "'toy'" in message, message
    assert "'sims/toy'" in message, message


def test_a_refused_step_registers_nothing_and_is_not_added(tmp_path: Path) -> None:
    """A command step refused because one of its two products is already produced leaves its other product unregistered (registry.key_for of its path is None) and is not in the next pipeline's tasks."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="a.csv", name="first")

    with pytest.raises(WiringError):
        wf.command_step("touch {a} {b}", produces={"a": "a.csv", "b": "b.csv"}, name="pair")

    assert wf.registry.key_for(wf.registry.path_for("b.csv")) is None
    assert [task.name for task in wf.pipeline().tasks] == ["first"]


def test_a_step_its_step_function_rejects_is_not_added_so_its_name_stays_free(
    tmp_path: Path,
) -> None:
    """A python step named 'x' that raises for an unresolved input is not added, and a corrected python step named 'x' added afterwards is accepted and is the only task in the pipeline."""
    wf = Workflow(tmp_path / "out", "run1")
    with pytest.raises(WiringError):
        wf.python_step(
            _increment, inputs={"source": "missing.parquet"}, produces="x.parquet", name="x"
        )

    wf.python_step(_constant, inputs={}, produces="x.parquet", name="x")

    [task] = wf.pipeline().tasks
    assert task.name == "x"
    assert task.inputs == {}


# Building the pipeline


def test_pipeline_returns_the_tasks_in_the_order_added_as_a_tuple(tmp_path: Path) -> None:
    """wf.pipeline().tasks is a tuple of the added steps' tasks in the order they were added."""
    wf = Workflow(tmp_path / "out", "run1")
    for name in ("c", "a", "b"):
        wf.python_step(_constant, inputs={}, produces=f"{name}.parquet", name=name)

    tasks = wf.pipeline().tasks

    assert isinstance(tasks, tuple)
    assert [task.name for task in tasks] == ["c", "a", "b"]


def test_pipeline_root_registry_and_resources_come_from_the_workflow(tmp_path: Path) -> None:
    """wf.pipeline() has root wf.state, registry wf.registry, and resources equal to the resources given to the workflow."""
    resources = {"project": "proj_test", "memory": 8}
    wf = Workflow(tmp_path / "out", "run1", resources=resources)

    pipeline = wf.pipeline()

    assert pipeline.root == wf.state
    assert pipeline.registry is wf.registry
    assert dict(pipeline.resources) == {"project": "proj_test", "memory": 8}
    assert dict(wf.resources) == {"project": "proj_test", "memory": 8}


def test_a_workflow_without_resources_gives_a_pipeline_with_empty_resources(
    tmp_path: Path,
) -> None:
    """A workflow made without resources gives a pipeline whose resources are an empty mapping."""
    wf = Workflow(tmp_path / "out", "run1")

    pipeline = wf.pipeline()

    assert isinstance(pipeline.resources, Mapping)
    assert dict(pipeline.resources) == {}
    assert dict(wf.resources) == {}


def test_a_step_added_after_pipeline_does_not_reach_the_returned_pipeline(
    tmp_path: Path,
) -> None:
    """A pipeline returned before a third step is added still has two tasks, and a second call of wf.pipeline() has all three."""
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="a.parquet", name="a")
    wf.python_step(_constant, inputs={}, produces="b.parquet", name="b")
    earlier = wf.pipeline()

    wf.python_step(_constant, inputs={}, produces="c.parquet", name="c")

    assert [task.name for task in earlier.tasks] == ["a", "b"]
    assert [task.name for task in wf.pipeline().tasks] == ["a", "b", "c"]


def test_each_call_of_pipeline_returns_a_new_equal_pipeline(tmp_path: Path) -> None:
    """Two calls of wf.pipeline() with no step added between them return two distinct Pipeline objects with equal tasks, root, registry and resources."""
    wf = Workflow(tmp_path / "out", "run1", resources={"project": "proj_test"})
    wf.python_step(_constant, inputs={}, produces="a.parquet", name="a")

    first = wf.pipeline()
    second = wf.pipeline()

    assert first is not second
    assert first.tasks == second.tasks
    assert first.root == second.root
    assert first.registry is second.registry
    assert dict(first.resources) == dict(second.resources)


def test_a_later_change_to_the_resources_passed_in_does_not_reach_a_pipeline(
    tmp_path: Path,
) -> None:
    """Changing the dict passed as resources after the workflow is made changes neither wf.resources nor the resources of a pipeline returned before or after the change."""
    given: dict[str, Any] = {"project": "proj_a"}
    wf = Workflow(tmp_path / "out", "run1", resources=given)
    before = wf.pipeline()

    given["project"] = "proj_b"
    given["memory"] = 8
    after = wf.pipeline()

    for resources in (wf.resources, before.resources, after.resources):
        assert dict(resources) == {"project": "proj_a"}


def test_a_workflow_with_no_steps_gives_a_pipeline_with_no_tasks(tmp_path: Path) -> None:
    """wf.pipeline() before any step is added returns a Pipeline whose tasks are an empty tuple."""
    pipeline = Workflow(tmp_path / "out", "run1").pipeline()

    assert isinstance(pipeline, Pipeline)
    assert pipeline.tasks == ()


def test_a_workflow_pipeline_runs_locally_and_writes_under_the_run_directory(
    tmp_path: Path,
) -> None:
    """run() of a two-step workflow pipeline succeeds for both steps, writes the products under output_root/run_name, and writes pytask's state under output_root/.dagger/run_name."""
    output_root = (tmp_path / "out").resolve()
    wf = Workflow(tmp_path / "out", "run1")
    wf.python_step(_constant, inputs={}, produces="a.parquet", name="a")
    wf.python_step(_increment, inputs={"source": "a.parquet"}, produces="b.csv", name="b")

    result = run(wf.pipeline())

    assert result == {"a": TaskOutcome.SUCCESS, "b": TaskOutcome.SUCCESS}
    assert (output_root / "run1" / "a.parquet").is_file()
    assert (output_root / "run1" / "b.csv").is_file()
    assert (output_root / ".dagger" / "run1" / "pytask.lock").is_file()
    assert not (output_root / "run1" / "pytask.lock").exists()


# The frontend's exports


def test_the_frontend_exports_workflow_and_not_the_step_functions_or_the_registry() -> None:
    """vivarium.dagger.frontend.Workflow is the class in vivarium.dagger.frontend.workflow and is in __all__, and none of ArtifactRegistry, python_step, notebook_step, command_step and simulation_step is in __all__."""
    exported = vivarium.dagger.frontend.__all__

    assert vivarium.dagger.frontend.Workflow is Workflow
    assert Workflow.__module__ == "vivarium.dagger.frontend.workflow"
    assert "Workflow" in exported
    lower_layer = (
        "ArtifactRegistry",
        "python_step",
        "notebook_step",
        "command_step",
        "simulation_step",
    )
    assert [name for name in lower_layer if name in exported] == []


def test_the_step_functions_and_the_registry_stay_importable_from_their_modules() -> None:
    """python_step, notebook_step, command_step and simulation_step import from their own frontend modules, and ArtifactRegistry from vivarium.dagger.backend.wiring."""
    locations = {
        "python_step": "vivarium.dagger.frontend.python_step",
        "notebook_step": "vivarium.dagger.frontend.notebook_step",
        "command_step": "vivarium.dagger.frontend.command_step",
        "simulation_step": "vivarium.dagger.frontend.simulation_step",
        "ArtifactRegistry": "vivarium.dagger.backend.wiring",
    }

    for name, module in locations.items():
        found = getattr(importlib.import_module(module), name)
        assert callable(found), (module, name)
        assert found.__module__ == module, (module, name)
