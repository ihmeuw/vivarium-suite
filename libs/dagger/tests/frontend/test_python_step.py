"""Acceptance tests for the python_step frontend (``frontend.python_step``)."""

import dataclasses
import functools
import importlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pytask import PathNode, PNode, TaskOutcome

from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry, WiringError
from vivarium.dagger.frontend.python_step import (
    PythonStepRun,
    python_step,
    resolve_reference,
    target_reference,
)
from vivarium.dagger.harness import build_fixture


def _read_frame(data: Path) -> pd.DataFrame:
    """Read the input path and return its frame unchanged."""
    return pd.read_parquet(data)


def _constant() -> pd.DataFrame:
    """Return a fixed one-column frame, taking no inputs."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _builds_a_frame() -> Any:
    """Build the fixed frame but return nothing, as a step missing its return statement."""
    pd.DataFrame({"n": [1, 2, 3]})


def _path_of(node: PNode) -> Path:
    """Return the path of a file node."""
    assert isinstance(node, PathNode)
    assert isinstance(node.path, Path)
    return node.path


def _in_main() -> pd.DataFrame:
    """Stand in for a step function defined in a script or a notebook."""
    return _constant()


setattr(_in_main, "__module__", "__main__")


def _from_missing_module() -> pd.DataFrame:
    """Stand in for a step function whose module cannot be imported."""
    return _constant()


setattr(_from_missing_module, "__module__", "no_such_module_xyz_vw")


def _under_missing_name() -> pd.DataFrame:
    """Stand in for a step function whose qualified name its module does not have."""
    return _constant()


setattr(_under_missing_name, "__qualname__", "_no_such_step_xyz_vw")


def _make_inner() -> Callable[[], pd.DataFrame]:
    """Return a step function defined inside this one."""

    def inner() -> pd.DataFrame:
        return _constant()

    return inner


def _with_wraps(func: Callable[[], pd.DataFrame]) -> Callable[[], pd.DataFrame]:
    """Wrap ``func`` using ``functools.wraps``."""

    @functools.wraps(func)
    def wrapper() -> pd.DataFrame:
        return func()

    return wrapper


def _without_wraps(func: Callable[[], pd.DataFrame]) -> Callable[[], pd.DataFrame]:
    """Wrap ``func`` without ``functools.wraps``."""

    def wrapper() -> pd.DataFrame:
        return func()

    return wrapper


@_with_wraps
def _wrapped() -> pd.DataFrame:
    """Return the fixed frame through a decorator that uses ``functools.wraps``."""
    return _constant()


@_without_wraps
def _unwrapped() -> pd.DataFrame:
    """Return the fixed frame through a decorator that does not use ``functools.wraps``."""
    return _constant()


class _Steps:
    """Hold step functions bound in each way a class can bind them."""

    def __call__(self) -> pd.DataFrame:
        """Return the fixed frame when an instance is called."""
        return _constant()

    def compute(self) -> pd.DataFrame:
        """Return the fixed frame from a bound method."""
        return _constant()

    @classmethod
    def make(cls) -> pd.DataFrame:
        """Return the fixed frame from a classmethod."""
        return _constant()

    @staticmethod
    def static() -> pd.DataFrame:
        """Return the fixed frame from a staticmethod."""
        return _constant()


_STEPS = _Steps()

_NESTED = "nested|inside another function"

_IMPORT = r"(?i)cannot be imported as"

_REBUILD = """
import json
import sys
from pathlib import Path

from vivarium.dagger.frontend.python_step import PythonStepRun, resolve_reference

for reference, suffix, inputs, product in json.loads(sys.argv[1]):
    step_run = PythonStepRun(resolve_reference(reference), suffix)
    step_run(product=Path(product), **{name: Path(path) for name, path in inputs.items()})
"""


def _without_identity(message: str, target: Callable[..., Any]) -> str:
    """Remove from ``message`` every string that names ``target`` itself."""
    names = {repr(target), str(target)}
    qualname = getattr(target, "__qualname__", None)
    if qualname is not None:
        module = getattr(target, "__module__", None)
        names |= {qualname, f"{module}:{qualname}", f"{module}.{qualname}"}
    for name in sorted(names, key=len, reverse=True):
        message = message.replace(name, "")
    return message


def test_python_step_returns_a_task_wired_to_the_registry(tmp_path: Path) -> None:
    """python_step returns one Task whose product and inputs are the registry's nodes."""
    src = tmp_path / "src.parquet"
    pd.DataFrame({"n": [1]}).to_parquet(src)
    reg = FileRegistry(tmp_path / "out", "run1", root_inputs={"src": src})

    task = python_step(
        _read_frame,
        inputs={"data": "src"},
        produces="mid.parquet",
        registry=reg,
        resources={},
    )

    assert isinstance(task, Task)
    [product] = list(task.outputs.values())
    assert product is reg.resolve_input("mid.parquet")  # the registry's shared product node
    [dependency] = list(task.inputs.values())
    assert isinstance(dependency, PathNode)
    assert dependency.path == src.resolve()  # input resolved to the root-input file node


def test_running_a_step_calls_the_target_and_writes_the_product(tmp_path: Path) -> None:
    """Running the built task calls the target with loaded inputs and autosaves its DataFrame."""
    src = tmp_path / "src.parquet"
    pd.DataFrame({"n": [1, 2, 3]}).to_parquet(src)
    reg = FileRegistry(tmp_path / "out", "run1", root_inputs={"src": src})
    calls: list[str] = []

    def scale(data: Path) -> pd.DataFrame:
        """Read the input path and return its frame doubled."""
        calls.append("ran")
        frame = pd.read_parquet(data)
        return frame.assign(n=frame["n"] * 2)

    task = python_step(
        scale,
        inputs={"data": "src"},
        produces="scaled.parquet",
        registry=reg,
        resources={},
    )

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.SUCCESS}
    assert calls == ["ran"]  # the target ran exactly once with its input path loaded
    saved = pd.read_parquet(reg.path_for("scaled.parquet"))
    assert saved["n"].tolist() == [2, 4, 6]  # the returned frame was autosaved to the product


def test_python_step_carries_resources_and_runs_in_the_pipeline_env(tmp_path: Path) -> None:
    """The resources dict passed to python_step is carried on the Task, whose env is None."""
    reg = FileRegistry(tmp_path / "out", "run1")
    resources = {"cores": 2, "memory": "4G"}

    task = python_step(
        _constant,
        inputs={},
        produces="out.parquet",
        registry=reg,
        resources=resources,
    )

    assert task.resources == resources
    assert task.env is None


def test_two_steps_run_end_to_end_through_a_shared_key(tmp_path: Path) -> None:
    """A producer and a consumer wired through one key run in order and the data flows."""
    reg = FileRegistry(tmp_path / "out", "run1")

    def grow(mid: Path) -> pd.DataFrame:
        """Read the upstream product path and add ten to its column."""
        frame = pd.read_parquet(mid)
        return frame.assign(n=frame["n"] + 10)

    producer = python_step(
        _constant, inputs={}, produces="mid.parquet", registry=reg, resources={}
    )
    consumer = python_step(
        grow,
        inputs={"mid": "mid.parquet"},
        produces="final.parquet",
        registry=reg,
        resources={},
    )

    assert outcomes(build([producer, consumer], tmp_path / "state")) == {
        producer.name: TaskOutcome.SUCCESS,
        consumer.name: TaskOutcome.SUCCESS,
    }
    final = pd.read_parquet(reg.path_for("final.parquet"))
    assert final["n"].tolist() == [11, 12, 13]  # the producer's data flowed to the consumer


def test_csv_product_round_trips(tmp_path: Path) -> None:
    """A ".csv" product is autosaved so it round-trips through pd.read_csv."""
    reg = FileRegistry(tmp_path / "out", "run1")

    task = python_step(_constant, inputs={}, produces="table.csv", registry=reg, resources={})

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.SUCCESS}
    saved = pd.read_csv(reg.path_for("table.csv"))
    assert saved["n"].tolist() == [1, 2, 3]  # the returned frame round-trips as CSV


def test_unknown_input_key_surfaces_a_did_you_mean(tmp_path: Path) -> None:
    """An input key with no producer or root input raises the registry's did-you-mean."""
    reg = FileRegistry(tmp_path / "out", "run1")
    reg.register_output("caps/ethiopia.csv")

    with pytest.raises(WiringError, match="Did you mean 'caps/ethiopia.csv'"):
        python_step(
            _read_frame,
            inputs={"data": "caps/ethiopa.csv"},
            produces="out.parquet",
            registry=reg,
            resources={},
        )


def test_unsupported_product_suffix_is_rejected(tmp_path: Path) -> None:
    """A produces key whose suffix is not a supported save format is rejected."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError):
        python_step(
            _constant,
            inputs={},
            produces="out.txt",
            registry=reg,
            resources={},
        )


def test_a_step_that_writes_no_product_is_a_failure(tmp_path: Path) -> None:
    """A target that fails to produce its product is reported a failure (via a raising target)."""
    reg = FileRegistry(tmp_path / "out", "run1")

    def boom() -> pd.DataFrame:
        """Raise before returning, so no product is written."""
        raise RuntimeError("no product written")

    task = python_step(boom, inputs={}, produces="out.parquet", registry=reg, resources={})

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.FAIL}


def test_product_parameter_name_collision_is_rejected(tmp_path: Path) -> None:
    """An input whose parameter name is the reserved product name is rejected with a message that starts with the step's name."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError, match=r"(?s)^Step 'out\.parquet': .*collides"):
        python_step(
            _constant,
            inputs={"product": "src"},
            produces="out.parquet",
            registry=reg,
            resources={},
        )


def test_python_step_run_holds_the_target_and_product_suffix(tmp_path: Path) -> None:
    """python_step's Task.run is a PythonStepRun of the given target and the produces suffix."""
    reg = FileRegistry(tmp_path / "out", "run1")

    task = python_step(_constant, inputs={}, produces="tables/out.csv", registry=reg)

    assert task.run == PythonStepRun(_constant, ".csv")


def test_python_step_run_compares_and_hashes_by_value_and_is_frozen() -> None:
    """Two PythonStepRuns of the same target and suffix are equal and hash equal, and fields cannot be reassigned."""
    step_run = PythonStepRun(_constant, ".parquet")
    twin = PythonStepRun(_constant, ".parquet")

    assert step_run == twin
    assert hash(step_run) == hash(twin)
    assert step_run != PythonStepRun(_constant, ".csv")
    assert step_run != PythonStepRun(_read_frame, ".parquet")
    for field, value in (("target", _read_frame), ("suffix", ".csv")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(step_run, field, value)


def test_python_step_run_rejects_an_unsupported_suffix() -> None:
    """Constructing a PythonStepRun with a suffix that has no save format raises ValueError."""
    with pytest.raises(ValueError, match=r"'\.txt'"):
        PythonStepRun(_constant, ".txt")


def test_unsupported_suffix_leaves_the_registry_unchanged(tmp_path: Path) -> None:
    """A rejected produces suffix registers nothing, so the key can still be registered afterwards."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError):
        python_step(_constant, inputs={}, produces="out.txt", registry=reg)

    with pytest.raises(WiringError):
        reg.resolve_input("out.txt")
    assert reg.register_output("out.txt").path == reg.path_for("out.txt")


def test_name_sets_the_task_name_and_defaults_to_the_produces_key(tmp_path: Path) -> None:
    """python_step(name=...) becomes Task.name; without name the task is named by its produces key."""
    reg = FileRegistry(tmp_path / "out", "run1")

    named = python_step(
        _constant, inputs={}, produces="a.parquet", registry=reg, name="make_a"
    )
    unnamed = python_step(_constant, inputs={}, produces="b.parquet", registry=reg)

    assert named.name == "make_a"
    assert unnamed.name == "b.parquet"


@pytest.mark.parametrize(
    "produces",
    [pytest.param("a.parquet", id="one_key"), pytest.param({"a": "a.parquet"}, id="mapping")],
)
def test_an_empty_name_is_rejected(tmp_path: Path, produces: str | dict[str, str]) -> None:
    """python_step(name='') raises ValueError starting with 'python_step:' for a single key and for a mapping, and registers nothing."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError, match="^python_step: name must be a non-empty"):
        python_step(_constant, inputs={}, produces=produces, registry=reg, name="")

    with pytest.raises(WiringError):
        reg.resolve_input("a.parquet")


@pytest.mark.parametrize(
    "target, qualname",
    [
        pytest.param(_constant, "_constant", id="function"),
        pytest.param(_wrapped, "_wrapped", id="wraps_decorated"),
        pytest.param(_Steps.static, "_Steps.static", id="staticmethod"),
    ],
)
def test_target_reference_round_trips_importable_targets(
    target: Callable[..., Any], qualname: str
) -> None:
    """target_reference names a module-level function, a functools.wraps-decorated function, and a staticmethod, and resolve_reference returns the same object."""
    reference = target_reference(target)

    assert reference == f"{__name__}:{qualname}"
    assert resolve_reference(reference) is target


@pytest.mark.parametrize(
    "target, case, cause",
    [
        pytest.param(_in_main, "script or (a )?notebook", None, id="main"),
        pytest.param(lambda: _constant(), "lambda", None, id="lambda"),
        pytest.param(_make_inner(), _NESTED, None, id="nested"),
        pytest.param(
            functools.partial(_read_frame, Path("x")), "partial", None, id="partial"
        ),
        pytest.param(_STEPS, "instance", None, id="callable_instance"),
        pytest.param(_STEPS.compute, "method", None, id="bound_method"),
        pytest.param(_Steps.make, "method", None, id="classmethod"),
        pytest.param(_unwrapped, _NESTED, None, id="decorator_without_wraps"),
        pytest.param(_from_missing_module, _IMPORT, ImportError, id="missing_module"),
        pytest.param(_under_missing_name, _IMPORT, AttributeError, id="missing_qualname"),
    ],
)
def test_target_reference_rejects_targets_that_cannot_be_imported_by_reference(
    target: Callable[..., Any], case: str, cause: type[Exception] | None
) -> None:
    """A __main__ function, a lambda, a nested function, a partial, a callable instance, a bound method, a classmethod, a decorator without wraps, and a target whose module or qualified name does not import each raise ValueError naming the case."""
    with pytest.raises(ValueError) as info:
        target_reference(target)

    message = str(info.value)
    assert re.search(case, _without_identity(message, target)), message
    assert re.search("top.level", message)
    if cause is not None:
        assert isinstance(info.value.__cause__, cause)


@pytest.mark.parametrize(
    "reference",
    [__name__, ":_constant", f"{__name__}:", ""],
    ids=["no_colon", "no_module", "no_qualname", "empty"],
)
def test_resolve_reference_rejects_a_malformed_reference(reference: str) -> None:
    """A reference without the 'module:qualname' form raises ValueError."""
    with pytest.raises(ValueError, match="module:qualname"):
        resolve_reference(reference)


def test_a_run_rebuilt_in_a_fresh_process_writes_the_same_product(tmp_path: Path) -> None:
    """A PythonStepRun rebuilt in a fresh isolated interpreter from the harness targets' references and suffixes writes products equal to a local build's."""
    fixture = build_fixture(tmp_path)
    ran = dict.fromkeys(fixture.steps, TaskOutcome.SUCCESS)
    assert outcomes(build(fixture.tasks, fixture.state)) == ran

    jobs: list[tuple[str, str, dict[str, str], str]] = []
    products: list[tuple[Path, Path]] = []
    for task in fixture.tasks:
        step_run = task.run
        assert isinstance(step_run, PythonStepRun)
        assert isinstance(step_run.suffix, str)
        [local] = [_path_of(node) for node in task.outputs.values()]
        remote = tmp_path / "remote" / local.name
        inputs = {name: str(_path_of(node)) for name, node in task.inputs.items()}
        reference = target_reference(step_run.target)
        jobs.append((reference, step_run.suffix, inputs, str(remote)))
        products.append((local, remote))

    subprocess.run([sys.executable, "-I", "-c", _REBUILD, json.dumps(jobs)], check=True)

    for local, remote in products:
        pd.testing.assert_frame_equal(pd.read_parquet(remote), pd.read_parquet(local))


def test_unsupported_suffix_error_names_the_step_and_produces_key(tmp_path: Path) -> None:
    """python_step's error for an unsupported product suffix names the step and the produces key."""
    cases: list[tuple[str | None, str]] = [(None, "out.txt"), ("clean", "clean")]
    for name, step in cases:
        reg = FileRegistry(tmp_path / "out", "run1")

        with pytest.raises(ValueError) as info:
            python_step(_constant, inputs={}, produces="out.txt", registry=reg, name=name)

        assert "out.txt" in str(info.value)
        assert str(info.value).startswith(f"Step {step!r}: "), info.value


def test_a_target_that_does_not_return_a_dataframe_fails_naming_it(tmp_path: Path) -> None:
    """Building a step whose target returns None fails with a TypeError that names the target, the returned type, and a missing return."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = python_step(_builds_a_frame, inputs={}, produces="out.parquet", registry=reg)
    step_run, direct = task.run, tmp_path / "direct.parquet"
    assert isinstance(step_run, PythonStepRun)

    session = build([task], tmp_path / "state")
    with pytest.raises(TypeError) as info:
        step_run(product=direct)

    assert outcomes(session) == {task.name: TaskOutcome.FAIL}
    [report] = session.execution_reports
    assert report.exc_info is not None
    assert isinstance(report.exc_info[1], TypeError)
    message = str(info.value)
    assert "_builds_a_frame" in message
    stripped = _without_identity(message, _builds_a_frame)
    assert "None" in stripped, message
    assert "Check for a missing return statement." in message, message
    assert not reg.path_for("out.parquet").exists()
    assert not direct.exists()


def test_an_unresolved_input_error_names_the_step_and_the_key(tmp_path: Path) -> None:
    """A python_step named 'b' whose input key has no producer raises WiringError whose message starts with "Step 'b': " and names the key and the did-you-mean key."""
    reg = FileRegistry(tmp_path / "out", "run1")
    reg.register_output("caps/ethiopia.csv")

    with pytest.raises(WiringError) as info:
        python_step(
            _read_frame,
            inputs={"data": "caps/ethiopa.csv"},
            produces="out.parquet",
            registry=reg,
            name="b",
        )

    message = str(info.value)
    assert message.startswith("Step 'b': "), message
    assert "'caps/ethiopa.csv'" in message
    assert "Did you mean 'caps/ethiopia.csv'" in message


def test_producing_a_root_input_error_names_the_step(tmp_path: Path) -> None:
    """A python_step named 'b' whose produces key is a declared root input raises WiringError whose message starts with "Step 'b': "."""
    source = tmp_path / "src.parquet"
    reg = FileRegistry(tmp_path / "out", "run1", root_inputs={"src.parquet": source})

    with pytest.raises(WiringError) as info:
        python_step(_constant, inputs={}, produces="src.parquet", registry=reg, name="b")

    message = str(info.value)
    assert message.startswith("Step 'b': "), message
    assert "'src.parquet'" in message


def test_the_step_name_prefix_defaults_to_the_produces_key(tmp_path: Path) -> None:
    """Without name=, the WiringError message for an unresolved input starts with "Step '<produces key>': "."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(WiringError) as info:
        python_step(
            _read_frame,
            inputs={"data": "missing.parquet"},
            produces="out.parquet",
            registry=reg,
        )

    message = str(info.value)
    assert message.startswith("Step 'out.parquet': "), message


def _returns_a_dict() -> Any:
    """Return a dict where a DataFrame is expected."""
    return {"value": [1]}


def test_a_target_that_returns_another_type_gets_no_missing_return_hint(
    tmp_path: Path,
) -> None:
    """A target that returns a dict fails naming dict, without the missing-return hint that only None gets."""
    reg = FileRegistry(tmp_path / "out", "run1")
    step_run = python_step(
        _returns_a_dict, inputs={}, produces="out.parquet", registry=reg
    ).run
    assert isinstance(step_run, PythonStepRun)

    with pytest.raises(TypeError) as info:
        step_run(product=tmp_path / "direct.parquet")

    assert "returned dict" in str(info.value)
    assert "missing return" not in str(info.value)


def test_target_reference_names_a_reloaded_module_and_says_to_rebuild_the_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A top-level function whose module was reloaded after it was taken raises ValueError that says the module was reloaded and to build the steps again, without the top-level advice."""
    (tmp_path / "reloaded_steps_mod.py").write_text("def make():\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("reloaded_steps_mod")
    try:
        target = module.make
        assert target_reference(target) == "reloaded_steps_mod:make"
        importlib.reload(module)

        with pytest.raises(ValueError) as info:
            target_reference(target)
    finally:
        sys.modules.pop("reloaded_steps_mod", None)

    message = str(info.value)
    assert "reloaded" in message, message
    assert "Build the steps again after reloading the module." in message
    assert "top level" not in message


# A python step with several products (MIC-7827)

_SPLIT = {"population": "population.parquet", "deaths": "deaths.csv"}
"""The products of the step 'split' that most of these tests compile."""

_SPLIT_NAMES = "The product names are 'deaths' and 'population'."
"""The sentence that lists ``_SPLIT``'s product names in a run-time message."""


def _population() -> pd.DataFrame:
    """Return the population frame that :func:`_split` returns."""
    return pd.DataFrame({"age": [1, 2], "count": [10, 20]})


def _deaths() -> pd.DataFrame:
    """Return the deaths frame that :func:`_split` returns."""
    return pd.DataFrame({"age": [1, 2], "count": [1, 2]})


def _split() -> dict[str, pd.DataFrame]:
    """Return a population frame and a deaths frame by product name."""
    return {"population": _population(), "deaths": _deaths()}


def _split_fails() -> dict[str, pd.DataFrame]:
    """Raise before returning, so no product is written."""
    raise RuntimeError("the split broke")


def _split_without_deaths() -> dict[str, pd.DataFrame]:
    """Return only the population frame, leaving out the deaths product."""
    return {"population": _population()}


def _split_with_extra_keys() -> dict[Any, pd.DataFrame]:
    """Return both frames and two frames under keys that are not product names."""
    return {**_split(), "births": _population(), 7: _population()}


def _population_only_as_other() -> dict[str, pd.DataFrame]:
    """Return the population frame under a key that is not a product name."""
    return {"other": _population()}


def _split_with_a_bad_value() -> dict[str, Any]:
    """Return the population frame and None for deaths."""
    return {"population": _population(), "deaths": None}


def _split_with_two_bad_values() -> dict[str, Any]:
    """Return an int for population and None for deaths."""
    return {"population": 3, "deaths": None}


def _split_returns_nothing() -> Any:
    """Build both frames but return nothing, as a step missing its return statement."""
    _split()


def _split_returns_a_frame() -> pd.DataFrame:
    """Return one frame where a dict of frames is expected."""
    return _population()


def _constant_by_name() -> dict[str, pd.DataFrame]:
    """Return the fixed frame under the product name 'out'."""
    return {"out": _constant()}


def _read_population(population: Path) -> pd.DataFrame:
    """Read the population product and return it doubled."""
    frame = pd.read_parquet(population)
    return frame.assign(count=frame["count"] * 2)


def _read_deaths(deaths: Path) -> pd.DataFrame:
    """Read the deaths product and return it unchanged."""
    return pd.read_csv(deaths)


def _copy_product(product: Path) -> dict[str, pd.DataFrame]:
    """Read the input named 'product' and return it under the product name 'out'."""
    return {"out": pd.read_parquet(product)}


def _split_task(reg: FileRegistry, target: Callable[..., Any] = _split) -> Task:
    """Return the step 'split' that writes ``_SPLIT``'s two products with ``target``."""
    return python_step(target, inputs={}, produces=_SPLIT, registry=reg, name="split")


def _split_paths(reg: FileRegistry) -> dict[str, Path]:
    """Return the path of each of ``_SPLIT``'s products by product name."""
    return {name: reg.path_for(key) for name, key in _SPLIT.items()}


def _call_directly(step_run: object, paths: dict[str, Path]) -> None:
    """Call the PythonStepRun ``step_run`` with ``paths`` as its product arguments."""
    assert isinstance(step_run, PythonStepRun)
    step_run(**paths)


def _assert_unregistered(reg: FileRegistry, *keys: str) -> None:
    """Assert that none of ``keys`` is registered with ``reg``."""
    for key in keys:
        assert reg.key_for(reg.path_for(key)) is None, key


def test_a_mapping_step_has_one_output_per_product_and_a_run_of_its_suffixes(
    tmp_path: Path,
) -> None:
    """With produces mapping 'population' and 'deaths' to two keys, python_step returns a Task whose outputs are the registry's node for each key under its name and whose run is a PythonStepRun of the target and each name's suffix."""
    reg = FileRegistry(tmp_path / "out", "run1")

    task = _split_task(reg)

    assert task.name == "split"
    assert task.outputs == {
        "population": reg.resolve_input("population.parquet"),
        "deaths": reg.resolve_input("deaths.csv"),
    }
    assert task.run == PythonStepRun(_split, {"population": ".parquet", "deaths": ".csv"})
    assert task.env is None


def test_a_mapping_with_one_entry_and_no_name_is_named_after_its_key(tmp_path: Path) -> None:
    """A mapping step with one product and no name is named after that product's key."""
    reg = FileRegistry(tmp_path / "out", "run1")

    task = python_step(
        _constant_by_name, inputs={}, produces={"out": "out.csv"}, registry=reg
    )

    assert task.name == "out.csv"
    assert list(task.outputs) == ["out"]


def test_building_a_mapping_step_saves_each_frame_in_its_keys_format(tmp_path: Path) -> None:
    """Building the split step writes population.parquet as Parquet and deaths.csv as CSV without an index column, each equal to the frame the target returned."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = _split_task(reg)

    assert outcomes(build([task], tmp_path / "state")) == {"split": TaskOutcome.SUCCESS}
    paths = _split_paths(reg)
    pd.testing.assert_frame_equal(pd.read_parquet(paths["population"]), _population())
    pd.testing.assert_frame_equal(pd.read_csv(paths["deaths"]), _deaths())


def _with_consumers(reg: FileRegistry, target: Callable[..., Any] = _split) -> list[Task]:
    """Return the split step and one python step that reads each of its products."""
    split = _split_task(reg, target)
    population = python_step(
        _read_population,
        inputs={"population": "population.parquet"},
        produces="doubled.parquet",
        registry=reg,
    )
    deaths = python_step(
        _read_deaths, inputs={"deaths": "deaths.csv"}, produces="copied.csv", registry=reg
    )
    return [split, population, deaths]


def test_a_downstream_step_reads_each_product_of_a_mapping_step(tmp_path: Path) -> None:
    """A python step that reads population.parquet and one that reads deaths.csv build after the split step and read the data it saved."""
    reg = FileRegistry(tmp_path / "out", "run1")
    tasks = _with_consumers(reg)

    result = outcomes(build(tasks, tmp_path / "state"))

    assert result == dict.fromkeys(
        ["split", "doubled.parquet", "copied.csv"], TaskOutcome.SUCCESS
    )
    doubled = pd.read_parquet(reg.path_for("doubled.parquet"))
    assert doubled["count"].tolist() == [20, 40]
    pd.testing.assert_frame_equal(pd.read_csv(reg.path_for("copied.csv")), _deaths())


def test_the_consumers_of_a_failed_mapping_step_are_skipped(tmp_path: Path) -> None:
    """When the split step's target raises, the step fails and the consumer of each of its products is skipped because it failed."""
    reg = FileRegistry(tmp_path / "out", "run1")
    tasks = _with_consumers(reg, _split_fails)

    result = outcomes(build(tasks, tmp_path / "state"))

    assert result == {
        "split": TaskOutcome.FAIL,
        "doubled.parquet": TaskOutcome.SKIP_PREVIOUS_FAILED,
        "copied.csv": TaskOutcome.SKIP_PREVIOUS_FAILED,
    }


def test_a_dict_missing_a_product_fails_the_step_and_writes_nothing(tmp_path: Path) -> None:
    """A target whose dict leaves out 'deaths' makes the build fail, and calling the run directly raises ValueError that names the target, the missing product and the product names; neither product is written."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = _split_task(reg, _split_without_deaths)
    direct = {
        name: tmp_path / "direct" / path.name for name, path in _split_paths(reg).items()
    }

    session = build([task], tmp_path / "state")
    with pytest.raises(ValueError) as info:
        _call_directly(task.run, direct)

    assert outcomes(session) == {"split": TaskOutcome.FAIL}
    assert str(info.value) == (
        "Python step target _split_without_deaths returned a dict whose keys do not match "
        f"its product names: the product 'deaths' is missing. {_SPLIT_NAMES}"
    )
    for path in [*_split_paths(reg).values(), *direct.values()]:
        assert not path.exists(), path


def test_a_dict_with_keys_that_are_not_products_raises_naming_them(tmp_path: Path) -> None:
    """A target whose dict has the keys 'births' and 7 beside both products raises ValueError naming both keys, and writes neither product."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = _split_task(reg, _split_with_extra_keys)

    with pytest.raises(ValueError) as info:
        _call_directly(task.run, _split_paths(reg))

    assert str(info.value) == (
        "Python step target _split_with_extra_keys returned a dict whose keys do not match "
        "its product names: the keys 'births' and 7 are not product names. "
        f"{_SPLIT_NAMES}"
    )
    assert not any(path.exists() for path in _split_paths(reg).values())


def test_a_dict_missing_one_product_with_an_extra_key_names_both(tmp_path: Path) -> None:
    """A dict that lacks both products and has one other key gives a message that names the two missing products and then the extra key."""
    step_run = PythonStepRun(_population_only_as_other, {"a": ".csv", "b": ".csv"})

    with pytest.raises(ValueError) as info:
        step_run(a=tmp_path / "a.csv", b=tmp_path / "b.csv")

    assert str(info.value) == (
        "Python step target _population_only_as_other returned a dict whose keys do not "
        "match its product names: the products 'a' and 'b' are missing; the key 'other' "
        "is not a product name. The product names are 'a' and 'b'."
    )


@pytest.mark.parametrize(
    "target, wrong",
    [
        pytest.param(_split_with_a_bad_value, "for 'deaths' is NoneType", id="one"),
        pytest.param(
            _split_with_two_bad_values,
            "for 'population' is int and for 'deaths' is NoneType",
            id="two",
        ),
    ],
)
def test_a_dict_value_that_is_not_a_dataframe_raises_and_writes_nothing(
    tmp_path: Path, target: Callable[..., Any], wrong: str
) -> None:
    """A dict whose value for a product is not a DataFrame raises TypeError naming each such product and its type, in the mapping's order, and writes no product, not even the valid frame."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = _split_task(reg, target)

    with pytest.raises(TypeError) as info:
        _call_directly(task.run, _split_paths(reg))

    assert str(info.value) == (
        f"Python step target {target.__name__} returned a dict whose value {wrong}; each "
        "value must be a pandas DataFrame."
    )
    assert not any(path.exists() for path in _split_paths(reg).values())


@pytest.mark.parametrize(
    "target, returned, hint",
    [
        pytest.param(_split_returns_a_frame, "DataFrame", "", id="frame"),
        pytest.param(
            _split_returns_nothing,
            "NoneType",
            " Check for a missing return statement.",
            id="none",
        ),
    ],
)
def test_a_mapping_step_whose_target_does_not_return_a_dict_raises(
    tmp_path: Path, target: Callable[..., Any], returned: str, hint: str
) -> None:
    """A mapping step whose target returns a DataFrame, or None, raises TypeError that names the target and the type, says to return a dict of frames and lists the product names; only None gets the missing-return hint."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = _split_task(reg, target)

    with pytest.raises(TypeError) as info:
        _call_directly(task.run, _split_paths(reg))

    assert str(info.value) == (
        f"Python step target {target.__name__} returned {returned}; a python step whose "
        "produces is a mapping must return a dict that maps each product name to a pandas "
        f"DataFrame. {_SPLIT_NAMES}{hint}"
    )
    assert not any(path.exists() for path in _split_paths(reg).values())


def test_one_product_name_is_listed_in_the_singular(tmp_path: Path) -> None:
    """A mapping step with one product whose target returns a DataFrame says 'The product name is' and names the one product."""
    step_run = PythonStepRun(_split_returns_a_frame, {"adults": ".csv"})

    with pytest.raises(TypeError, match=r"The product name is 'adults'\.$"):
        step_run(adults=tmp_path / "adults.csv")


def test_a_single_product_target_that_returns_a_dict_is_told_to_map_produces(
    tmp_path: Path,
) -> None:
    """A target that returns a dict for a step with one product key raises TypeError that says to pass produces a mapping of product names to file keys."""
    reg = FileRegistry(tmp_path / "out", "run1")
    task = python_step(_split, inputs={}, produces="out.parquet", registry=reg)

    with pytest.raises(TypeError) as info:
        _call_directly(task.run, {"product": tmp_path / "out.parquet"})

    assert str(info.value) == (
        "Python step target _split returned dict; a python step must return a pandas "
        "DataFrame. To write several products, pass produces a mapping of product names "
        "to file keys."
    )


def test_an_unsupported_suffix_on_a_later_key_names_it_and_registers_nothing(
    tmp_path: Path,
) -> None:
    """A mapping whose second key has the suffix '.txt' raises ValueError that starts with the step's name and names the product and the key, and the first key is not registered."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        python_step(
            _split,
            inputs={},
            produces={"population": "population.parquet", "deaths": "deaths.txt"},
            registry=reg,
            name="split",
        )

    assert str(info.value) == (
        "Step 'split': product 'deaths' (key 'deaths.txt') has unsupported suffix '.txt'; "
        "supported suffixes are .csv, .parquet."
    )
    with pytest.raises(WiringError):
        reg.resolve_input("population.parquet")


@pytest.mark.parametrize("name, prefix", [(None, "python_step"), ("x", "Step 'x'")])
def test_an_empty_mapping_is_refused(tmp_path: Path, name: str | None, prefix: str) -> None:
    """An empty produces mapping raises ValueError that says a step needs a product, starting with 'python_step:' without a name and with "Step 'x':" with one."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        python_step(_split, inputs={}, produces={}, registry=reg, name=name)

    assert str(info.value) == (
        f"{prefix}: produces must have at least one entry; a step with no product cannot "
        "be consumed and cannot be skipped."
    )


def test_several_products_with_no_name_are_refused_listing_the_keys(tmp_path: Path) -> None:
    """A mapping with two products and no name raises ValueError starting with 'python_step:' that lists the keys sorted and says to pass name=, and registers nothing."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        python_step(_split, inputs={}, produces=_SPLIT, registry=reg)

    assert str(info.value) == (
        "python_step: produces has more than one entry, with keys 'deaths.csv' and "
        "'population.parquet', so the step has no default name; pass name= to name it."
    )
    _assert_unregistered(reg, *_SPLIT.values())


def test_a_product_name_that_is_also_an_input_name_is_refused(tmp_path: Path) -> None:
    """A product named 'population' beside an input named 'population' raises ValueError naming the step and the name, and registers nothing."""
    reg = FileRegistry(tmp_path / "out", "run1", root_inputs={"raw": tmp_path / "raw.csv"})

    with pytest.raises(ValueError) as info:
        python_step(
            _split, inputs={"population": "raw"}, produces=_SPLIT, registry=reg, name="split"
        )

    assert str(info.value) == (
        "Step 'split': name 'population' in produces also appears in inputs; a name may "
        "appear in only one of inputs and produces."
    )
    _assert_unregistered(reg, *_SPLIT.values())


@pytest.mark.parametrize(
    "product, problem",
    [
        ("two words", "is not a valid Python identifier"),
        ("class", "is a Python keyword"),
        ("return", "is a Python keyword"),
        ("__code_id__", "is reserved"),
    ],
)
def test_a_product_name_that_is_not_a_parameter_name_is_refused(
    tmp_path: Path, product: str, problem: str
) -> None:
    """A product name that is not an identifier, is a keyword or is reserved raises ValueError that starts with "Step 'x':" and says what is wrong, and registers nothing."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        python_step(_split, inputs={}, produces={product: "a.csv"}, registry=reg, name="x")

    message = str(info.value)
    assert message.startswith(f"Step 'x': name {product!r} in produces {problem}"), message
    _assert_unregistered(reg, "a.csv")


def test_an_input_named_product_is_allowed_with_a_mapping(tmp_path: Path) -> None:
    """With produces a mapping, an input may be named 'product': the step builds and its 'out' product holds the input's data."""
    source = tmp_path / "src.parquet"
    _population().to_parquet(source)
    reg = FileRegistry(tmp_path / "out", "run1", root_inputs={"src": source})

    task = python_step(
        _copy_product, inputs={"product": "src"}, produces={"out": "out.csv"}, registry=reg
    )

    assert outcomes(build([task], tmp_path / "state")) == {"out.csv": TaskOutcome.SUCCESS}
    pd.testing.assert_frame_equal(pd.read_csv(reg.path_for("out.csv")), _population())


def test_a_later_key_that_is_a_root_input_registers_no_key(tmp_path: Path) -> None:
    """A mapping whose second key is a declared root input raises WiringError that starts with the step's name and names the key, and the first key is not registered."""
    reg = FileRegistry(
        tmp_path / "out", "run1", root_inputs={"raw.csv": tmp_path / "raw.csv"}
    )

    with pytest.raises(WiringError) as info:
        python_step(
            _split,
            inputs={},
            produces={"population": "population.parquet", "deaths": "raw.csv"},
            registry=reg,
            name="split",
        )

    message = str(info.value)
    assert message.startswith(
        "Step 'split': output 'raw.csv' is a declared root input"
    ), message
    _assert_unregistered(reg, "population.parquet")


def test_a_later_key_at_another_keys_path_registers_no_key(tmp_path: Path) -> None:
    """A mapping whose second key resolves to the path of a key already registered raises WiringError naming both keys, and the first key is not registered."""
    reg = FileRegistry(tmp_path / "out", "run1")
    reg.register_output("a.csv")

    with pytest.raises(WiringError) as info:
        python_step(
            _split,
            inputs={},
            produces={"population": "population.parquet", "deaths": "b/../a.csv"},
            registry=reg,
            name="split",
        )

    message = str(info.value)
    assert message.startswith(
        "Step 'split': keys 'a.csv' and 'b/../a.csv' resolve to one"
    ), message
    _assert_unregistered(reg, "population.parquet")


def test_two_products_at_one_key_are_refused(tmp_path: Path) -> None:
    """Two product names that map to one key raise ValueError naming both products and the path, and register nothing."""
    reg = FileRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError) as info:
        python_step(
            _split,
            inputs={},
            produces={"population": "x.csv", "deaths": "x.csv"},
            registry=reg,
            name="split",
        )

    assert str(info.value) == (
        "Step 'split': product 'population' (key 'x.csv') and product 'deaths' (key "
        f"'x.csv') resolve to one path {str(reg.path_for('x.csv'))!r}; each product must "
        "be written to its own path."
    )
    _assert_unregistered(reg, "x.csv")


def test_an_input_and_a_product_at_one_key_are_refused(tmp_path: Path) -> None:
    """An input and a product with the same key raise ValueError naming both as one file, and register nothing."""
    reg = FileRegistry(tmp_path / "out", "run1")
    reg.register_output("p.csv")

    with pytest.raises(ValueError) as info:
        python_step(
            _copy_product,
            inputs={"population": "p.csv"},
            produces={"p": "p.csv", "q": "q.csv"},
            registry=reg,
            name="split",
        )

    assert str(info.value) == (
        "Step 'split': input 'population' (key 'p.csv') and product 'p' (key 'p.csv') are "
        "one file; a step cannot read and write the same file."
    )
    _assert_unregistered(reg, "q.csv")


def test_a_mapping_run_compares_and_hashes_by_its_entries_and_is_frozen() -> None:
    """PythonStepRuns with the same suffix entries in a different order are equal and hash equal; a different suffix is unequal; the run's fields cannot be reassigned; and a later change to the dict passed in does not reach the run."""
    passed = {"population": ".parquet", "deaths": ".csv"}
    step_run = PythonStepRun(_split, passed)
    twin = PythonStepRun(_split, {"deaths": ".csv", "population": ".parquet"})

    assert step_run == twin
    assert hash(step_run) == hash(twin)
    assert step_run != PythonStepRun(_split, {"population": ".parquet", "deaths": ".parquet"})
    for field, value in (("target", _split_fails), ("suffix", {"deaths": ".csv"})):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(step_run, field, value)
    passed["deaths"] = ".parquet"
    assert step_run.suffix == {"population": ".parquet", "deaths": ".csv"}


def test_a_single_product_run_hashes_as_before() -> None:
    """A PythonStepRun with one suffix hashes as the tuple of its target and suffix, as the dataclass hash did."""
    assert hash(PythonStepRun(_constant, ".parquet")) == hash((_constant, ".parquet"))


@pytest.mark.parametrize(
    "suffix, message",
    [
        pytest.param(
            {},
            "A python step run needs at least one product suffix, got an empty mapping.",
            id="empty",
        ),
        pytest.param(
            {"population": ".parquet", "deaths": ".txt"},
            "Product 'deaths' has unsupported suffix '.txt'; supported suffixes are .csv, "
            ".parquet.",
            id="txt",
        ),
    ],
)
def test_a_mapping_run_rejects_an_empty_mapping_or_an_unsupported_suffix(
    suffix: dict[str, str], message: str
) -> None:
    """A PythonStepRun with an empty mapping of suffixes, or a mapping with the suffix '.txt', raises ValueError with the exact message."""
    with pytest.raises(ValueError) as info:
        PythonStepRun(_split, suffix)

    assert str(info.value) == message
