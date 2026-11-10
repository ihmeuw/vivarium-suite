"""Fixtures shared by the frontend tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.frontend.notebooks import isolated_jupyter_dirs


@pytest.fixture
def jupyter_dirs(tmp_path: Path) -> Iterator[None]:
    """Point IPython and Jupyter at empty directories, so no kernel reads or writes HOME."""
    with isolated_jupyter_dirs(tmp_path / "jupyter"):
        yield
