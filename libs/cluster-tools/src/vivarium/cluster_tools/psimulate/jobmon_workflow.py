"""
=========================
psimulate Jobmon Workflow
=========================

Build the Jobmon workflow for a psimulate ``run`` / ``restart`` / ``expand``
/ ``load_test`` command: construct one Jobmon task per ``JobParameters``,
register the task template, and wire them into a single workflow.

"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
from vivarium.cluster_tools.core.jobmon import client
from vivarium.cluster_tools.core.jobmon.env import resolve_env_bin_path
from vivarium.cluster_tools.psimulate import TASK_RUNNER_MODULE
from vivarium.cluster_tools.psimulate.jobs import JobParameters
from vivarium.cluster_tools.psimulate.paths import OutputPaths
from vivarium.cluster_tools.psimulate.results.writing import write_metadata

if TYPE_CHECKING:
    from jobmon.client.api import Tool
    from jobmon.client.task import Task
    from jobmon.client.workflow import Workflow


def get_task_list(
    tool: Tool,
    command: str,
    job_parameters_list: list[JobParameters],
    metadata_dir: Path,
    results_dir: Path,
    worker_logging_root: Path,
    native_specification: NativeSpecification,
    max_attempts: int = 3,
    env_prefix: str | None = None,
    template_name: str = "psimulate",
) -> list[Task]:
    """Create Jobmon tasks for a list of job parameters.

    Writes per-task metadata JSON files and batch-creates one Jobmon Task
    per job. This is the reusable core extracted from :func:`build_workflow`
    so that workflow step configs can also produce simulation task lists.

    Parameters
    ----------
    tool
        The Jobmon Tool instance to create task templates from.
    command
        The psimulate command (run, restart, expand, load_test).
    job_parameters_list
        List of job parameters (one per task).
    metadata_dir
        Directory to write per-task metadata JSON files.
    results_dir
        Directory where simulation results are written.
    worker_logging_root
        Directory for worker log output.
    native_specification
        SLURM resource specification for the simulation tasks.
    max_attempts
        Maximum number of attempts Jobmon will make for each task.
    env_prefix
        Optional absolute path to the conda env's or venv's prefix. When
        provided, the env's bin directories are prepended to ``PATH`` (for
        a venv, the base environment's ``bin`` as well) so the env's
        ``python`` is found without depending on ``conda`` being available
        on the worker.
    template_name
        Name to register the Jobmon ``TaskTemplate`` under. Must be unique
        per Tool/Workflow; callers that build multiple simulation step
        groups in a single workflow must pass a distinct value per group.

    Returns
    -------
        List of Jobmon Task objects, one per job.
    """
    worker_command = (
        f"python -m {TASK_RUNNER_MODULE} "
        "--metadata-dir {metadata_dir} "
        "--task-id {task_id} "
        "--results-dir {results_dir} "
        "--command {command}"
    )
    if env_prefix is not None:
        worker_command = f"PATH={resolve_env_bin_path(env_prefix)}:$PATH {worker_command}"

    task_template = client.make_task_template(
        tool,
        template_name=template_name,
        command_template=worker_command,
        node_args=["task_id"],
        task_args=["metadata_dir", "results_dir"],
        op_args=["command"],
        default_compute_resources=native_specification.to_jobmon_spec(worker_logging_root),
    )

    # Write job spec metadata (one JSON per task for the worker to pick up)
    for job_params in job_parameters_list:
        write_metadata(
            metadata_dir=metadata_dir,
            job_parameters=job_params,
        )

    # Batch-create all tasks
    tasks = client.create_tasks(
        task_template,
        max_attempts=max_attempts,
        task_id=[jp.task_id for jp in job_parameters_list],
        metadata_dir=str(metadata_dir),
        results_dir=str(results_dir),
        command=command,
        resource_scales=_simulation_resource_scales(job_parameters_list),
    )

    return tasks


def _simulation_resource_scales(
    job_parameters_list: list[JobParameters],
) -> dict[str, float] | None:
    """Per-task retry scaling for simulation tasks.

    Jobmon's scale formula is ``ceil(orig * (1 + scale))``. Supplying any
    ``resource_scales`` dict suppresses Jobmon's full default, so memory
    is restated at ``0.5`` to preserve its default +50% retry growth;
    runtime is set to ``-0.5`` to shrink the retry's request to half
    because the worker resumes from backup.
    """
    if job_parameters_list[0].backup_configuration["backup_freq"] is None:
        return None
    return {"memory": 0.5, "runtime": -0.5}


def build_workflow(
    workflow_name: str,
    command: str,
    job_parameters_list: list[JobParameters],
    output_paths: OutputPaths,
    native_specification: NativeSpecification,
    max_workers: int,
    max_attempts: int = 3,
) -> Workflow:
    """Build a Jobmon workflow for a psimulate command.

    Creates a Jobmon Tool, TaskTemplate, and one Task per job. Also writes
    metadata JSON files to ``output_paths.metadata_dir``.

    Parameters
    ----------
    workflow_name
        A unique name for the workflow.
    command
        The psimulate command (run, restart, expand, load_test).
    job_parameters_list
        List of job parameters (one per task).
    output_paths
        The output paths container.
    native_specification
        SLURM resource specification.
    max_workers
        Maximum number of concurrent tasks.
    max_attempts
        Maximum number of attempts Jobmon will make for each task.

    Returns
    -------
        A ready-to-run Jobmon Workflow object.
    """
    tool = client.make_tool()

    tasks = get_task_list(
        tool=tool,
        command=command,
        job_parameters_list=job_parameters_list,
        metadata_dir=output_paths.metadata_dir,
        results_dir=output_paths.results_dir,
        worker_logging_root=output_paths.worker_logging_root,
        native_specification=native_specification,
        max_attempts=max_attempts,
    )

    workflow = client.make_workflow(
        tool,
        workflow_args=workflow_name,
        name=workflow_name,
        max_attempts=max_attempts,
        max_concurrently_running=max_workers,
    )

    client.add_tasks(workflow, tasks)

    logger.debug(
        f"Built Jobmon workflow '{workflow_name}' with {len(tasks)} tasks "
        f"(max {max_workers} concurrent)."
    )
    return workflow
