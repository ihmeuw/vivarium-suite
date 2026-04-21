"""
========================
Workflow Config Parser
========================

Parse and validate workflow YAML configuration files.

"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_WORKFLOW_FIELDS = {"name", "project", "queue", "output_directory", "steps"}


@dataclass
class ResourceConfig:
    """Compute resource specification for a workflow step."""

    memory_gb: int
    """Memory in GB."""
    runtime: str = "01:00:00"
    """Maximum runtime in 'hh:mm:ss' format."""
    cores: int = 1
    """Number of CPU cores to request. Default is 1."""

    _RUNTIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    def __post_init__(self) -> None:
        if not self._RUNTIME_RE.match(self.runtime):
            raise ValueError(f"Invalid runtime '{self.runtime}'. Expected format 'hh:mm:ss'.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceConfig:
        """Create a ResourceConfig from a dictionary."""
        return cls(
            memory_gb=data["memory_gb"],
            runtime=data.get("runtime", "01:00:00"),
            cores=data.get("cores", 1),
        )


@dataclass
class StepConfig:
    """Configuration for a single workflow step."""

    name: str
    """Unique name for this step within the workflow."""
    resources: ResourceConfig
    """Resource configuration for this step."""
    command: str
    """Command string to execute for this step."""
    environment: str | None = None
    """Optional environment name to use for this step."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Step 'name' is required.")
        if not self.resources:
            raise ValueError(f"Step '{self.name}': 'resources' is required.")
        if not self.command:
            raise ValueError(f"Step '{self.name}': 'command' is required.")


@dataclass
class WorkflowConfig:
    """Parsed and validated workflow configuration."""

    name: str
    """Name of the workflow. This is what will be displayed in Jobmon"""
    project: str
    """Project that this workflow will be run under. E.g. 'proj_simscience'."""
    queue: str
    """Queue to submit the workflow to."""
    output_directory: Path
    """Directory where workflow outputs will be stored."""
    default_environment: str | None
    """Default environment to use for steps that do not specify one."""
    steps: list[StepConfig]
    """List of steps in the workflow."""

    @classmethod
    def from_yaml(cls, path: Path) -> WorkflowConfig:
        """Load, validate, and return a WorkflowConfig from a YAML file."""
        with path.open() as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict) or "workflow" not in raw:
            raise KeyError("Workflow configuration must contain a top-level 'workflow' key.")

        workflow = raw["workflow"]

        # Check required top-level fields
        for field_name in REQUIRED_WORKFLOW_FIELDS:
            if field_name not in workflow:
                raise KeyError(
                    f"Workflow configuration is missing required field '{field_name}'."
                )

        raw_steps = workflow["steps"]
        if not raw_steps:
            raise KeyError("Workflow 'steps' must not be empty.")

        steps = []
        for step_dict in raw_steps:
            step = StepConfig(
                name=step_dict["name"],
                resources=ResourceConfig.from_dict(step_dict["resources"]),
                command=step_dict["command"],
                environment=step_dict.get("environment"),
            )
            steps.append(step)

        config = cls(
            name=workflow["name"],
            project=workflow["project"],
            queue=workflow["queue"],
            output_directory=Path(workflow["output_directory"]),
            default_environment=workflow.get("default_environment"),
            steps=steps,
        )
        return config

    def __post_init__(self) -> None:
        """Validate workflow-level constraints."""
        # Unique step names
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise KeyError(
                f"Step names must be unique. Duplicate names found: {[name for name in names if names.count(name) > 1]}"
            )
