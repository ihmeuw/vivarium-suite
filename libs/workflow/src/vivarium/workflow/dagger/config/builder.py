"""
================
Workflow Builder
================

Build Jobmon workflows from workflow configuration.

"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from vivarium.cluster_tools.core.jobmon import client

from vivarium.workflow.dagger.config.config import WorkflowConfig
from vivarium.workflow.dagger.config.interface import (
    get_bash_step_tasks,
    get_notebook_step_tasks,
    get_pytest_step_tasks,
    get_python_step_tasks,
    get_simulation_step_run_root,
    get_simulation_step_tasks,
)
from vivarium.workflow.dagger.config.utilities import get_or_create_build_timestamp

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
:mod:`vivarium.workflow.dagger.config.parsing`."""


def build_workflow_from_config(
    config: WorkflowConfig, workflow_args: str, *, resume: bool = False
) -> Workflow:
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
    resume
        Whether this build is a resume (``dagger restart``). Forwarded only to
        the simulation step, the one step type whose output layout varies on
        resume.

    Raises
    ------
    ValueError
        If two simulation steps would write their runs to the same directory.
    """
    _check_simulation_run_roots(config)
    tool = client.make_tool()
    workflow = client.make_workflow(
        tool,
        workflow_args=workflow_args,
        name=config.name,
        max_attempts=config.max_attempts,
    )
    previous_step_tasks: list[Task] = []
    all_tasks: list[Task] = []

    for parsed_step in config.steps:
        api_fn = STEP_TYPE_API_FNS[parsed_step.step_type]
        # Step-level environment wins; otherwise the workflow default is
        # substituted. Build a new dict so the cached api_kwargs is not mutated.
        kwargs = parsed_step.api_kwargs
        if kwargs.get("environment") is None:
            kwargs = {**kwargs, "environment": config.default_environment}
        # The simulation step is the only one whose tasks carry their own
        # max_attempts, and the only one that varies its behavior on resume.
        if parsed_step.step_type == "simulation":
            step_tasks = api_fn(
                **kwargs, tool=tool, is_resume=resume, max_attempts=config.max_attempts
            )
        else:
            step_tasks = api_fn(**kwargs, tool=tool)

        # Wire sequential dependencies: every task in this step
        # depends on every task from the previous step.
        for task in step_tasks:
            for prev_task in previous_step_tasks:
                client.add_upstream(task, prev_task)

        all_tasks.extend(step_tasks)
        previous_step_tasks = step_tasks

    client.add_tasks(workflow, all_tasks)

    return workflow


def _check_simulation_run_roots(config: WorkflowConfig) -> None:
    """Refuse two simulation steps that would write their runs to the same directory."""
    simulation_steps = [step for step in config.steps if step.step_type == "simulation"]
    if len(simulation_steps) < 2:
        return
    step_names_by_run_root: dict[Path, str] = {}
    for step in simulation_steps:
        kwargs = step.api_kwargs
        run_root = get_simulation_step_run_root(
            name=step.name,
            output_directory=kwargs["output_directory"],
            model_specification=kwargs["model_specification"],
            build_timestamp=get_or_create_build_timestamp(kwargs["output_directory"]),
            artifact_path=kwargs.get("artifact_path"),
        )
        if run_root in step_names_by_run_root:
            raise ValueError(
                f"Simulation steps '{step_names_by_run_root[run_root]}' and '{step.name}' "
                f"would both write their runs to {run_root}. The directory is named after "
                "the step's artifact; without one, after the artifact_path in its model "
                "specification's input_data; without that, after the model specification "
                "file. Change whichever of these names the directory for one of the steps."
            )
        step_names_by_run_root[run_root] = step.name
