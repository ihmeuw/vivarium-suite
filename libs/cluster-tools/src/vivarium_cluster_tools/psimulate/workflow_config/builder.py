"""
================
Workflow Builder
================

Build Jobmon workflows from workflow configuration.

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from jobmon.client.api import Tool

from vivarium_cluster_tools.psimulate.workflow_config.config import StepConfig, WorkflowConfig

if TYPE_CHECKING:
    from jobmon.client.workflow import Workflow


class WorkflowBuilder:
    """Build a complete Jobmon workflow from a workflow configuration.

    For each step in the workflow, creates a Jobmon task and wires
    dependencies so that steps execute in sequential order in which
    they are defined in the configuration file.
    """

    def __init__(self, config: WorkflowConfig) -> None:
        self.config = config
        self._tool = Tool(name="vivarium_cluster_tools")

    def build(self) -> Workflow:
        """Build the full workflow DAG and return the Jobmon Workflow."""
        # TODO: MIC-6997 - encapsulate Jobmon UI in one place
        task_template = self._tool.get_task_template(
            template_name="workflow_command_step",
            command_template="conda run --no-capture-output -n {env} {command}",
            node_args=["command"],
            task_args=[],
            op_args=["env"],
            default_cluster_name="slurm",
        )

        # TODO: MIC-6997 - encapsulate Jobmon UI in one place
        workflow = self._tool.create_workflow(
            name=self.config.name,
            default_cluster_name="slurm",
            default_max_attempts=3,
        )

        tasks = []
        for step in self.config.steps:
            env = (
                step.environment
                or self.config.default_environment
                or os.environ.get("CONDA_DEFAULT_ENV")
            )
            if not env or env == "base":
                raise ValueError(
                    f"Step '{step.name}': a non-base conda environment is required. "
                    "Set 'environment' on the step, 'default_environment' on the workflow, "
                    "or activate a conda environment before running."
                )
            resources = step.resources
            compute_resources = {
                "queue": self.config.queue,
                "project": self.config.project,
                "memory": resources.memory_gb,
                "runtime": resources.runtime,
                "cores": resources.cores,
            }

            task = task_template.create_task(
                name=step.name,
                compute_resources=compute_resources,
                env=env,
                command=step.command,
            )
            tasks.append(task)

        # Wire sequential dependencies: each step depends on the previous
        for i in range(1, len(tasks)):
            tasks[i].add_upstream(tasks[i - 1])

        workflow.add_tasks(tasks)

        return workflow
