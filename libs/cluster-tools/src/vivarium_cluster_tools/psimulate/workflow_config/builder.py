"""
================
Workflow Builder
================

Build Jobmon workflows from workflow configuration.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jobmon.client.api import Tool

from vivarium_cluster_tools.psimulate.workflow_config.config import WorkflowConfig
from vivarium_cluster_tools.psimulate.workflow_config.utilities import (
    get_or_create_build_timestamp,
    is_resume,
    resolve_step_env_prefix,
)

if TYPE_CHECKING:
    from jobmon.client.task import Task
    from jobmon.client.workflow import Workflow


class WorkflowBuilder:
    """Build a complete Jobmon workflow from a workflow configuration.

    For each step in the workflow, creates one or more Jobmon tasks and
    wires dependencies so that steps execute in sequential order (all
    tasks from step *N* must complete before any task in step *N+1*
    starts).
    """

    def __init__(self, config: WorkflowConfig) -> None:
        self.config = config
        self._tool = Tool(name="vivarium_cluster_tools")

    def build(self, workflow_args: str) -> Workflow:
        """Build the full workflow DAG and return the Jobmon Workflow.

        Parameters
        ----------
        workflow_args
            Deterministic string that Jobmon uses to identify the workflow.
            Must be identical across runs for resume to work.
        """
        # TODO: MIC-6997 - encapsulate Jobmon UI in one place
        workflow = self._tool.create_workflow(
            workflow_args=workflow_args,
            name=self.config.name,
            default_cluster_name="slurm",
            default_max_attempts=self.config.max_attempts,
        )

        # Generate a stable build timestamp once per workflow build.
        # On resume, reuse the timestamp from the previous build so that
        # steps produce identical output paths.
        build_timestamp = get_or_create_build_timestamp(self.config.output_directory)
        resuming = is_resume(self.config.output_directory)

        previous_step_tasks: list[Task] = []
        all_tasks: list[Task] = []

        for step in self.config.steps:
            step_tasks = step.get_tasks(
                self._tool,
                env_prefix=resolve_step_env_prefix(
                    step, default_environment=self.config.default_environment
                ),
                build_timestamp=build_timestamp,
                is_resume=resuming,
            )

            # Wire sequential dependencies: every task in this step
            # depends on every task from the previous step.
            for task in step_tasks:
                for prev_task in previous_step_tasks:
                    task.add_upstream(prev_task)

            all_tasks.extend(step_tasks)
            previous_step_tasks = step_tasks

        workflow.add_tasks(all_tasks)

        return workflow
