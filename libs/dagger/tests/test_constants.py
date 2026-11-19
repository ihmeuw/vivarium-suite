"""Tests for ``vivarium.dagger.constants``."""

import subprocess
import sys
from typing import Any

import pytest

from vivarium.dagger.constants import DEFAULT_RESOURCES

_EXPECTED = {"queue": "all.q", "memory": 1, "runtime": 3600, "cores": 1}
"""The compute resources that the constants module ships."""

_IMPORT_CONSTANTS = """
import sys

import vivarium.dagger.constants

loaded = {name.partition(".")[0] for name in sys.modules}
print(sorted(loaded & {"pytask", "_pytask", "jobmon"}))
"""
"""A script that imports the constants module and prints which of pytask and jobmon it
imported."""


def test_default_resources_are_all_q_one_gigabyte_an_hour_and_one_core_with_no_project() -> (
    None
):
    """DEFAULT_RESOURCES is exactly {'queue': 'all.q', 'memory': 1, 'runtime': 3600, 'cores': 1}, with no 'project' key."""
    assert dict(DEFAULT_RESOURCES) == _EXPECTED
    assert "project" not in DEFAULT_RESOURCES


def test_default_resources_cannot_be_changed() -> None:
    """Assigning or deleting a key of DEFAULT_RESOURCES raises TypeError, and the mapping is unchanged afterwards."""
    # Typed as Any, because a Mapping has no item assignment for mypy to accept.
    resources: Any = DEFAULT_RESOURCES

    with pytest.raises(TypeError):
        resources["queue"] = "long.q"
    with pytest.raises(TypeError):
        resources["project"] = "proj_test"
    with pytest.raises(TypeError):
        del resources["memory"]

    assert dict(DEFAULT_RESOURCES) == _EXPECTED


def test_importing_the_constants_module_imports_neither_pytask_nor_jobmon() -> None:
    """Importing vivarium.dagger.constants in a fresh interpreter leaves pytask and jobmon out of sys.modules."""
    completed = subprocess.run(
        [sys.executable, "-c", _IMPORT_CONSTANTS],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout
