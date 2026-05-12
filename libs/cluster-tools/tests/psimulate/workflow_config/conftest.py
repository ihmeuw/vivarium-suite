"""Shared fixtures for the workflow_config test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.psimulate.workflow_config.utilities import (
    make_simulation_step_dict,
    make_workflow_dict,
    write_workflow_yaml,
)


@pytest.fixture(scope="session")
def valid_pytest_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A valid test directory path for pytest steps."""
    path = tmp_path_factory.mktemp("test_dir") / "unit"
    path.mkdir()
    return str(path)


@pytest.fixture(scope="session")
def valid_pytest_paths(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Multiple valid test directory paths for pytest steps."""
    base = tmp_path_factory.mktemp("test_dirs")
    unit = base / "unit"
    unit.mkdir()
    integration = base / "integration"
    integration.mkdir()
    return [str(unit), str(integration)]


@pytest.fixture(scope="session")
def valid_python_script(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A valid Python script path for python steps."""
    script = tmp_path_factory.mktemp("scripts") / "run.py"
    script.write_text("")
    return str(script)


@pytest.fixture(scope="session")
def valid_notebook_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A valid notebook path for notebook steps."""
    notebook = tmp_path_factory.mktemp("notebooks") / "analysis.ipynb"
    notebook.write_text("")
    return notebook


@pytest.fixture()
def valid_workflow_dict() -> dict[str, Any]:
    """A valid minimal workflow config dict."""
    return make_workflow_dict()


@pytest.fixture()
def valid_workflow_yaml(tmp_path: Path, valid_workflow_dict: dict[str, Any]) -> Path:
    """Write the valid workflow dict to a YAML file and return the path."""
    return write_workflow_yaml(tmp_path, valid_workflow_dict)


@pytest.fixture()
def simulation_step_dict() -> dict[str, Any]:
    """A valid minimal simulation step dict."""
    return make_simulation_step_dict()


@pytest.fixture()
def valid_model_spec_file(tmp_path: Path) -> Path:
    """Create a valid model specification file."""
    model_spec = tmp_path / "model_spec.yaml"
    model_spec.write_text("configuration:\n  time:\n    start:\n      year: 2020\n")
    return model_spec


@pytest.fixture()
def valid_branch_config_file(tmp_path: Path) -> Path:
    """Create a valid branch configuration file."""
    branch_config = tmp_path / "branches.yaml"
    branch_config.write_text("branches:\n  - scenario: baseline\n")
    return branch_config


@pytest.fixture()
def valid_artifact_file(tmp_path: Path) -> Path:
    """Create a valid artifact file."""
    artifact = tmp_path / "artifact.hdf"
    artifact.write_text("mock artifact data")
    return artifact
