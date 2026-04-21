"""Shared fixtures for the workflow_config test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.psimulate.workflow_config.utilities import make_workflow_dict, write_workflow_yaml


@pytest.fixture()
def valid_workflow_dict() -> dict[str, Any]:
    """A valid minimal workflow config dict."""
    return make_workflow_dict()


@pytest.fixture()
def valid_workflow_yaml(tmp_path: Path, valid_workflow_dict: dict[str, Any]) -> Path:
    """Write the valid workflow dict to a YAML file and return the path."""
    return write_workflow_yaml(tmp_path, valid_workflow_dict)
