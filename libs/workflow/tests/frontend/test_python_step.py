"""Acceptance tests for the python_step frontend (``frontend.python_step``)."""

import dataclasses
import functools
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

from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry, WiringError
from vivarium.workflow.frontend.python_step import (
    PythonStepRun,
    python_step,
    resolve_reference,
    target_reference,
)
from vivarium.workflow.harness import build_fixture


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

from vivarium.workflow.frontend.python_step import PythonStepRun, resolve_reference

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
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"src": src})

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
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"src": src})
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
    reg = ArtifactRegistry(tmp_path / "out", "run1")
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
    reg = ArtifactRegistry(tmp_path / "out", "run1")

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
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    task = python_step(_constant, inputs={}, produces="table.csv", registry=reg, resources={})

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.SUCCESS}
    saved = pd.read_csv(reg.path_for("table.csv"))
    assert saved["n"].tolist() == [1, 2, 3]  # the returned frame round-trips as CSV


def test_unknown_input_key_surfaces_a_did_you_mean(tmp_path: Path) -> None:
    """An input key with no producer or root input raises the registry's did-you-mean."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
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
    reg = ArtifactRegistry(tmp_path / "out", "run1")

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
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    def boom() -> pd.DataFrame:
        """Raise before returning, so no product is written."""
        raise RuntimeError("no product written")

    task = python_step(boom, inputs={}, produces="out.parquet", registry=reg, resources={})

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.FAIL}


def test_product_parameter_name_collision_is_rejected(tmp_path: Path) -> None:
    """An input whose parameter name is the reserved product name is rejected."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError, match=r"(?s)Task 'out\.parquet'.*collides"):
        python_step(
            _constant,
            inputs={"product": "src"},
            produces="out.parquet",
            registry=reg,
            resources={},
        )


def test_python_step_run_holds_the_target_and_product_suffix(tmp_path: Path) -> None:
    """python_step's Task.run is a PythonStepRun of the given target and the produces suffix."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

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
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError):
        python_step(_constant, inputs={}, produces="out.txt", registry=reg)

    with pytest.raises(WiringError):
        reg.resolve_input("out.txt")
    assert reg.register_output("out.txt").path == reg.path_for("out.txt")


def test_name_sets_the_task_name_and_defaults_to_the_produces_key(tmp_path: Path) -> None:
    """python_step(name=...) becomes Task.name; without name the task is named by its produces key."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    named = python_step(
        _constant, inputs={}, produces="a.parquet", registry=reg, name="make_a"
    )
    unnamed = python_step(_constant, inputs={}, produces="b.parquet", registry=reg)

    assert named.name == "make_a"
    assert unnamed.name == "b.parquet"


def test_an_empty_name_is_rejected(tmp_path: Path) -> None:
    """python_step(name='') raises ValueError."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError, match="non-empty"):
        python_step(_constant, inputs={}, produces="a.parquet", registry=reg, name="")

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
        reg = ArtifactRegistry(tmp_path / "out", "run1")

        with pytest.raises(ValueError) as info:
            python_step(_constant, inputs={}, produces="out.txt", registry=reg, name=name)

        assert "out.txt" in str(info.value)
        assert f"Task '{step}'" in str(info.value)


def test_a_target_that_does_not_return_a_dataframe_fails_naming_it(tmp_path: Path) -> None:
    """Building a step whose target returns None fails with a TypeError that names the target, the returned type, and a missing return."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
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
    assert re.search(r"\breturn\b", stripped), message
    assert not reg.path_for("out.parquet").exists()
    assert not direct.exists()
