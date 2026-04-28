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
