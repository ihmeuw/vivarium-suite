"""
========================
Jobmon Workflow Builder
========================

Build and configure Jobmon workflows for psimulate runs.

"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jobmon.client.api import Tool
from loguru import logger

from vivarium_cluster_tools.psimulate.cluster.interface import NativeSpecification
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.results.writing import write_metadata

if TYPE_CHECKING:
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
    env: str | None = None,
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
    env
        Optional conda environment name. When provided, the worker command
        is wrapped with ``conda run --no-capture-output -n <env>``.

    Returns
    -------
        List of Jobmon Task objects, one per job.
    """
    worker_command = (
        "python -m vivarium_cluster_tools.psimulate.worker.task_runner "
        "--metadata-dir {metadata_dir} "
        "--task-id {task_id} "
        "--results-dir {results_dir} "
        "--command {command}"
    )
    if env is not None:
        worker_command = f"conda run --no-capture-output -n {env} {worker_command}"

    task_template = tool.get_task_template(
        template_name="psimulate",
        command_template=worker_command,
        node_args=["task_id"],
        task_args=["metadata_dir", "results_dir"],
        op_args=["command"],
        default_cluster_name="slurm",
        default_compute_resources=native_specification.to_jobmon_spec(worker_logging_root),
    )

    # Write job spec metadata (one JSON per task for the worker to pick up)
    for job_params in job_parameters_list:
        write_metadata(
            metadata_dir=metadata_dir,
            job_parameters=job_params,
        )

    # Batch-create all tasks
    tasks = task_template.create_tasks(
        max_attempts=max_attempts,
        task_id=[jp.task_id for jp in job_parameters_list],
        metadata_dir=str(metadata_dir),
        results_dir=str(results_dir),
        command=command,
    )

    return tasks


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
    tool = Tool(name="vivarium_cluster_tools")

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

    workflow = tool.create_workflow(
        workflow_args=workflow_name,
        name=workflow_name,
        max_concurrently_running=max_workers,
        default_cluster_name="slurm",
        default_max_attempts=max_attempts,
    )

    workflow.add_tasks(tasks)

    logger.debug(
        f"Built Jobmon workflow '{workflow_name}' with {len(tasks)} tasks "
        f"(max {max_workers} concurrent)."
    )
    return workflow
