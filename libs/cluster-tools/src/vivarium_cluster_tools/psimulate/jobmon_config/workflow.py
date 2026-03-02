"""
========================
Jobmon Workflow Builder
========================

Build and configure Jobmon workflows for psimulate runs.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jobmon.client.api import Tool
from loguru import logger

from vivarium_cluster_tools.psimulate.cluster.interface import NativeSpecification
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.paths import OutputPaths
from vivarium_cluster_tools.psimulate.results.writing import write_metadata

if TYPE_CHECKING:
    from jobmon.client.workflow import Workflow


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

    Returns
    -------
        A ready-to-run Jobmon Workflow object.
    """
    tool = Tool(name="vivarium_cluster_tools_psimulate")
    task_template = tool.get_task_template(
        template_name="psimulate_task",
        command_template=(
            # task runner to come as workhorse wrapper
            f"python -m vivarium_cluster_tools.psimulate.worker.task_runner "
            "--metadata-dir {metadata_dir} "
            "--task-id {task_id} "
            "--results-dir {results_dir} "
            "--worker-log-dir {worker_log_dir} "
            "--command {command}"
        ),
        node_args=["task_id"],
        task_args=["metadata_dir", "results_dir", "worker_log_dir", "command"],
        op_args=[],
        default_cluster_name="slurm",
        default_compute_resources=native_specification.to_jobmon_spec(
            output_paths.cluster_logging_root
        ),
    )

    workflow = tool.create_workflow(
        workflow_args=workflow_name,
        name=workflow_name,
        max_concurrently_running=max_workers,
        default_cluster_name="slurm",
        default_max_attempts=max_attempts,
    )

    # Write job spec metadata and create tasks
    tasks = []
    for job_params in job_parameters_list:
        write_metadata(
            metadata_dir=output_paths.metadata_dir,
            job_parameters=job_params,
        )

        task = task_template.create_task(
            name=f"psim_{job_params.task_id[:12]}",
            task_id=job_params.task_id,
            metadata_dir=str(output_paths.metadata_dir),
            results_dir=str(output_paths.results_dir),
            worker_log_dir=str(output_paths.worker_logging_root),
            command=command,
        )
        tasks.append(task)

    workflow.add_tasks(tasks)

    logger.info(
        f"Built Jobmon workflow '{workflow_name}' with {len(tasks)} tasks "
        f"(max {max_workers} concurrent)."
    )
    return workflow
