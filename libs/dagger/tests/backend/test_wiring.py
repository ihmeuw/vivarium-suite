"""Unit tests for the producer/consumer wiring registry (``backend.wiring``)."""

import os
from pathlib import Path
from typing import Any

import pytest
from pytask import TaskOutcome

from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.nodes import RunDirectoryNode
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry, WiringError


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


def test_register_output_rejects_a_root_input_key(tmp_path: Path) -> None:
    """Registering an output under a declared root-input key raises WiringError naming the key and its source path."""
    source = tmp_path / "delivered.csv"
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"coverage": source})

    with pytest.raises(WiringError) as excinfo:
        reg.register_output("coverage")

    message = str(excinfo.value)
    assert "coverage" in message
    assert str(source) in message


def test_key_for_returns_the_key_of_a_registered_output(tmp_path: Path) -> None:
    """key_for returns an output's key for its path, given as resolved or as relative."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    reg.register_output("caps/ethiopia.csv")
    resolved = reg.path_for("caps/ethiopia.csv")
    (tmp_path / "out").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "out", target_is_directory=True)
    paths: list[Path | str] = [
        resolved,
        str(resolved),
        os.path.relpath(resolved),
        tmp_path / "out" / "run1" / "caps" / ".." / "caps" / "ethiopia.csv",
        tmp_path / "link" / "run1" / "caps" / "ethiopia.csv",
    ]

    for path in paths:
        assert reg.key_for(path) == "caps/ethiopia.csv", path


def test_key_for_returns_the_key_of_a_root_input(tmp_path: Path) -> None:
    """key_for returns a root input's key for its file path."""
    source = tmp_path / "sources" / "bop.csv"
    source.parent.mkdir()
    source.write_text("x")
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"bop": source})

    assert reg.key_for(source) == "bop"
    assert reg.key_for(str(source)) == "bop"


def test_key_for_returns_none_for_an_unregistered_path(tmp_path: Path) -> None:
    """key_for returns None for a path no key is registered at, including an unregistered key's path_for."""
    source = tmp_path / "bop.csv"
    source.write_text("x")
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"bop": source})
    reg.register_output("x.csv")

    assert reg.key_for(tmp_path / "elsewhere.csv") is None
    assert reg.key_for(reg.path_for("y.csv")) is None
    assert reg.key_for(reg.path_for("x.csv").parent) is None


# An unresolved input names the keys that exist (MIC-7785)


def test_unresolved_input_with_no_close_key_lists_the_registered_keys_in_sorted_order(
    tmp_path: Path,
) -> None:
    """An input key with no close match ends its message with 'The keys registered so far are ...', listing the produced keys and the root-input keys together in sorted order, and has no did-you-mean."""
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"c": tmp_path / "c.csv"})
    reg.register_output("m.csv")
    reg.register_output("a.csv")

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("zzz")

    message = str(excinfo.value)
    assert message == (
        "input 'zzz' is unresolved: no registered producer and no root input. "
        "The keys registered so far are 'a.csv', 'c', 'm.csv'."
    )
    assert "Did you mean" not in message


def _registry_with_keys(tmp_path: Path, count: int) -> ArtifactRegistry:
    """Return a registry of ``count`` keys 'key_00.csv', ..., half of them root inputs."""
    keys = [f"key_{index:02d}.csv" for index in range(count)]
    root_inputs = {key: tmp_path / "sources" / key for key in keys[::2]}
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs=root_inputs)
    for key in reversed(keys[1::2]):
        reg.register_output(key)
    return reg


def test_unresolved_input_lists_the_first_ten_keys_and_counts_the_rest(
    tmp_path: Path,
) -> None:
    """With more than ten registered keys and none close, the message lists the first ten in sorted order, followed by ', and N more.' where N is the number left out."""
    reg = _registry_with_keys(tmp_path, 13)

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("zzz")

    listed = ", ".join(f"'key_{index:02d}.csv'" for index in range(10))
    message = str(excinfo.value)
    assert message.endswith(f" The keys registered so far are {listed}, and 3 more.")
    assert "key_10.csv" not in message
    assert "Did you mean" not in message


def test_unresolved_input_with_exactly_ten_keys_lists_all_of_them(tmp_path: Path) -> None:
    """With exactly ten registered keys and none close, the message lists all ten and gives no 'more' count."""
    reg = _registry_with_keys(tmp_path, 10)

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("zzz")

    listed = ", ".join(f"'key_{index:02d}.csv'" for index in range(10))
    message = str(excinfo.value)
    assert message.endswith(f" The keys registered so far are {listed}.")
    assert "more" not in message


def test_unresolved_input_with_no_registered_keys_says_none_are_registered(
    tmp_path: Path,
) -> None:
    """An input key resolved against an empty registry ends its message with 'No keys are registered so far.'"""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("zzz")

    assert str(excinfo.value) == (
        "input 'zzz' is unresolved: no registered producer and no root input. "
        "No keys are registered so far."
    )


def test_unresolved_input_with_a_close_key_keeps_the_did_you_mean_and_lists_no_keys(
    tmp_path: Path,
) -> None:
    """A mistyped key with a close match still ends with 'Did you mean '<key>'?', and the message does not list the registered keys."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    reg.register_output("caps/ethiopia.csv")
    reg.register_output("zzz_other.csv")

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("caps/ethiopa.csv")

    message = str(excinfo.value)
    assert message.endswith(" Did you mean 'caps/ethiopia.csv'?")
    assert "registered so far" not in message
    assert "zzz_other.csv" not in message


def test_declared_root_input_without_a_file_keeps_its_message_and_lists_no_keys(
    tmp_path: Path,
) -> None:
    """A declared root input whose file is absent is reported with 'no file at <path>', not with a list of registered keys."""
    gone = tmp_path / "gone.csv"
    reg = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={"bop": gone})
    reg.register_output("other.csv")

    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input("bop")

    message = str(excinfo.value)
    assert f"no file at {gone.resolve()}" in message
    assert "registered so far" not in message
    assert "other.csv" not in message


# A did-you-mean ignores a shared suffix (MIC-7785)


def _suggestion(tmp_path: Path, key: str, registered: list[str]) -> str:
    """Return the sentence after 'no root input. ' when ``key`` is resolved among ``registered``."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    for name in registered:
        reg.register_output(name)
    with pytest.raises(WiringError) as excinfo:
        reg.resolve_input(key)
    return str(excinfo.value).partition("no root input. ")[2]


@pytest.mark.parametrize(
    "key, registered, expected",
    [
        (
            "fertility.parquet",
            ["adults.parquet", "population.parquet"],
            "The keys registered so far are 'adults.parquet', 'population.parquet'.",
        ),
        (
            "adult.parquet",
            ["adults.parquet", "population.parquet"],
            "Did you mean 'adults.parquet'?",
        ),
        (
            "Nigeria/primary/ifa_coverge.csv",
            ["Nigeria/primary/ifa_coverage.csv", "Nigeria/primary/anc.csv"],
            "Did you mean 'Nigeria/primary/ifa_coverage.csv'?",
        ),
        (
            "Nigeria/primary/fertility.parquet",
            ["Nigeria/primary/adults.parquet", "Nigeria/primary/population.parquet"],
            "The keys registered so far are 'Nigeria/primary/adults.parquet', "
            "'Nigeria/primary/population.parquet'.",
        ),
        (
            "outputs/fertility.parquet",
            ["outputs/adults.parquet", "outputs/population.parquet"],
            "The keys registered so far are 'outputs/adults.parquet', "
            "'outputs/population.parquet'.",
        ),
        (
            "Nigeria/primry/anc.csv",
            ["Nigeria/primary/anc.csv", "Nigeria/primary/ifa_coverage.csv"],
            "Did you mean 'Nigeria/primary/anc.csv'?",
        ),
        (
            "adults.parqet",
            ["adults.parquet", "population.parquet"],
            "Did you mean 'adults.parquet'?",
        ),
    ],
    ids=[
        "unrelated-same-suffix",
        "close-stem",
        "close-nested-stem",
        "unrelated-nested-same-suffix",
        "unrelated-other-directory-same-suffix",
        "mistyped-directory",
        "mistyped-suffix",
    ],
)
def test_did_you_mean_judges_closeness_without_a_shared_suffix(
    tmp_path: Path, key: str, registered: list[str], expected: str
) -> None:
    """A key's stem is compared to the stems of the keys in its directory with its suffix, so a shared directory and '.parquet' alone suggest nothing; with no key sharing both, whole keys are compared."""
    assert _suggestion(tmp_path, key, registered) == expected


# Directory outputs (MIC-7754)


_RUN_KEY = "sims/toy"
"""A directory output's key."""

_RUN_ALIAS = "sims/../sims/toy"
"""A key spelled unlike _RUN_KEY that path_for resolves to the same path."""

_DONE = "_DONE"
"""A sentinel file name; the registry takes any name it is given."""


def _message(error: pytest.ExceptionInfo[WiringError]) -> str:
    """Return the message of a raised WiringError."""
    return str(error.value)


def test_register_directory_output_returns_a_run_directory_node_at_the_keys_path(
    tmp_path: Path,
) -> None:
    """register_directory_output returns a RunDirectoryNode whose path is path_for(key) and whose sentinel is the given name."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    node = reg.register_directory_output(_RUN_KEY, _DONE)

    assert isinstance(node, RunDirectoryNode)
    assert node.path == reg.path_for(_RUN_KEY)
    assert node.sentinel == _DONE


def test_register_directory_output_twice_returns_the_same_node(tmp_path: Path) -> None:
    """Registering one key as a directory output twice returns the identical node."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    first = reg.register_directory_output(_RUN_KEY, _DONE)

    assert reg.register_directory_output(_RUN_KEY, _DONE) is first


def test_resolve_input_of_a_directory_key_returns_the_identical_node(tmp_path: Path) -> None:
    """resolve_input of a key registered by register_directory_output returns the same node object, so the consumer gets the edge."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")

    node = reg.register_directory_output(_RUN_KEY, _DONE)

    assert reg.resolve_input(_RUN_KEY) is node


def test_key_for_the_run_directory_returns_its_key(tmp_path: Path) -> None:
    """key_for of the run directory's path, also given relative or through a symlink, returns the directory output's key."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    reg.register_directory_output(_RUN_KEY, _DONE)
    resolved = reg.path_for(_RUN_KEY)
    (tmp_path / "out").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "out", target_is_directory=True)
    paths: list[Path | str] = [
        resolved,
        str(resolved),
        os.path.relpath(resolved),
        tmp_path / "out" / "run1" / "sims" / ".." / "sims" / "toy",
        tmp_path / "link" / "run1" / "sims" / "toy",
    ]

    for path in paths:
        assert reg.key_for(path) == _RUN_KEY, path


def test_register_directory_output_refuses_a_root_input_key_with_register_outputs_message(
    tmp_path: Path,
) -> None:
    """A directory output on a declared root input's key raises WiringError with the same message register_output gives for that key."""
    source = tmp_path / "delivered"
    as_file = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={_RUN_KEY: source})
    as_directory = ArtifactRegistry(tmp_path / "out", "run1", root_inputs={_RUN_KEY: source})

    with pytest.raises(WiringError) as file_error:
        as_file.register_output(_RUN_KEY)
    with pytest.raises(WiringError) as directory_error:
        as_directory.register_directory_output(_RUN_KEY, _DONE)

    assert _message(directory_error) == _message(file_error)
    assert _RUN_KEY in _message(directory_error)


@pytest.mark.parametrize(
    "first_is_directory", [True, False], ids=["after-a-directory", "after-a-file"]
)
def test_register_directory_output_refuses_a_second_key_on_one_path(
    tmp_path: Path, first_is_directory: bool
) -> None:
    """A directory output whose key resolves to the path of a different registered key raises WiringError with the same message register_output gives."""
    as_file = ArtifactRegistry(tmp_path / "out", "run1")
    as_directory = ArtifactRegistry(tmp_path / "out", "run1")
    for reg in (as_file, as_directory):
        if first_is_directory:
            reg.register_directory_output(_RUN_KEY, _DONE)
        else:
            reg.register_output(_RUN_KEY)

    with pytest.raises(WiringError) as file_error:
        as_file.register_output(_RUN_ALIAS)
    with pytest.raises(WiringError) as directory_error:
        as_directory.register_directory_output(_RUN_ALIAS, _DONE)

    assert _message(directory_error) == _message(file_error)
    assert _RUN_KEY in _message(directory_error)
    assert _RUN_ALIAS in _message(directory_error)
    assert "one path" in _message(directory_error)


def test_register_directory_output_refuses_a_root_inputs_path(tmp_path: Path) -> None:
    """A directory output whose path is a declared root input's path raises WiringError with the same message register_output gives, naming the root input's key."""
    out = tmp_path / "out"
    root_inputs = {"raw": out / "run1" / _RUN_KEY}
    as_file = ArtifactRegistry(out, "run1", root_inputs=root_inputs)
    as_directory = ArtifactRegistry(out, "run1", root_inputs=root_inputs)

    with pytest.raises(WiringError) as file_error:
        as_file.register_output(_RUN_KEY)
    with pytest.raises(WiringError) as directory_error:
        as_directory.register_directory_output(_RUN_KEY, _DONE)

    assert _message(directory_error) == _message(file_error)
    assert "raw" in _message(directory_error)


def test_a_file_output_key_cannot_then_be_a_directory_output(tmp_path: Path) -> None:
    """register_directory_output of a key already registered by register_output raises WiringError naming the key."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    reg.register_output(_RUN_KEY)

    with pytest.raises(WiringError) as excinfo:
        reg.register_directory_output(_RUN_KEY, _DONE)

    assert _RUN_KEY in _message(excinfo)


def test_a_directory_output_key_cannot_then_be_a_file_output(tmp_path: Path) -> None:
    """register_output of a key already registered by register_directory_output raises WiringError naming the key, instead of returning the run directory node."""
    reg = ArtifactRegistry(tmp_path / "out", "run1")
    reg.register_directory_output(_RUN_KEY, _DONE)

    with pytest.raises(WiringError) as excinfo:
        reg.register_output(_RUN_KEY)

    assert _RUN_KEY in _message(excinfo)
