"""
================
Workflow Builder
================

Build Jobmon workflows from workflow configuration.

"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from jobmon.client.api import Tool

from vivarium_cluster_tools.psimulate.workflow_config.config import WorkflowConfig

if TYPE_CHECKING:
    from jobmon.client.task import Task
    from jobmon.client.workflow import Workflow


WORKFLOW_ARGS_FILENAME = ".workflow_args"
"""File written to the output directory to persist the Jobmon workflow_args
for resume support."""

BUILD_TIMESTAMP_FILENAME = ".build_timestamp"
"""File written to the output directory to persist the build timestamp
so that resume builds produce identical output paths."""


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
        build_timestamp = self._get_or_create_build_timestamp()
        is_resume = self._is_resume()

        previous_step_tasks: list[Task] = []
        all_tasks: list[Task] = []

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

            step_tasks = step.get_tasks(
                self._tool,
                env=env,
                build_timestamp=build_timestamp,
                is_resume=is_resume,
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

    def _is_resume(self) -> bool:
        """Check whether this is a resumed workflow build.

        Returns True if the build timestamp file already exists in the
        output directory, indicating a previous build has run.
        """
        timestamp_path = self.config.output_directory / BUILD_TIMESTAMP_FILENAME
        return timestamp_path.exists()

    def _get_or_create_build_timestamp(self) -> str:
        """Return a stable build timestamp, persisting it for resume support.

        On a fresh build, generates a new timestamp from ``datetime.now()``
        and writes it to a marker file in the output directory. On resume,
        reads and returns the previously persisted timestamp.

        .. note::

            If you want to re-run a workflow to the same output directory
            after a previous successful run, you must first delete the
            ``.build_timestamp`` file from the output directory. Otherwise
            the new run will reuse the old timestamp and write results
            into the same subdirectories, potentially clobbering data.
            Using a fresh output directory for each new run avoids this.

        Returns
        -------
            Timestamp string in ``YYYY_MM_DD_HH_MM_SS`` format.
        """
        timestamp_path = self.config.output_directory / BUILD_TIMESTAMP_FILENAME
        if timestamp_path.exists():
            return timestamp_path.read_text().strip()

        build_timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        self.config.output_directory.mkdir(parents=True, exist_ok=True)
        timestamp_path.write_text(build_timestamp)
        return build_timestamp
