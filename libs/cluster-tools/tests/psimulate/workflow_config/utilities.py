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
