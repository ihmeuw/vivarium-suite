"""
=========================
Workflow Config Interface
=========================

Python API for building workflow step tasks programmatically, as an
alternative to authoring a YAML workflow file. Each function validates its
kwargs and constructs the Jobmon tasks for that step type.

"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vivarium_cluster_tools.psimulate import COMMANDS, branches
from vivarium_cluster_tools.psimulate.jobmon_config.workflow import get_task_list
from vivarium_cluster_tools.psimulate.jobs import (
    BackupConfiguration,
    build_job_parameters_from_keyspace,
)
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.workflow_config.config import (
    DEFAULT_BACKUP_FREQ_SECONDS,
    ResourceConfig,
)
from vivarium_cluster_tools.psimulate.workflow_config.utilities import (
    ensure_output_directory_exists,
    get_or_create_build_timestamp,
    get_single_command_task,
    resolve_step_env_prefix,
)
from vivarium_cluster_tools.psimulate.workflow_config.validation import (
    validate_bash_step,
    validate_notebook_step,
    validate_pytest_step,
    validate_python_step,
    validate_simulation_step,
)

if TYPE_CHECKING:
    from jobmon.client.api import Tool
    from jobmon.client.task import Task

_NOTEBOOK_DEFAULT_KERNEL = "python3"
"""Jupyter kernel used for notebook execution. Not user-configurable."""


def get_bash_step_tasks(
    *,
    name: str,
    resources: ResourceConfig,
    command: str,
    output_directory: Path,
    tool: Tool,
    environment: str | None = None,
    is_resume: bool = False,
) -> list[Task]:
    """Build a bash workflow step and return its Jobmon tasks.

    Parameters
    ----------
    name
        Unique name for this step within the workflow.
    resources
        Compute resources for this step.
    command
        Shell command string to execute.
    output_directory
        Directory for this step's worker logs and step-level outputs.
    tool
        Jobmon Tool used to register task templates and create tasks.
    environment
        Optional conda environment name to use for this step. If unset,
        falls back to the runner's active ``CONDA_DEFAULT_ENV``.
    is_resume
        Whether this is a resumed workflow build.

    Returns
    -------
        The Jobmon tasks produced by the step.
    """
    validate_bash_step(
        name=name,
        resources=resources,
        command=command,
        environment=environment,
    )
    ensure_output_directory_exists(output_directory)
    return get_single_command_task(
        tool,
        name=name,
        resources=resources,
        output_directory=output_directory,
        env_prefix=resolve_step_env_prefix(name=name, environment=environment),
        command=command,
    )


def get_simulation_step_tasks(
    *,
    name: str,
    resources: ResourceConfig,
    output_directory: Path,
    model_specification: Path,
    branch_configuration: Path,
    tool: Tool,
    environment: str | None = None,
    artifact_path: Path | None = None,
    backup_freq: float | None = DEFAULT_BACKUP_FREQ_SECONDS,
    sim_verbosity: int = 0,
    is_resume: bool = False,
) -> list[Task]:
    """Build a parallel-simulation workflow step and return its Jobmon tasks.

    Produces one Jobmon task per ``(input_draw, random_seed, branch)``
    combination defined by the branch configuration. Uses the same task
    runner infrastructure as ``psimulate run``.

    Parameters
    ----------
    name
        Unique name for this step within the workflow.
    resources
        Compute resources for *each individual simulation task*
        (memory, runtime, cores).
    output_directory
        Directory for this step's outputs. The simulation step lays out
        ``model_name / timestamp`` subdirectories beneath this.
    model_specification
        Path to the model specification YAML file. Both relative and
        absolute paths are accepted.
    branch_configuration
        Path to the branch configuration YAML file. Both relative and
        absolute paths are accepted.
    tool
        Jobmon Tool used to register task templates and create tasks.
    environment
        Optional conda environment name to use for this step. If unset,
        falls back to the runner's active ``CONDA_DEFAULT_ENV``.
    artifact_path
        Optional path to a data artifact file. Both relative and absolute
        paths are accepted.
    backup_freq
        Backup frequency in seconds, or ``None`` to disable backups.
        Defaults to 30 minutes.
    sim_verbosity
        Vivarium simulation logging verbosity level. Default is 0.
    is_resume
        Whether this is a resumed workflow build.

    Returns
    -------
        The Jobmon tasks produced by the step.
    """
    validate_simulation_step(
        name=name,
        resources=resources,
        model_specification=model_specification,
        branch_configuration=branch_configuration,
        environment=environment,
        artifact_path=artifact_path,
        backup_freq=backup_freq,
        sim_verbosity=sim_verbosity,
    )
    ensure_output_directory_exists(output_directory)
    output_paths = OutputPaths.from_entry_point_args(
        command=COMMANDS.run,
        input_artifact_path=artifact_path,
        result_directory=output_directory,
        input_model_spec_path=model_specification,
        launch_time=get_or_create_build_timestamp(output_directory),
        is_resume=is_resume,
    )
    output_paths.touch()

    keyspace = branches.Keyspace.from_branch_configuration(branch_configuration)
    job_parameters = build_job_parameters_from_keyspace(
        keyspace,
        model_specification_path=model_specification,
        output_root=output_paths.root,
        worker_logging_root=output_paths.worker_logging_root,
        backup_configuration=BackupConfiguration(
            backup_dir=str(output_paths.backup_dir),
            backup_freq=backup_freq,
            backup_metadata_path=str(output_paths.backup_metadata_path),
        ),
        extras={
            "sim_verbosity": sim_verbosity,
        },
    )

    return get_task_list(
        tool=tool,
        command=COMMANDS.run,
        job_parameters_list=job_parameters,
        metadata_dir=output_paths.metadata_dir,
        results_dir=output_paths.results_dir,
        worker_logging_root=output_paths.worker_logging_root,
        native_specification=resources.to_native_specification(name),
        env_prefix=resolve_step_env_prefix(name=name, environment=environment),
        template_name=f"psimulate_{name}",
    )


def get_pytest_step_tasks(
    *,
    name: str,
    resources: ResourceConfig,
    output_directory: Path,
    tool: Tool,
    environment: str | None = None,
    path: str | list[str] | None = None,
    k: str | None = None,
    runslow: bool = False,
    is_resume: bool = False,
) -> list[Task]:
    """Build a pytest-based workflow step and return its Jobmon tasks.

    At least one of ``path`` or ``k`` must be provided. When the step's
    ``resources.cores`` is greater than 1, the command is run with
    ``--numprocesses <cores>`` (pytest-xdist).

    Parameters
    ----------
    name
        Unique name for this step within the workflow.
    resources
        Compute resources for this step.
    output_directory
        Directory for this step's worker logs and step-level outputs.
    tool
        Jobmon Tool used to register task templates and create tasks.
    environment
        Optional conda environment name to use for this step. If unset,
        falls back to the runner's active ``CONDA_DEFAULT_ENV``.
    path
        Test path(s) — a single file/directory or a list of them — passed
        to pytest as positional arguments. Both relative and absolute
        paths are accepted.
    k
        Pytest ``-k`` expression used to filter tests by name.
    runslow
        If ``True``, pass ``--runslow`` to pytest. Default is ``False``.
    is_resume
        Whether this is a resumed workflow build.

    Returns
    -------
        The Jobmon tasks produced by the step.
    """
    validate_pytest_step(
        name=name,
        resources=resources,
        environment=environment,
        path=path,
        k=k,
        runslow=runslow,
    )
    ensure_output_directory_exists(output_directory)
    parts = ["pytest"]
    if path:
        if isinstance(path, list):
            parts.extend(shlex.quote(p) for p in path)
        else:
            parts.append(shlex.quote(path))
    if k:
        parts.append(f"-k {shlex.quote(k)}")
    if runslow:
        parts.append("--runslow")
    if resources.cores > 1:
        parts.append(f"--numprocesses {resources.cores}")
    return get_single_command_task(
        tool,
        name=name,
        resources=resources,
        output_directory=output_directory,
        env_prefix=resolve_step_env_prefix(name=name, environment=environment),
        command=" ".join(parts),
    )


def get_python_step_tasks(
    *,
    name: str,
    resources: ResourceConfig,
    output_directory: Path,
    path: str,
    tool: Tool,
    environment: str | None = None,
    positional_args: list[Any] | None = None,
    keyword_args: dict[str, Any] | None = None,
    is_resume: bool = False,
) -> list[Task]:
    """Build a Python-script workflow step and return its Jobmon tasks.

    Constructs a ``python <path> [positional_args...] [--key value...]``
    command. Positional arguments are appended in list order; keyword
    arguments are emitted sorted by key. Keyword values map to CLI flags
    as follows:

    - ``True`` or ``None`` -> bare ``--key`` flag
    - ``False`` -> omitted from the command
    - any other scalar -> ``--key value``

    Parameters
    ----------
    name
        Unique name for this step within the workflow.
    resources
        Compute resources for this step.
    output_directory
        Directory for this step's worker logs and step-level outputs.
    path
        Path to the Python script (must end with ``.py``). Both relative
        and absolute paths are accepted.
    tool
        Jobmon Tool used to register task templates and create tasks.
    environment
        Optional conda environment name to use for this step. If unset,
        falls back to the runner's active ``CONDA_DEFAULT_ENV``.
    positional_args
        Optional list of scalar values appended in order as positional
        CLI arguments.
    keyword_args
        Optional dict mapping identifier-style keys to scalar values,
        rendered as ``--key value`` flags (see flag rules above).
    is_resume
        Whether this is a resumed workflow build.

    Returns
    -------
        The Jobmon tasks produced by the step.
    """
    validate_python_step(
        name=name,
        resources=resources,
        path=path,
        environment=environment,
        positional_args=positional_args,
        keyword_args=keyword_args,
    )
    ensure_output_directory_exists(output_directory)
    parts = ["python", shlex.quote(path)]
    for value in positional_args or []:
        parts.append(shlex.quote(str(value)))
    for key in sorted(keyword_args or {}):
        value = (keyword_args or {})[key]
        if value is True or value is None:
            parts.append(f"--{key}")
        elif value is False:
            continue
        else:
            parts.append(f"--{key} {shlex.quote(str(value))}")
    return get_single_command_task(
        tool,
        name=name,
        resources=resources,
        output_directory=output_directory,
        env_prefix=resolve_step_env_prefix(name=name, environment=environment),
        command=" ".join(parts),
    )


def get_notebook_step_tasks(
    *,
    name: str,
    resources: ResourceConfig,
    output_directory: Path,
    path: Path,
    output_path: Path,
    tool: Tool,
    environment: str | None = None,
    parameters: dict[str, Any] | None = None,
    cwd: Path | None = None,
    is_resume: bool = False,
) -> list[Task]:
    """Build a notebook-based workflow step and return its Jobmon tasks.

    Parameter values map to papermill flags as follows:

    - ``str`` / ``int`` / ``float`` -> ``-p key value``
    - ``bool`` / ``None`` -> ``-y key {true,false,null}`` (YAML-typed)

    Parameter keys must be valid Python identifiers because papermill
    injects them as variable assignments in a notebook cell.

    Parameters
    ----------
    name
        Unique name for this step within the workflow.
    resources
        Compute resources for this step.
    output_directory
        Directory for this step's worker logs and step-level outputs.
    path
        Path to the input notebook (must end with ``.ipynb``). Both
        relative and absolute paths are accepted.
    output_path
        Path where the executed notebook will be written (must end with
        ``.ipynb``). Both relative and absolute paths are accepted.
    tool
        Jobmon Tool used to register task templates and create tasks.
    environment
        Optional conda environment name to use for this step. If unset,
        falls back to the runner's active ``CONDA_DEFAULT_ENV``.
    parameters
        Optional dict of scalar values injected as notebook parameters.
    cwd
        Optional working directory for notebook execution. If not
        provided, defaults to the parent directory of ``path``.
    is_resume
        Whether this is a resumed workflow build.

    Returns
    -------
        The Jobmon tasks produced by the step.
    """
    validate_notebook_step(
        name=name,
        resources=resources,
        path=path,
        output_path=output_path,
        environment=environment,
        parameters=parameters,
        cwd=cwd,
    )
    ensure_output_directory_exists(output_directory)
    effective_cwd = cwd if cwd is not None else path.parent
    params = parameters or {}
    parts = [
        f"mkdir -p {shlex.quote(str(output_path.parent))}",
        "&&",
        "papermill",
        shlex.quote(str(path)),
        shlex.quote(str(output_path)),
        f"-k {_NOTEBOOK_DEFAULT_KERNEL}",
    ]
    for key in sorted(params):
        value = params[key]
        if isinstance(value, bool) or value is None:
            yaml_value = "true" if value is True else "false" if value is False else "null"
            parts.append(f"-y {shlex.quote(f'{key}: {yaml_value}')}")
        else:
            parts.append(f"-p {key} {shlex.quote(str(value))}")
    parts.append(f"--cwd {shlex.quote(str(effective_cwd))}")
    return get_single_command_task(
        tool,
        name=name,
        resources=resources,
        output_directory=output_directory,
        env_prefix=resolve_step_env_prefix(name=name, environment=environment),
        command=" ".join(parts),
    )
