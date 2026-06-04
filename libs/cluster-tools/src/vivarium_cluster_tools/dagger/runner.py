"""
=============
dagger Runner
=============

The main process loop for ``dagger run`` invocations: parse the
workflow configuration, build the Jobmon workflow, bind and run it,
and notify on completion.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from vivarium_cluster_tools.core.cluster.interface import get_workflow_timeout_seconds
from vivarium_cluster_tools.core.jobmon import client
from vivarium_cluster_tools.core.notifications import send_slack_notification
from vivarium_cluster_tools.dagger.config.builder import build_workflow_from_config
from vivarium_cluster_tools.dagger.config.config import WorkflowConfig
from vivarium_cluster_tools.dagger.config.serialization import workflow_config_to_dict
from vivarium_cluster_tools.dagger.config.utilities import WORKFLOW_ARGS_FILENAME


def run_workflow(
    workflow_config: WorkflowConfig,
    verbose: int = 0,
    resume: bool = False,
) -> None:
    """Entry point for the ``dagger run`` subcommand.

    Parameters
    ----------
    workflow_config
        The parsed and validated workflow configuration (with CLI overrides applied).
    verbose
        Verbosity level.
    resume
        Whether to resume a previously started workflow.
    """
    logger.info(f"Starting workflow: {workflow_config.name}")

    # Create output directory if it doesn't exist
    output_root = workflow_config.output_directory
    output_root.mkdir(parents=True, exist_ok=True)

    workflow_args_path = output_root / WORKFLOW_ARGS_FILENAME

    if resume:
        workflow_args = workflow_args_path.read_text().strip()
        logger.info(f"Resuming workflow with args: {workflow_args}")
    else:
        # Generate a unique workflow_args using a timestamp so each fresh
        # run is distinct even with the same config and output directory.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_hash = hashlib.md5(str(output_root).encode()).hexdigest()[:8]
        workflow_args = f"workflow_{workflow_config.name}_{output_hash}_{timestamp}"

    # Write the requested configuration to output directory
    _write_workflow_configuration(output_root, workflow_config)

    # Build the workflow
    logger.debug("Building workflow.")
    workflow = build_workflow_from_config(workflow_config, workflow_args=workflow_args)

    # Persist workflow_args before running so --resume can find it
    workflow_args_path.write_text(workflow_args)

    wf_status, monitoring_url = client.bind_and_run_workflow(
        workflow,
        output_root,
        resume=resume,
        seconds_until_timeout=get_workflow_timeout_seconds(),
    )

    send_slack_notification(
        workflow_name=workflow_config.name,
        status=wf_status,
        command_label="dagger run",
        monitoring_url=monitoring_url,
        results_dir=str(output_root),
    )

    if wf_status != client.JOBMON_STATUS_DONE:
        raise RuntimeError(
            f"Workflow finished with status '{wf_status}' "
            f"(expected '{client.JOBMON_STATUS_DONE}' for DONE)."
        )
    logger.info(f"Workflow completed successfully. Results in {output_root}")


def restart_workflow(
    results_directory: Path,
    *,
    project: str | None = None,
    queue: str | None = None,
    max_attempts: int | None = None,
    verbose: int = 0,
) -> None:
    """Resume a previously started ``dagger`` workflow from its output directory.

    Reloads the ``configuration.yaml`` and persisted Jobmon ``workflow_args``
    written by the original ``dagger run`` invocation, applies any CLI
    overrides, forces the output directory to ``results_directory``, and resumes
    the Jobmon workflow, skipping completed tasks.

    Parameters
    ----------
    results_directory
        Output directory from a previous ``dagger run``. The workflow's output
        directory is forced to this path.
    project
        Override for the workflow project.
    queue
        Override for the workflow queue.
    max_attempts
        Override for the maximum number of Jobmon task attempts.
    verbose
        Verbosity level.
    """
    raise NotImplementedError  # [stub] Implement in Phase 2.


def _write_workflow_configuration(output_root: Path, workflow_config: WorkflowConfig) -> None:
    """Write workflow configuration to a YAML file in the output directory.

    Creates a ``configuration.yaml`` that can be reused directly with
    ``dagger run --config configuration.yaml``.

    Parameters
    ----------
    output_root
        The root output directory for the workflow.
    workflow_config
        The parsed and validated workflow configuration.
    """
    config: dict[str, Any] = {"workflow": workflow_config_to_dict(workflow_config)}
    config_file = output_root / "configuration.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    logger.info(f"Run configuration written to {config_file}")
