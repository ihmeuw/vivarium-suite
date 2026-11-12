import hashlib
from pathlib import Path

import pytest
from pytask import PathNode

from vivarium.dagger.backend.nodes import (
    AlwaysStaleNode,
    RunDirectoryNode,
    file_node,
    run_directory_node,
)


def test_file_node_state_is_content_hash(tmp_path: Path) -> None:
    """Return the sha256 of the file contents, or None when the file is absent."""
    path = tmp_path / "data.csv"
    node = file_node(path)
    assert node.state() is None

    path.write_bytes(b"a,b\n1,2\n")

    assert node.state() == hashlib.sha256(b"a,b\n1,2\n").hexdigest()


def test_file_node_resolves_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give relative and absolute spellings of one file the same signature and path."""
    monkeypatch.chdir(tmp_path)

    relative, absolute = file_node("sub/../data.csv"), file_node(tmp_path / "data.csv")

    assert relative.path == absolute.path == (tmp_path / "data.csv").resolve()
    assert relative.signature == absolute.signature


def test_always_stale_node_state_changes_every_call() -> None:
    """Return a different state on each call, so the node is never seen as unchanged."""
    node = AlwaysStaleNode(name="step::code")

    assert len({node.state() for _ in range(5)}) == 5


def test_always_stale_node_signature_is_stable_and_name_scoped() -> None:
    """Keep one signature across calls, shared only with same-named nodes."""
    node = AlwaysStaleNode(name="step::code")

    assert node.signature == node.signature == AlwaysStaleNode(name="step::code").signature
    assert node.signature != AlwaysStaleNode(name="other::code").signature


def test_always_stale_node_rejects_save() -> None:
    """Reject saves: it is a dependency, not a product."""
    with pytest.raises(NotImplementedError):
        AlwaysStaleNode(name="step::code").save("x")


# RunDirectoryNode (MIC-7754)

_SENTINEL = "_DONE"
"""A sentinel file name for these tests; the node takes any name it is given."""


def _run_directory(tmp_path: Path) -> Path:
    """Create a run directory with a results file and a sentinel holding b'first'."""
    directory = tmp_path / "run"
    (directory / "results").mkdir(parents=True)
    (directory / "results" / "a.parquet").write_bytes(b"results")
    (directory / _SENTINEL).write_bytes(b"first")
    return directory


def test_run_directory_node_state_is_the_sentinel_content_hash(tmp_path: Path) -> None:
    """A run directory node's state is the sha256 hex digest of its sentinel file's bytes, and changes when the sentinel's content changes."""
    directory = _run_directory(tmp_path)
    node = run_directory_node(directory, _SENTINEL)

    assert node.state() == hashlib.sha256(b"first").hexdigest()

    (directory / _SENTINEL).write_bytes(b"second")

    assert node.state() == hashlib.sha256(b"second").hexdigest()


def test_run_directory_node_state_ignores_the_other_files_in_the_directory(
    tmp_path: Path,
) -> None:
    """Adding, changing or removing a file in the run directory other than the sentinel leaves the node's state unchanged."""
    directory = _run_directory(tmp_path)
    node = run_directory_node(directory, _SENTINEL)
    before = node.state()

    (directory / "results" / "b.parquet").write_bytes(b"added")
    assert node.state() == before
    (directory / "results" / "a.parquet").write_bytes(b"changed")
    assert node.state() == before
    (directory / "results" / "a.parquet").unlink()
    assert node.state() == before
    assert before is not None


def test_run_directory_node_state_is_none_without_the_sentinel(tmp_path: Path) -> None:
    """A run directory that exists but holds no sentinel file has the state None."""
    directory = _run_directory(tmp_path)
    (directory / _SENTINEL).unlink()

    assert run_directory_node(directory, _SENTINEL).state() is None


def test_run_directory_node_state_is_none_when_the_directory_is_missing(
    tmp_path: Path,
) -> None:
    """A run directory node whose directory does not exist has the state None."""
    assert run_directory_node(tmp_path / "missing", _SENTINEL).state() is None


def test_run_directory_node_state_is_none_when_the_path_is_a_file(tmp_path: Path) -> None:
    """A run directory node whose path is a regular file has the state None instead of raising NotADirectoryError."""
    path = tmp_path / "run"
    path.write_bytes(b"a file, not a directory")

    assert run_directory_node(path, _SENTINEL).state() is None


def test_run_directory_node_load_returns_the_directory_path(tmp_path: Path) -> None:
    """load() and load(is_product=True) return the run directory's resolved Path."""
    directory = _run_directory(tmp_path)
    node = run_directory_node(directory, _SENTINEL)

    assert node.load() == directory.resolve()
    assert node.load(is_product=True) == directory.resolve()
    assert isinstance(node.load(), Path)


@pytest.mark.parametrize("value", ["text", b"bytes", 1, ["a", "list"]])
def test_run_directory_node_rejects_save_with_a_type_error(
    tmp_path: Path, value: object
) -> None:
    """save() raises TypeError for a str, bytes or any other value, and writes nothing."""
    directory = _run_directory(tmp_path)
    before = sorted(
        (path.relative_to(tmp_path), path.read_bytes())
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    node = run_directory_node(directory, _SENTINEL)

    with pytest.raises(TypeError):
        node.save(value)

    after = sorted(
        (path.relative_to(tmp_path), path.read_bytes())
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert after == before


@pytest.mark.parametrize("relative", ["sub/../run", Path("sub/../run")])
def test_run_directory_node_resolves_its_path_and_is_named_by_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: Path | str
) -> None:
    """run_directory_node given a relative path resolves it, names the node by the resolved path in POSIX form, and keeps the sentinel name."""
    monkeypatch.chdir(tmp_path)
    resolved = (tmp_path / "run").resolve()

    node = run_directory_node(relative, _SENTINEL)

    assert node.path == resolved
    assert node.name == resolved.as_posix()
    assert node.sentinel == _SENTINEL


def test_run_directory_node_is_a_path_node_with_a_path_nodes_signature(
    tmp_path: Path,
) -> None:
    """A run directory node is a pytask PathNode whose signature equals that of a PathNode at the same path."""
    directory = _run_directory(tmp_path).resolve()

    node = run_directory_node(directory, _SENTINEL)

    assert isinstance(node, PathNode)
    assert isinstance(node, RunDirectoryNode)
    assert node.signature == PathNode.from_path(directory).signature
