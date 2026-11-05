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
