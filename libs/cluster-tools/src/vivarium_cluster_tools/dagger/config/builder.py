"""
================
Workflow Builder
================

Build Jobmon workflows from workflow configuration.

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from vivarium_cluster_tools.core.jobmon import client
from vivarium_cluster_tools.dagger.config.config import WorkflowConfig
from vivarium_cluster_tools.dagger.config.interface import (
    get_bash_step_tasks,
    get_notebook_step_tasks,
    get_pytest_step_tasks,
    get_python_step_tasks,
    get_simulation_step_tasks,
)
from vivarium_cluster_tools.dagger.config.utilities import is_resume

if TYPE_CHECKING:
    from jobmon.client.task import Task
    from jobmon.client.workflow import Workflow


STEP_TYPE_API_FNS: dict[str, Callable[..., list["Task"]]] = {
    "bash": get_bash_step_tasks,
    "simulation": get_simulation_step_tasks,
    "pytest": get_pytest_step_tasks,
    "python": get_python_step_tasks,
    "notebook": get_notebook_step_tasks,
}
"""Maps each YAML ``step_type`` to the API function that builds its tasks.
Paired with the dispatch tables in
:mod:`vivarium_cluster_tools.dagger.config.parsing`."""


def build_workflow_from_config(config: WorkflowConfig, workflow_args: str) -> Workflow:
    """Build a complete Jobmon workflow from a workflow configuration.

    For each step in the workflow, dispatches to the matching interface API
    function (one of ``get_*_step_tasks``) and wires dependencies so that
    steps execute in sequential order (all tasks from step *N* must complete
    before any task in step *N+1* starts).

    Parameters
    ----------
    config
        The validated workflow configuration to build.
    workflow_args
        Deterministic string that Jobmon uses to identify the workflow.
        Must be identical across runs for resume to work.
    """
    tool = client.make_tool()
    workflow = client.make_workflow(
        tool,
        workflow_args=workflow_args,
        name=config.name,
        max_attempts=config.max_attempts,
    )
    resuming = is_resume(config.output_directory)
    previous_step_tasks: list[Task] = []
    all_tasks: list[Task] = []

    for parsed_step in config.steps:
        api_fn = STEP_TYPE_API_FNS[parsed_step.step_type]
        # Step-level environment wins; otherwise the workflow default is
        # substituted. Build a new dict so the cached api_kwargs is not mutated.
        kwargs = parsed_step.api_kwargs
        if kwargs.get("environment") is None:
            kwargs = {**kwargs, "environment": config.default_environment}
        step_tasks = api_fn(**kwargs, tool=tool, is_resume=resuming)

        # Wire sequential dependencies: every task in this step
        # depends on every task from the previous step.
        for task in step_tasks:
            for prev_task in previous_step_tasks:
                client.add_upstream(task, prev_task)

        all_tasks.extend(step_tasks)
        previous_step_tasks = step_tasks

    client.add_tasks(workflow, all_tasks)

    return workflow
