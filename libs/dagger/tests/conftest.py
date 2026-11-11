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
    import vivarium.dagger.backend.build as build_module

    real = build_module.entry_points
    monkeypatch.setattr(
        build_module,
        "entry_points",
        lambda **kwargs: [
            point for point in real(**kwargs) if point.value != build_module.BRIDGE_PLUGIN
        ],
    )
