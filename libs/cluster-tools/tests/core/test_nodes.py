import hashlib
from pathlib import Path

import pytest

from vivarium.cluster_tools.core.backend.nodes import AlwaysStaleNode, file_node


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
