"""
========================
Workflow Config Parser
========================

Parse and validate workflow YAML configuration files.

"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from vivarium_cluster_tools.psimulate import COMMANDS, branches
from vivarium_cluster_tools.psimulate.cluster.interface import NativeSpecification
from vivarium_cluster_tools.psimulate.cluster.validation import (
    validate_hardware,
    validate_project,
    validate_runtime_and_queue,
)
from vivarium_cluster_tools.psimulate.jobmon_config.workflow import get_task_list
from vivarium_cluster_tools.psimulate.jobs import (
    BackupConfiguration,
    build_job_parameters_from_keyspace,
)
from vivarium_cluster_tools.psimulate.paths import OutputPaths

if TYPE_CHECKING:
    from jobmon.client.api import Tool
    from jobmon.client.task import Task

REQUIRED_WORKFLOW_FIELDS = {"name", "steps"}

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
        return result

    def to_native_specification(self, job_name: str) -> NativeSpecification:
        """Convert to a :class:`~vivarium_cluster_tools.psimulate.cluster.interface.NativeSpecification` for Jobmon task submission.

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
        )


class BaseStepConfig(ABC):
    """Abstract base class for all workflow step configurations.

    Defines the interface that all step types must implement. Concrete
    subclasses **must** be decorated with ``@dataclass`` so that
    ``__post_init__`` is called automatically after ``__init__``.

    The base class provides a concrete __post_init__ that performs common
    validation and then calls the abstract _validate() method for
    subclass-specific validation.
    """

    name: str
    resources: ResourceConfig
    output_directory: Path
    environment: str | None

    def __post_init__(self) -> None:
        """Common validation for all step types, then call subclass validation.

        This method is called automatically by @dataclass after __init__.
        It performs validation common to all steps, then dispatches to the
        subclass-specific _validate() method.
        """
        if not hasattr(self, "__dataclass_fields__"):
            raise TypeError(f"{type(self).__name__} must be decorated with @dataclass.")
        if not self.name:
            raise ValueError("Step 'name' is required.")
        if not self.resources:
            raise ValueError(f"Step '{self.name}': 'resources' is required.")
        if not isinstance(self.resources.queue, str) or not isinstance(
            self.resources.project, str
        ):
            raise ValueError(
                f"Step '{self.name}': resources 'queue' and 'project' must be "
                "configured. Set them at the step level or provide workflow-level defaults."
            )

        # Call subclass-specific validation
        self._validate()

        # Build the Jobmon-facing resource specification once at construction.
        self.native_specification = self.resources.to_native_specification(self.name)

    @abstractmethod
    def _validate(self) -> None:
        """Subclass-specific validation logic.

        Called at the end of __post_init__ after common validation.
        Subclasses should validate their type-specific fields here.
        This includes checking that any provided arguments are in the
        set returned by supported_arguments().
        """
        pass

    @abstractmethod
    def supported_arguments(self) -> set[str] | None:
        """Return the set of argument names valid in the 'args' section.

        For command-based steps (no 'type' field), returns None since they
        don't have an 'args' section - they just have a 'command' field.

        For typed steps (with 'type' field), returns the set of valid keys
        that can appear in the 'args' section. These correspond to CLI options
        users can pass to that step type's command.

        The _validate() method should check that any provided args are in this
        set (for typed steps) or that no args are provided (for command steps).

        Returns
        -------
            None for command-based steps, or set of supported argument names
            for typed steps (e.g., {"config", "model_specification",
            "branch_configuration", "artifact_path", "hardware", ...}).
        """
        pass

    @classmethod
    @abstractmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        output_directory: Path,
        *,
        project: str,
        queue: str,
    ) -> BaseStepConfig:
        """Create a step config from a raw YAML dictionary.

        Parameters
        ----------
        data
            Dictionary from workflow YAML.
        output_directory
            Workflow-level output directory.
        project
            Workflow-level project for resource resolution.
        queue
            Workflow-level queue for resource resolution.

        Returns
        -------
            A new step config instance.
        """
        pass

    @abstractmethod
    def get_tasks(
        self,
        tool: Tool,
        *,
        env: str,
        build_timestamp: str,
    ) -> list[Task]:
        """Create Jobmon Tasks for this step.

        Returns one or more tasks to add to the workflow. Command steps
        return a single task; simulation steps return one task per
        (draw, seed, branch) combination.

        Resources (including project and queue) are read from
        ``self.resources``, which has workflow-level defaults resolved
        at construction time via :meth:`ResourceConfig.from_dict`.

        Parameters
        ----------
        tool
            The Jobmon Tool instance to create task templates from.
        env
            Conda environment name to wrap the command with.
        build_timestamp
            Stable timestamp string (``YYYY_MM_DD_HH_MM_SS``) generated once
            per workflow build. Steps that create output directories should
            use this to ensure paths are deterministic across resume builds.

        Returns
        -------
            A list of Jobmon Task instances ready to be added to a workflow.
        """
        pass

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize the step configuration to a dictionary.

        Returns a dict suitable for writing to a workflow YAML file.

        Returns
        -------
            Dictionary representation of the step configuration.
        """
        pass


@dataclass
class CommandStepConfig(BaseStepConfig):
    """Configuration for a command-based workflow step.

    Step type for steps that provide a raw command string.
    The command is executed as-is (wrapped with conda run by WorkflowBuilder).
    """

    name: str
    """Unique name for this step within the workflow."""
    resources: ResourceConfig
    """Resource configuration for this step."""
    command: str
    """Command string to execute for this step."""
    output_directory: Path
    """Output directory for this step. Inherited from the workflow's output_directory."""
    environment: str | None = None
    """Optional environment name to use for this step."""

    def _validate(self) -> None:
        """Validate that command is not empty."""
        if not self.command:
            raise ValueError(f"Step '{self.name}': 'command' is required.")

    def supported_arguments(self) -> set[str] | None:
        """Command-based steps don't have an 'args' section."""
        return None

    def get_tasks(
        self,
        tool: Tool,
        *,
        env: str,
        build_timestamp: str,
    ) -> list[Task]:
        """Create a single Jobmon Task for this command step."""
        task_template = tool.get_task_template(
            template_name="workflow_command_step",
            command_template="conda run --no-capture-output -n {env} {command}",
            node_args=["command"],
            task_args=[],
            op_args=["env"],
            default_cluster_name="slurm",
        )
        native_spec = self.native_specification
        compute_resources = native_spec.to_jobmon_spec(
            worker_logging_root=self.output_directory,
        )
        task = task_template.create_task(
            name=self.name,
            compute_resources=compute_resources,
            env=env,
            command=self.command,
        )
        return [task]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary, omitting None values."""
        result: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "resources": self.resources.to_dict(),
        }

        # Add environment if specified
        if self.environment is not None:
            result["environment"] = self.environment

        return result

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], output_directory: Path, *, project: str, queue: str
    ) -> CommandStepConfig:
        """Create a CommandStepConfig from a dictionary.

        Parameters
        ----------
        data
            Dictionary from workflow YAML (a step dict without 'type' field).
        output_directory
            Workflow-level output directory.
        project
            Workflow-level project for resource resolution.
        queue
            Workflow-level queue for resource resolution.

        Returns
        -------
            A new CommandStepConfig instance.
        """
        return cls(
            name=data["name"],
            resources=ResourceConfig.from_dict(
                data["resources"], workflow_project=project, workflow_queue=queue
            ),
            command=data["command"],
            output_directory=output_directory,
            environment=data.get("environment"),
        )


@dataclass
class SimulationStepConfig(BaseStepConfig):
    """Configuration for a parallel simulation workflow step.

    This step type directly creates parallel simulation tasks — one per
    (input_draw, random_seed, branch) combination — rather than launching
    ``psimulate run`` as a subprocess. The tasks use the same task runner
    infrastructure as ``psimulate run``.

    The ``resources`` field specifies compute resources for each individual
    simulation task (memory, runtime, cores).

    Examples
    --------
    Inline configuration::

        steps:
          - name: model_sims
            type: simulation
            resources:
              memory_gb: 3
              runtime: "24:00:00"
            args:
              model_specification: /path/to/model.yaml
              branch_configuration: /path/to/branches.yaml
              artifact_path: /path/to/artifact.hdf
              backup_freq: 1800
              sim_verbosity: 1
    """

    _SUPPORTED_ARGS: ClassVar[set[str]] = {
        "model_specification",
        "branch_configuration",
        "artifact_path",
        "backup_freq",
        "sim_verbosity",
    }

    name: str
    """Unique name for this step within the workflow."""
    resources: ResourceConfig
    """Compute resources for each individual simulation task."""
    output_directory: Path
    """Output directory for this step. Inherited from the workflow's output_directory."""
    model_specification: Path
    """Path to model specification YAML file."""
    branch_configuration: Path
    """Path to branch configuration YAML file."""
    environment: str | None = None
    """Optional environment name to use for this step."""
    artifact_path: Path | None = None
    """Optional path to artifact file."""
    backup_freq: float | None = DEFAULT_BACKUP_FREQ_SECONDS
    """Backup frequency in seconds, or ``None`` to disable. Default is 30 minutes."""
    sim_verbosity: int = 0
    """Vivarium simulation logging verbosity level. Default is 0."""

    def _validate(self) -> None:
        """Validate simulation step configuration."""
        if not self.model_specification:
            raise ValueError(
                f"Step '{self.name}': simulation type requires 'model_specification'."
            )
        if not self.branch_configuration:
            raise ValueError(
                f"Step '{self.name}': simulation type requires 'branch_configuration'."
            )

    def supported_arguments(self) -> set[str]:
        """Return valid keys for the 'args' section of simulation steps."""
        return self._SUPPORTED_ARGS

    def get_tasks(
        self,
        tool: Tool,
        *,
        env: str,
        build_timestamp: str,
    ) -> list[Task]:
        """Create parallel simulation Jobmon Tasks.

        Uses :class:`~vivarium_cluster_tools.psimulate.paths.OutputPaths`
        to create the same ``model_name / timestamp`` directory layout as
        ``psimulate run``.  Parses the branch configuration into a keyspace,
        builds one :class:`~vivarium_cluster_tools.psimulate.jobs.JobParameters`
        per (draw, seed, branch) combination, writes per-task metadata,
        and returns the full list of Jobmon tasks.
        """
        # Build output paths using the same layout as psimulate run:
        # output_directory / model_name / timestamp / ...
        output_paths = OutputPaths.from_entry_point_args(
            command=COMMANDS.run,
            input_artifact_path=self.artifact_path,
            result_directory=self.output_directory,
            input_model_spec_path=self.model_specification,
            launch_time=build_timestamp,
        )
        output_paths.touch()

        # Parse branch configuration into keyspace
        keyspace = branches.Keyspace.from_branch_configuration(self.branch_configuration)

        # Build job parameters for each (draw, seed, branch) combination
        job_parameters = build_job_parameters_from_keyspace(
            keyspace,
            model_specification_path=self.model_specification,
            output_root=output_paths.root,
            worker_logging_root=output_paths.worker_logging_root,
            backup_configuration=BackupConfiguration(
                backup_dir=str(output_paths.backup_dir),
                backup_freq=self.backup_freq,
                backup_metadata_path=str(output_paths.backup_metadata_path),
            ),
            extras={
                "sim_verbosity": self.sim_verbosity,
            },
        )

        return get_task_list(
            tool=tool,
            command=COMMANDS.run,
            job_parameters_list=job_parameters,
            metadata_dir=output_paths.metadata_dir,
            results_dir=output_paths.results_dir,
            worker_logging_root=output_paths.worker_logging_root,
            native_specification=self.native_specification,
            env=env,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary with type: simulation."""
        result: dict[str, Any] = {
            "name": self.name,
            "type": "simulation",
            "resources": self.resources.to_dict(),
        }
        if self.environment is not None:
            result["environment"] = self.environment

        args: dict[str, Any] = {
            "model_specification": str(self.model_specification),
            "branch_configuration": str(self.branch_configuration),
        }
        if self.artifact_path is not None:
            args["artifact_path"] = str(self.artifact_path)
        if self.backup_freq != DEFAULT_BACKUP_FREQ_SECONDS:
            args["backup_freq"] = self.backup_freq
        if self.sim_verbosity != 0:
            args["sim_verbosity"] = self.sim_verbosity

        result["args"] = args
        return result

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], output_directory: Path, *, project: str, queue: str
    ) -> SimulationStepConfig:
        """Create a SimulationStepConfig from a dictionary."""
        args = data.get("args", {}) or {}

        # Validate that only supported arguments are in args
        unsupported = set(args) - cls._SUPPORTED_ARGS
        if unsupported:
            step_name = data.get("name", "<unnamed>")
            raise ValueError(
                f"Step '{step_name}': unsupported args {sorted(unsupported)}. "
                f"Supported args: {sorted(cls._SUPPORTED_ARGS)}."
            )

        kwargs: dict[str, Any] = {
            "name": data["name"],
            "resources": ResourceConfig.from_dict(
                data["resources"], workflow_project=project, workflow_queue=queue
            ),
            "output_directory": output_directory,
            "environment": data.get("environment"),
            "model_specification": Path(args["model_specification"]),
            "branch_configuration": Path(args["branch_configuration"]),
        }
        if "artifact_path" in args:
            kwargs["artifact_path"] = Path(args["artifact_path"])
        if "backup_freq" in args:
            kwargs["backup_freq"] = args["backup_freq"]
        if "sim_verbosity" in args:
            kwargs["sim_verbosity"] = args["sim_verbosity"]

        return cls(**kwargs)


@dataclass
class WorkflowConfig:
    """Parsed and validated workflow configuration."""

    # Step type mappings - add new step types here as they are implemented
    SUPPORTED_STEP_TYPES: ClassVar[dict[str, type[BaseStepConfig]]] = {
        "simulation": SimulationStepConfig,
    }

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
    steps: list[BaseStepConfig]
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
    def _parse_steps(
        raw_steps: list[dict[str, Any]],
        output_directory: Path,
        *,
        project: str,
        queue: str,
    ) -> list[BaseStepConfig]:
        """Parse a list of raw step dicts into step config objects.

        Routes to the appropriate step type based on the 'type' field using
        WorkflowConfig.SUPPORTED_STEP_TYPES. Falls back to CommandStepConfig
        if no type is specified. Raises ValueError for unrecognized types.
        """
        steps: list[BaseStepConfig] = []
        for step_dict in raw_steps:
            if "command" in step_dict and "type" in step_dict:
                step_name = step_dict.get("name", "<unnamed>")
                raise ValueError(
                    f"Step '{step_name}': Cannot specify both 'command' and 'type'. "
                    "Use 'command' for command-based steps or 'type' for typed steps."
                )
            step_type = step_dict.get("type")
            if step_type is not None:
                if step_type not in WorkflowConfig.SUPPORTED_STEP_TYPES:
                    step_name = step_dict.get("name", "<unnamed>")
                    raise ValueError(
                        f"Step '{step_name}': unsupported type '{step_type}'. "
                        f"Must be one of: {sorted(WorkflowConfig.SUPPORTED_STEP_TYPES)}."
                    )
                step_class = WorkflowConfig.SUPPORTED_STEP_TYPES[step_type]
                step = step_class.from_dict(
                    step_dict,
                    output_directory=output_directory,
                    project=project,
                    queue=queue,
                )
            else:
                # Default command-based step
                step = CommandStepConfig.from_dict(
                    step_dict,
                    output_directory=output_directory,
                    project=project,
                    queue=queue,
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

        resolved_project = project or workflow.get("project")
        resolved_queue = queue or workflow.get("queue")
        resolved_output_directory = output_directory or (
            Path(workflow["output_directory"]) if "output_directory" in workflow else None
        )

        if not resolved_project:
            raise ValueError(
                "Project is required. Provide it in the config file or via --project/-P."
            )
        if not resolved_queue:
            raise ValueError(
                "Queue is required. Provide it in the config file or via --queue/-q."
            )
        if not resolved_output_directory:
            raise ValueError(
                "Output directory is required. Provide it in the config file "
                "or via --output-directory/-o."
            )

        steps = cls._parse_steps(
            workflow["steps"],
            output_directory=resolved_output_directory,
            project=resolved_project,
            queue=resolved_queue,
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
        validate_project(self.project)
        # Uses a placeholder value for runtime
        validate_runtime_and_queue("01:00:00", self.queue)  # validate queue value
        # Unique step names
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(
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
