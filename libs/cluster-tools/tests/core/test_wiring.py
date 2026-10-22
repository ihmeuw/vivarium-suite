"""Unit tests for the producer/consumer wiring registry (``core.backend.wiring``)."""

from pathlib import Path
from typing import Any

import pytest
from pytask import TaskOutcome

from vivarium.cluster_tools.core.backend.build import build, outcomes
from vivarium.cluster_tools.core.backend.task import Task
from vivarium.cluster_tools.core.backend.wiring import ArtifactRegistry, WiringError


class _Code:
    """A stub code_id node with a fixed state, so only files drive staleness here."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[Any, Any] = {}

    @property
    def signature(self) -> str:
        return f"code:{self.name}"

    def state(self) -> str | None:
        return "v1"

    def load(self, is_product: bool = False) -> Any:
        return "v1"

    def save(self, value: Any) -> None:
        raise NotImplementedError


def test_register_output_and_resolve_input_share_one_node(tmp_path: Path) -> None:
    """Resolving a produced key as an input returns the identical node object."""
    reg = ArtifactRegistry(tmp_path, "run1")

    node = reg.register_output("caps/ethiopia.csv")

    assert reg.resolve_input("caps/ethiopia.csv") is node


def test_output_paths_are_namespaced_by_root_and_run(tmp_path: Path) -> None:
    """The output root and run name namespace produced paths; two runs do not collide."""
    one = ArtifactRegistry(tmp_path, "run1").register_output("x.csv")
    two = ArtifactRegistry(tmp_path, "run2").register_output("x.csv")

    assert one.path == (tmp_path / "run1" / "x.csv").resolve()
    assert two.path == (tmp_path / "run2" / "x.csv").resolve()
    assert one.path != two.path


def test_unresolved_input_is_reported_by_key(tmp_path: Path) -> None:
    """An input with no producer and no root-input file is unresolved, naming the key."""
    reg = ArtifactRegistry(tmp_path, "run1")

    with pytest.raises(WiringError, match="only_source"):
        reg.resolve_input("only_source")


def test_two_keys_one_path_is_a_definition_error(tmp_path: Path) -> None:
    """Two different keys resolving to one path raise, naming both keys and the path."""
    reg = ArtifactRegistry(tmp_path, "run1")
    reg.register_output("sub/x.csv")

    with pytest.raises(WiringError) as excinfo:
        reg.register_output("sub/../sub/x.csv")

    message = str(excinfo.value)
    assert "sub/x.csv" in message
    assert "sub/../sub/x.csv" in message
    assert "one path" in message


def test_mistyped_input_key_gets_a_did_you_mean(tmp_path: Path) -> None:
    """A mistyped input key is flagged with a suggestion from the registered keys."""
    reg = ArtifactRegistry(tmp_path, "run1")
    reg.register_output("caps/ethiopia.csv")

    with pytest.raises(WiringError, match="Did you mean 'caps/ethiopia.csv'"):
        reg.resolve_input("caps/ethiopa.csv")


def test_root_input_resolves_to_an_existing_source_file(tmp_path: Path) -> None:
    """A declared root input resolves to a file node for its source file."""
    source = tmp_path / "sources" / "bop.csv"
    source.parent.mkdir()
    source.write_text("x")
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"bop": source})

    assert reg.resolve_input("bop").path == source.resolve()


def test_declared_root_input_without_a_file_is_unresolved(tmp_path: Path) -> None:
    """A declared root input whose file is absent is unresolved, naming the key."""
    reg = ArtifactRegistry(
        tmp_path / "out", "run1", root_inputs={"bop": tmp_path / "gone.csv"}
    )

    with pytest.raises(WiringError, match="no file at"):
        reg.resolve_input("bop")


def test_root_input_colliding_with_an_output_path_is_a_definition_error(
    tmp_path: Path,
) -> None:
    """A root input resolving onto a produced key's path is caught, not silently merged."""
    out = tmp_path / "out"
    reg = ArtifactRegistry(out, "run1", root_inputs={"raw": out / "run1" / "collide.csv"})

    with pytest.raises(WiringError) as excinfo:
        reg.register_output("collide.csv")

    message = str(excinfo.value)
    assert "raw" in message
    assert "collide.csv" in message
    assert "one path" in message


def test_registry_wires_a_runnable_pipeline(tmp_path: Path) -> None:
    """Wire a two-step pipeline through shared keys, assemble a list, and run it."""
    raw = tmp_path / "raw.csv"
    raw.write_text("hello")
    out = tmp_path / "out"
    reg = ArtifactRegistry(out, "run1", root_inputs={"raw": raw})
    mid = reg.register_output("mid.csv")
    final = reg.register_output("final.csv")
    (out / "run1").mkdir(parents=True)

    assert reg.resolve_input("mid.csv") is mid  # the consumer gets the producer's node

    tasks = [
        Task(
            name="make_mid",
            run=f"cp {raw} {mid.path}",
            inputs={"raw": reg.resolve_input("raw")},
            outputs={"mid": mid},
            resources={},
            env=None,
            code_id=_Code("mid"),
        ),
        Task(
            name="make_final",
            run=f"cp {mid.path} {final.path}",
            inputs={"mid": reg.resolve_input("mid.csv")},
            outputs={"final": final},
            resources={},
            env=None,
            code_id=_Code("final"),
        ),
    ]

    assert outcomes(build(tasks, tmp_path / "state")) == {
        "make_mid": TaskOutcome.SUCCESS,
        "make_final": TaskOutcome.SUCCESS,
    }
    assert final.path.read_text() == "hello"  # data flowed producer -> consumer
