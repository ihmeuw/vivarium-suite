"""
=============
dagger Runner
=============

The main process loop for ``dagger run`` invocations: parse the
workflow configuration, build the Jobmon workflow, bind and run it,
and notify on completion.
"""

from __future__ import annotations

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
from vivarium_cluster_tools.dagger.config.parsing import load_workflow_config
from vivarium_cluster_tools.dagger.config.serialization import workflow_config_to_dict
from vivarium_cluster_tools.dagger.config.utilities import (
    CONFIGURATION_FILENAME,
    WORKFLOW_ARGS_FILENAME,
)
from vivarium_cluster_tools.utilities import hash_output_path


def run_workflow(
    workflow_config: WorkflowConfig,
    verbose: int = 0,
    slack_channel: str | None = None,
    slack_tag: str | None = None,
    mute_slack: bool = False,
) -> None:
    """Entry point for the ``dagger run`` subcommand: start a fresh workflow.

    Parameters
    ----------
    workflow_config
        The parsed and validated workflow configuration (with CLI overrides applied).
    verbose
        Verbosity level.
    slack_channel
        Optional Slack channel to post a successful-run notification to instead
        of DMing the launching user.
    slack_tag
        Optional username to @-mention in the channel notification on success.
    mute_slack
        If ``True``, suppress the completion notification entirely.
    """
    logger.info(f"Starting workflow: {workflow_config.name}")

    output_root = workflow_config.output_directory
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_hash = hash_output_path(output_root)
    workflow_id = f"workflow_{workflow_config.name}_{output_hash}_{timestamp}"

    _execute_workflow(
        workflow_config,
        workflow_id=workflow_id,
        resume=False,
        command_label="dagger run",
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
    )


def restart_workflow(
    results_directory: Path,
    *,
    project: str | None = None,
    queue: str | None = None,
    max_attempts: int | None = None,
    verbose: int = 0,
    slack_channel: str | None = None,
    slack_tag: str | None = None,
    mute_slack: bool = False,
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
    slack_channel
        Optional Slack channel to post a successful-run notification to instead
        of DMing the launching user.
    slack_tag
        Optional username to @-mention in the channel notification on success.
    mute_slack
        If ``True``, suppress the completion notification entirely.

    Raises
    ------
    FileNotFoundError
        If ``results_directory`` is not a resumable ``dagger run`` output (it
        lacks a saved configuration or the persisted workflow args).
    """
    logger.info(f"Restarting workflow from {results_directory}")

    config_path = results_directory / CONFIGURATION_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"{results_directory} is not a resumable dagger run output: "
            f"missing {CONFIGURATION_FILENAME}."
        )
    workflow_args_path = results_directory / WORKFLOW_ARGS_FILENAME
    if not workflow_args_path.exists():
        raise FileNotFoundError(
            f"{results_directory} is not a resumable dagger run output: "
            f"missing {WORKFLOW_ARGS_FILENAME} (persisted workflow_args)."
        )

    workflow_id = workflow_args_path.read_text().strip()
    logger.info(f"Resuming workflow with args: {workflow_id}")

    # Reload the saved config, forcing the output directory to the results dir
    workflow_config = load_workflow_config(
        config_path,
        project=project,
        queue=queue,
        max_attempts=max_attempts,
        output_directory=results_directory,
    )

    _execute_workflow(
        workflow_config,
        workflow_id=workflow_id,
        resume=True,
        command_label="dagger restart",
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
    )


def _execute_workflow(
    workflow_config: WorkflowConfig,
    *,
    workflow_id: str,
    resume: bool,
    command_label: str,
    slack_channel: str | None = None,
    slack_tag: str | None = None,
    mute_slack: bool = False,
) -> None:
    """Build, bind, run, and report a workflow; shared by run and restart.

    Persists the configuration and ``workflow_id`` (Jobmon's ``workflow_args``)
    to the output directory *before* building, so that a workflow which fails
    early is still restartable by ``dagger restart``.
    """
    output_root = workflow_config.output_directory
    output_root.mkdir(parents=True, exist_ok=True)

    _write_workflow_configuration(output_root, workflow_config)
    (output_root / WORKFLOW_ARGS_FILENAME).write_text(workflow_id)

    logger.debug("Building workflow.")
    workflow = build_workflow_from_config(
        workflow_config, workflow_args=workflow_id, resume=resume
    )

    wf_status, monitoring_url = client.bind_and_run_workflow(
        workflow,
        output_root,
        resume=resume,
        seconds_until_timeout=get_workflow_timeout_seconds(),
    )

    send_slack_notification(
        workflow_name=workflow_config.name,
        status=wf_status,
        command_label=command_label,
        monitoring_url=monitoring_url,
        results_dir=str(output_root),
        slack_channel=slack_channel,
        slack_tag=slack_tag,
        mute_slack=mute_slack,
    )

    if wf_status != client.JOBMON_STATUS_DONE:
        raise RuntimeError(
            f"Workflow finished with status '{wf_status}' "
            f"(expected '{client.JOBMON_STATUS_DONE}' for DONE)."
        )
    logger.info(f"Workflow completed successfully. Results in {output_root}")


def _write_workflow_configuration(output_root: Path, workflow_config: WorkflowConfig) -> None:
    """Write workflow configuration to a YAML file in the output directory.

    Creates a ``configuration.yaml`` that ``dagger restart`` reloads to resume
    the workflow (and that can also be reused directly with ``dagger run -c``).

    Parameters
    ----------
    output_root
        The root output directory for the workflow.
    workflow_config
        The parsed and validated workflow configuration.
    """
    config: dict[str, Any] = {"workflow": workflow_config_to_dict(workflow_config)}
    config_file = output_root / CONFIGURATION_FILENAME
    config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    logger.info(f"Run configuration written to {config_file}")
