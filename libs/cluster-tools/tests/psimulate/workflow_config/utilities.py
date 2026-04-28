"""Utility functions for workflow_config tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def make_step_dict(**overrides: Any) -> dict[str, Any]:
    """Create a minimal valid step dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "name": "test_step",
        "command": "echo test",
        "resources": {
            "memory_gb": 4,
            "runtime": "01:00:00",
        },
    }
    defaults.update(overrides)
    return defaults


def make_workflow_dict(**overrides: Any) -> dict[str, Any]:
    """Create a valid minimal workflow config dict.

    Returns a dict with the ``workflow`` top-level key containing
    two steps: one structured (pytest) and one raw command.
    Override any top-level workflow field or replace ``steps`` entirely.
    """
    steps = overrides.pop("steps", None)
    if steps is None:
        steps = [
            {
                "name": "pre_tests",
                "command": "pytest tests/test_lbwsg.py tests/test_mortality.py --runslow",
                "resources": {
                    "memory_gb": 10,
                    "runtime": "01:00:00",
                },
            },
            {
                "name": "post_analysis",
                "command": "python scripts/analyze.py --input /results",
                "environment": "analysis_env",
                "resources": {
                    "memory_gb": 20,
                    "runtime": "02:00:00",
                    "cores": 2,
                },
            },
        ]

    workflow: dict[str, Any] = {
        "name": "test_pipeline",
        "project": "proj_simscience",
        "queue": "all.q",
        "output_directory": "/tmp/results",
        "steps": steps,
    }
    workflow.update(overrides)
    return {"workflow": workflow}


def write_workflow_yaml(tmp_path: Path, data: dict[str, Any]) -> Path:
    """Write a workflow config dict to a YAML file and return the path."""
    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(yaml.dump(data, sort_keys=False))
    return yaml_path


def make_simulation_step_dict(**overrides: Any) -> dict[str, Any]:
    """Create a minimal valid simulation step dict with sensible defaults.

    Returns a dict suitable for inclusion in a workflow's steps list.
    Override any field or provide additional fields like config, hardware, etc.
    """
    defaults: dict[str, Any] = {
        "name": "simulation",
        "type": "simulation",
        "model_specification": "/tmp/model_spec.yaml",
        "branch_configuration": "/tmp/branches.yaml",
        "resources": {
            "memory_gb": 5,
            "runtime": "03:00:00",
        },
    }
    defaults.update(overrides)
    return defaults


def write_psimulate_config(
    tmp_path: Path,
    model_specification: str | None = None,
    branch_configuration: str | None = None,
    artifact_path: str | None = None,
    **extra_fields: Any,
) -> Path:
    """Write a psimulate run config file and return the path.

    Creates a config file compatible with `psimulate run --run-config`.

    Parameters
    ----------
    tmp_path
        Temporary directory to write the config file.
    model_specification
        Path to model specification file.
    branch_configuration
        Path to branch configuration file.
    artifact_path
        Optional path to artifact file.
    extra_fields
        Additional fields to include in the config.

    Returns
    -------
        Path to the written config file.
    """
    config: dict[str, Any] = {}

    if model_specification is not None:
        config["model_specification"] = model_specification
    if branch_configuration is not None:
        config["branch_configuration"] = branch_configuration
    if artifact_path is not None:
        config["artifact_path"] = artifact_path

    config.update(extra_fields)

    config_path = tmp_path / "psimulate_config.yaml"
    config_path.write_text(yaml.dump(config, sort_keys=False))
    return config_path
