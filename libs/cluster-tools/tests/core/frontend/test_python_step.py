"""Acceptance tests for the python_step frontend (``core.frontend.python_step``)."""

from pathlib import Path

import pandas as pd
import pytest
from pytask import PathNode, TaskOutcome

from vivarium.cluster_tools.core.backend.build import build, outcomes
from vivarium.cluster_tools.core.backend.task import Task
from vivarium.cluster_tools.core.backend.wiring import ArtifactRegistry, WiringError
from vivarium.cluster_tools.core.frontend.python_step import python_step


def _read_frame(data: Path) -> pd.DataFrame:
    """Read the input path and return its frame unchanged."""
    return pd.read_parquet(data)


def _constant() -> pd.DataFrame:
    """Return a fixed one-column frame, taking no inputs."""
    return pd.DataFrame({"n": [1, 2, 3]})


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
        env=None,
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
        env=None,
    )

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.SUCCESS}
    assert calls == ["ran"]  # the target ran exactly once with its input path loaded
    saved = pd.read_parquet(reg.path_for("scaled.parquet"))
    assert saved["n"].tolist() == [2, 4, 6]  # the returned frame was autosaved to the product


def test_python_step_carries_resources_and_env(tmp_path: Path) -> None:
    """The resources dict and env passed to python_step are carried on the Task."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    resources = {"cores": 2, "memory": "4G"}

    task = python_step(
        _constant,
        inputs={},
        produces="out.parquet",
        registry=reg,
        resources=resources,
        env="forecasting",
    )

    assert task.resources == resources
    assert task.env == "forecasting"


def test_two_steps_run_end_to_end_through_a_shared_key(tmp_path: Path) -> None:
    """A producer and a consumer wired through one key run in order and the data flows."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    def grow(mid: Path) -> pd.DataFrame:
        """Read the upstream product path and add ten to its column."""
        frame = pd.read_parquet(mid)
        return frame.assign(n=frame["n"] + 10)

    producer = python_step(
        _constant, inputs={}, produces="mid.parquet", registry=reg, resources={}, env=None
    )
    consumer = python_step(
        grow,
        inputs={"mid": "mid.parquet"},
        produces="final.parquet",
        registry=reg,
        resources={},
        env=None,
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

    task = python_step(
        _constant, inputs={}, produces="table.csv", registry=reg, resources={}, env=None
    )

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
            env=None,
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
            env=None,
        )


def test_a_step_that_writes_no_product_is_a_failure(tmp_path: Path) -> None:
    """A target that fails to produce its product is reported a failure (via a raising target)."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    def boom() -> pd.DataFrame:
        """Raise before returning, so no product is written."""
        raise RuntimeError("no product written")

    task = python_step(
        boom, inputs={}, produces="out.parquet", registry=reg, resources={}, env=None
    )

    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.FAIL}


def test_product_parameter_name_collision_is_rejected(tmp_path: Path) -> None:
    """An input whose parameter name is the reserved product name is rejected."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    with pytest.raises(ValueError, match="collides"):
        python_step(
            _constant,
            inputs={"product": "src"},
            produces="out.parquet",
            registry=reg,
            resources={},
            env=None,
        )
