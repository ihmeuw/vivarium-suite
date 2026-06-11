"""
======================
Workflow Configuration
======================

Top-level workflow fields that must appear in the YAML file rather than a CLI override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
from vivarium.cluster_tools.core.cluster.validation import (
    validate_hardware,
    validate_project,
    validate_runtime_and_queue,
)

REQUIRED_WORKFLOW_FIELDS = {"steps"}
"""Top-level workflow fields that must appear in the YAML file.

``name``, ``project``, ``queue``, and ``output_directory`` are each
required overall but may be provided via either the YAML or a CLI
override; their presence is validated by
:func:`~vivarium.cluster_tools.dagger.config.parsing.load_workflow_config`
rather than by :meth:`WorkflowConfig.parse_yaml_file`."""

DEFAULT_MAX_ATTEMPTS = 2


DEFAULT_BACKUP_FREQ_SECONDS = 30.0 * 60.0
"""Default backup frequency in seconds (30 minutes), matching ``psimulate run``."""


@dataclass
class ResourceConfig:
    """Compute resource specification for a workflow step."""

    memory_gb: int
    """Memory in GB."""
    project: str | None = None
    """Cluster project to charge. Falls back to the workflow-level project."""
    queue: str | None = None
    """Cluster queue to submit to. Falls back to the workflow-level queue."""
    runtime: str = "01:00:00"
    """Maximum runtime in ``hh:mm:ss`` format. Default is ``01:00:00``."""
    cores: int = 1
    """Number of CPU cores to request. Default is 1."""
    hardware: list[str] | None = None
    """Optional list of hardware types to target (e.g. ``["r650", "r650v2"]``)."""
    requires_archive_node: bool = False
    """Whether to enforce landing on an archive node."""

    _RUNTIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    def __post_init__(self) -> None:
        if not self._RUNTIME_RE.match(self.runtime):
            raise ValueError(
                f"Invalid runtime '{self.runtime}'. Expected format ``hh:mm:ss``."
            )
        if self.project is not None:
            validate_project(self.project)
        if self.queue is not None or self.project is not None:
            # Validate runtime against queue if queue is specified at step level
            if self.queue is not None:
                validate_runtime_and_queue(self.runtime, self.queue)
        if self.hardware is not None:
            validate_hardware(self.hardware)
        if not isinstance(self.requires_archive_node, bool):
            raise TypeError(
                f"'requires_archive_node' must be a bool, "
                f"got {type(self.requires_archive_node).__name__}: "
                f"{self.requires_archive_node!r}."
            )

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        workflow_project: str | None = None,
        workflow_queue: str | None = None,
    ) -> ResourceConfig:
        """Create a ResourceConfig from a dictionary.

        Step-level values take precedence; workflow-level defaults fill in
        any that are absent.

        Parameters
        ----------
        data
            Resource dictionary from a step's ``resources`` section.
        workflow_project
            Workflow-level project used as fallback.
        workflow_queue
            Workflow-level queue used as fallback.
        """
        kwargs: dict[str, Any] = {"memory_gb": data["memory_gb"]}
        kwargs["project"] = data.get("project") or workflow_project
        kwargs["queue"] = data.get("queue") or workflow_queue
        if "runtime" in data:
            kwargs["runtime"] = data["runtime"]
        if "cores" in data:
            kwargs["cores"] = data["cores"]
        if "hardware" in data:
            kwargs["hardware"] = data["hardware"]
        if "requires_archive_node" in data:
            kwargs["requires_archive_node"] = data["requires_archive_node"]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary, omitting None values and defaults."""
        result: dict[str, Any] = {
            "memory_gb": self.memory_gb,
            "runtime": self.runtime,
        }
        if self.project is not None:
            result["project"] = self.project
        if self.queue is not None:
            result["queue"] = self.queue
        if self.cores != 1:  # Only include if not default
            result["cores"] = self.cores
        if self.hardware is not None:
            result["hardware"] = self.hardware
        if self.requires_archive_node:
            result["requires_archive_node"] = True
        return result

    def to_native_specification(self, job_name: str) -> NativeSpecification:
        """Convert to a :class:`~vivarium.cluster_tools.core.cluster.interface.NativeSpecification` for Jobmon task submission.

        Parameters
        ----------
        job_name
            The SLURM job name for this step's tasks.
        """
        if not isinstance(self.project, str) or not isinstance(self.queue, str):
            raise TypeError(
                f"'project' and 'queue' must be strings, got "
                f"project={self.project!r}, queue={self.queue!r}."
            )
        return NativeSpecification(
            job_name=job_name,
            project=self.project,
            queue=self.queue,
            peak_memory=float(self.memory_gb),
            max_runtime=self.runtime,
            hardware=self.hardware or [],
            cores=self.cores,
            requires_archive_node=self.requires_archive_node,
        )


@dataclass(frozen=True)
class ParsedStep:
    """A parsed workflow step ready to be passed to an interface API function.

    Produced by
    :func:`~vivarium.cluster_tools.dagger.config.parsing.parse_step_from_yaml`.
    Holds the *inputs* to the matching ``get_*_step_tasks`` function (in
    ``api_kwargs``) plus the step type tag used to dispatch task building
    and YAML serialization.
    """

    step_type: str
    """One of "bash", "simulation", "pytest", "python", "notebook"."""
    name: str
    """The step's unique name within the workflow."""
    api_kwargs: dict[str, Any]
    """Kwargs ready to send into the matching interface API function. Excludes
    ``tool`` and ``is_resume``, which are supplied by the builder."""


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
    """Directory where workflow outputs will be stored. Both relative and absolute
    paths are accepted."""
    default_environment: str | None
    """Default environment to use for steps that do not specify one."""
    steps: list[ParsedStep]
    """Parsed workflow steps, each carrying the kwargs needed by the matching
    interface API function."""
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    """Maximum number of Jobmon task attempts. Default is 2."""

    def __post_init__(self) -> None:
        """Validate workflow-level constraints."""
        validate_project(self.project)
        # Uses a placeholder value for runtime
        validate_runtime_and_queue("01:00:00", self.queue)  # validate queue value
        # Unique step names
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(
                f"Step names must be unique. Duplicate names found: {[name for name in names if names.count(name) > 1]}"
            )

    @staticmethod
    def parse_yaml_file(path: Path) -> dict[str, Any]:
        """Read and perform basic structural validation on a workflow YAML file.

        Returns the ``workflow`` dict from inside the top-level key.

        Parameters
        ----------
        path
            Path to the YAML file.

        Raises
        ------
        ValueError
            If the file does not contain a top-level workflow key, if required workflow-level
            fields are missing, or if the workflow 'steps' list is empty.

        Returns
        -------
        The raw workflow dictionary from the YAML file, without any further parsing or
        validation.
        """
        with path.open() as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict) or "workflow" not in raw:
            raise ValueError(
                "Workflow configuration: missing required top-level 'workflow' key."
            )

        workflow: dict[str, Any] = raw["workflow"]

        # Check required top-level fields
        for field_name in REQUIRED_WORKFLOW_FIELDS:
            if field_name not in workflow:
                raise ValueError(
                    f"Workflow configuration: missing required field '{field_name}'."
                )

        raw_steps = workflow["steps"]
        if not raw_steps:
            raise ValueError("Workflow configuration: 'steps' list must not be empty.")

        return workflow
