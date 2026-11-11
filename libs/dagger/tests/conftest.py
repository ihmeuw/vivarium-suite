"""Fixtures shared by the tests of every subpackage."""

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def umask_022() -> Iterator[None]:
    """Set the process's umask to 0o022 for the test, and restore it after."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def without_bridge_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the ``pytask`` entry points look as if the bridge's plugin were not registered."""
    from importlib.metadata import EntryPoint

    import vivarium.dagger.backend.build as build_module

    real = build_module.entry_points

    def without_bridge(group: str) -> list[EntryPoint]:
        """Return ``group``'s entry points except the bridge's plugin."""
        return [
            point for point in real(group=group) if point.value != build_module.BRIDGE_PLUGIN
        ]

    monkeypatch.setattr(build_module, "entry_points", without_bridge)
