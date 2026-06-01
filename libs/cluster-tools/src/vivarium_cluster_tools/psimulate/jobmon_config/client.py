"""
=============
Jobmon Client
=============

Single point of contact for the Jobmon SDK. All Jobmon imports and SDK
calls live in this module so that future changes to the Jobmon API surface
only require updates here.

Callers should import :class:`Task` and :class:`Workflow` types from this
module rather than from ``jobmon.*`` directly, and use the helper functions
below instead of calling SDK methods themselves.

Project-wide defaults (tool name, cluster name) are encoded here so they
cannot drift between call sites.
"""

from __future__ import annotations

from typing import Any

from jobmon.client.api import Tool
from jobmon.client.task import Task
from jobmon.client.task_template import TaskTemplate
from jobmon.client.workflow import Workflow
from jobmon.core.configuration import JobmonConfig

__all__ = [
    "JOBMON_STATUS_DONE",
    "Task",
    "TaskTemplate",
    "Tool",
    "Workflow",
    "add_tasks",
    "add_upstream",
    "bind_workflow",
    "count_completed_tasks",
    "create_task",
    "create_tasks",
    "get_monitoring_url",
    "make_task_template",
    "make_tool",
    "make_workflow",
    "run_workflow",
]


TOOL_NAME = "vivarium_cluster_tools"
CLUSTER_NAME = "slurm"
JOBMON_STATUS_DONE = "D"
"""Jobmon's workflow / task ``final_status`` value for a successful completion."""


def make_tool() -> Tool:
    """Instantiate the project's Jobmon ``Tool``."""
    return Tool(name=TOOL_NAME)


def make_task_template(
    tool: Tool,
    *,
    template_name: str,
    command_template: str,
    node_args: list[str],
    task_args: list[str],
    op_args: list[str],
    default_compute_resources: dict[str, Any] | None = None,
) -> TaskTemplate:
    """Register a Jobmon ``TaskTemplate`` on *tool*."""
    kwargs: dict[str, Any] = {
        "template_name": template_name,
        "command_template": command_template,
        "node_args": node_args,
        "task_args": task_args,
        "op_args": op_args,
        "default_cluster_name": CLUSTER_NAME,
    }
    if default_compute_resources is not None:
        kwargs["default_compute_resources"] = default_compute_resources
    return tool.get_task_template(**kwargs)


def create_task(
    template: TaskTemplate,
    *,
    name: str,
    compute_resources: dict[str, Any],
    env_prefix: str,
    command: str,
) -> Task:
    """Create a single Jobmon ``Task`` for the workflow-command-step template.

    The ``env_prefix`` and ``command`` kwargs match the template's
    ``node_args``; see :func:`make_task_template` and the call site in
    :func:`~vivarium_cluster_tools.psimulate.workflow_config.utilities.get_single_command_task`.
    """
    return template.create_task(
        name=name,
        compute_resources=compute_resources,
        env_prefix=env_prefix,
        command=command,
    )


def create_tasks(
    template: TaskTemplate,
    *,
    max_attempts: int,
    task_id: list[str],
    metadata_dir: str,
    results_dir: str,
    command: str,
    resource_scales: dict[str, float] | None = None,
) -> list[Task]:
    """Batch-create Jobmon ``Task``\\s for the simulation-step template.

    Parameters
    ----------
    template
        The Jobmon ``TaskTemplate`` returned by :func:`make_task_template`.
    max_attempts
        Maximum number of times Jobmon will attempt each task before giving up.
    task_id
        The template's ``node_arg`` — one value per task. The length of
        this list determines how many tasks are created.
    metadata_dir
        The template's ``task_arg`` for the metadata directory (shared
        across the batch).
    results_dir
        The template's ``task_arg`` for the results directory (shared
        across the batch).
    command
        The template's ``op_arg`` for the psimulate subcommand
        (``run`` / ``restart`` / ``expand`` / ``load_test``).
    resource_scales
        Optional per-resource retry scaling forwarded to Jobmon. When
        ``None`` (the default), the kwarg is omitted from the
        ``template.create_tasks`` call so Jobmon falls back to its
        built-in default of ``{"memory": 0.5, "runtime": 0.5}`` (+50%
        on each retry).

    Returns
    -------
        A list of Jobmon ``Task``\\s for the batch, one per ``task_id``.
    """
    kwargs: dict[str, Any] = {
        "max_attempts": max_attempts,
        "task_id": task_id,
        "metadata_dir": metadata_dir,
        "results_dir": results_dir,
        "command": command,
    }
    if resource_scales is not None:
        kwargs["resource_scales"] = resource_scales
    return template.create_tasks(**kwargs)


def add_upstream(task: Task, upstream: Task) -> None:
    """Wire a step-to-step dependency between two tasks."""
    task.add_upstream(upstream)


def make_workflow(
    tool: Tool,
    *,
    workflow_args: str,
    name: str,
    max_attempts: int,
    max_concurrently_running: int | None = None,
) -> Workflow:
    """Create a Jobmon ``Workflow`` on *tool*."""
    kwargs: dict[str, Any] = {
        "workflow_args": workflow_args,
        "name": name,
        "default_cluster_name": CLUSTER_NAME,
        "default_max_attempts": max_attempts,
    }
    if max_concurrently_running is not None:
        kwargs["max_concurrently_running"] = max_concurrently_running
    return tool.create_workflow(**kwargs)


def add_tasks(workflow: Workflow, tasks: list[Task]) -> None:
    """Add *tasks* to *workflow*."""
    workflow.add_tasks(tasks)


def bind_workflow(workflow: Workflow) -> None:
    """Bind *workflow* to the Jobmon server.

    After binding, ``workflow.workflow_id`` is populated and the workflow
    can be submitted via :func:`run_workflow`.
    """
    workflow.bind()


def get_monitoring_url(workflow: Workflow) -> str | None:
    """Return the Jobmon GUI URL for *workflow*, or ``None`` if unconfigured.

    Requires *workflow* to have been bound (so ``workflow_id`` is set).
    """
    gui_url = JobmonConfig().get("http", "gui_url")
    if not gui_url:
        return None
    return f"{gui_url}/#/workflow/{workflow.workflow_id}"


def run_workflow(
    workflow: Workflow,
    *,
    resume: bool,
    seconds_until_timeout: int | None = None,
) -> str:
    """Submit *workflow* and return its Jobmon status string."""
    run_kwargs: dict[str, Any] = {"resume": resume}
    if seconds_until_timeout is not None:
        run_kwargs["seconds_until_timeout"] = seconds_until_timeout
    status = workflow.run(**run_kwargs)
    if status is None:
        raise RuntimeError("Jobmon workflow.run() returned None unexpectedly.")
    return status


def count_completed_tasks(workflow: Workflow) -> int:
    """Count tasks in *workflow* whose ``final_status`` is :data:`JOBMON_STATUS_DONE`."""
    return sum(1 for t in workflow.tasks.values() if t.final_status == JOBMON_STATUS_DONE)
