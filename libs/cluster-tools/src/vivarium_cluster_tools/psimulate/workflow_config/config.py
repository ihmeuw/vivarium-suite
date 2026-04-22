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

REQUIRED_WORKFLOW_FIELDS = {"name", "steps"}

DEFAULT_MAX_ATTEMPTS = 2

VALID_PROJECTS = {"proj_simscience", "proj_simscience_prod"}
VALID_QUEUES = {"all.q", "long.q"}


@dataclass
class ResourceConfig:
    """Compute resource specification for a workflow step."""

    memory_gb: int
    """Memory in GB."""
    runtime: str = "01:00:00"
    """Maximum runtime in ``hh:mm:ss`` format. Default is ``01:00:00``."""
    cores: int = 1
    """Number of CPU cores to request. Default is 1."""

    _RUNTIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    def __post_init__(self) -> None:
        if not self._RUNTIME_RE.match(self.runtime):
            raise ValueError(
                f"Invalid runtime '{self.runtime}'. Expected format ``hh:mm:ss``."
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceConfig:
        """Create a ResourceConfig from a dictionary."""
        kwargs: dict[str, Any] = {"memory_gb": data["memory_gb"]}
        if "runtime" in data:
            kwargs["runtime"] = data["runtime"]
        if "cores" in data:
            kwargs["cores"] = data["cores"]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary, omitting None values and default cores."""
        result: dict[str, Any] = {}
        if self.memory_gb is not None:
            result["memory_gb"] = self.memory_gb
        if self.runtime is not None:
            result["runtime"] = self.runtime
        if self.cores != 1:  # Only include if not default
            result["cores"] = self.cores
        return result


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary, omitting None values."""
        result: dict[str, Any] = {"name": self.name}

        result["command"] = self.command

        # Add environment if specified
        if self.environment is not None:
            result["environment"] = self.environment

        # Add resources if specified and non-empty
        if self.resources is not None:
            resources_dict = self.resources.to_dict()
            if resources_dict:  # Only add if there are non-default values
                result["resources"] = resources_dict

        return result


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
    """List of sequential steps in the workflow."""
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    """Maximum number of Jobmon task attempts. Default is 2."""

    @staticmethod
    def _parse_yaml_file(path: Path) -> dict[str, Any]:
        """Read and perform basic structural validation on a workflow YAML file.

        Returns the ``workflow`` dict from inside the top-level key.

        Parameters
        ----------
        path
            Path to the YAML file.
        """
        with path.open() as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict) or "workflow" not in raw:
            raise KeyError("Workflow configuration must contain a top-level 'workflow' key.")

        workflow: dict[str, Any] = raw["workflow"]

        # Check required top-level fields
        for field_name in REQUIRED_WORKFLOW_FIELDS:
            if field_name not in workflow:
                raise KeyError(
                    f"Workflow configuration is missing required field '{field_name}'."
                )

        raw_steps = workflow["steps"]
        if not raw_steps:
            raise KeyError("Workflow 'steps' must not be empty.")

        return workflow

    @staticmethod
    def _parse_steps(raw_steps: list[dict[str, Any]]) -> list[StepConfig]:
        """Parse a list of raw step dicts into ``StepConfig`` objects."""
        steps = []
        for step_dict in raw_steps:
            step = StepConfig(
                name=step_dict["name"],
                resources=ResourceConfig.from_dict(step_dict["resources"]),
                command=step_dict["command"],
                environment=step_dict.get("environment"),
            )
            steps.append(step)
        return steps

    @classmethod
    def from_yaml_with_cli_overrides(
        cls,
        path: Path,
        *,
        project: str | None = None,
        queue: str | None = None,
        output_directory: Path | None = None,
        max_attempts: int | None = None,
    ) -> WorkflowConfig:
        """Load a WorkflowConfig from YAML, merging CLI overrides.

        CLI arguments take precedence over values in the YAML file.
        Validates that ``project``, ``queue``, and ``output_directory`` are provided
        by at least one source

        Parameters
        ----------
        path
            Path to the workflow YAML configuration file.
        project
            CLI override for the project field.
        queue
            CLI override for the queue field.
        output_directory
            CLI override for the output directory.
        max_attempts
            CLI override for the maximum number of Jobmon task attempts.

        Raises
        ------
        KeyError
            If ``project``, ``queue``, or ``output_directory`` cannot be resolved
            from either the YAML file or CLI arguments.
        """
        workflow = cls._parse_yaml_file(path)
        steps = cls._parse_steps(workflow["steps"])

        resolved_project = project or workflow.get("project")
        resolved_queue = queue or workflow.get("queue")
        resolved_output_directory = output_directory or (
            Path(workflow["output_directory"]) if "output_directory" in workflow else None
        )

        if not resolved_project:
            raise KeyError(
                "Project is required. Provide it in the config file or via --project/-P."
            )
        if not resolved_queue:
            raise KeyError(
                "Queue is required. Provide it in the config file or via --queue/-q."
            )
        if not resolved_output_directory:
            raise KeyError(
                "Output directory is required. Provide it in the config file "
                "or via --output-directory/-o."
            )

        return cls(
            name=workflow["name"],
            project=resolved_project,
            queue=resolved_queue,
            output_directory=resolved_output_directory,
            default_environment=workflow.get("default_environment"),
            steps=steps,
            max_attempts=max_attempts or workflow.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
        )

    def __post_init__(self) -> None:
        """Validate workflow-level constraints."""
        # Validate project
        if self.project not in VALID_PROJECTS:
            raise ValueError(
                f"Invalid project '{self.project}'. "
                f"Must be one of: {sorted(VALID_PROJECTS)}."
            )
        # Validate queue
        if self.queue not in VALID_QUEUES:
            raise ValueError(
                f"Invalid queue '{self.queue}'. " f"Must be one of: {sorted(VALID_QUEUES)}."
            )
        # Unique step names
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise KeyError(
                f"Step names must be unique. Duplicate names found: {[name for name in names if names.count(name) > 1]}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary suitable for YAML output."""
        result: dict[str, Any] = {
            "name": self.name,
            "project": self.project,
            "queue": self.queue,
            "output_directory": str(self.output_directory),
            "max_attempts": self.max_attempts,
        }
        if self.default_environment is not None:
            result["default_environment"] = self.default_environment
        result["steps"] = [step.to_dict() for step in self.steps]

        return result
