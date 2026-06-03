"""Unit tests for the dagger runner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("jobmon")

import yaml
from click.testing import CliRunner

from vivarium_cluster_tools.dagger.config.config import (
    ParsedStep,
    ResourceConfig,
    WorkflowConfig,
)
from vivarium_cluster_tools.dagger.config.utilities import WORKFLOW_ARGS_FILENAME
from vivarium_cluster_tools.dagger.runner import _write_workflow_configuration, run_workflow

_RUNNER = "vivarium_cluster_tools.dagger.runner"


@pytest.fixture
def workflow_config(tmp_path: Path) -> WorkflowConfig:
    """Minimal valid WorkflowConfig for runner tests."""
    output_dir = tmp_path / "workflow_output"
    output_dir.mkdir()
    step_kwargs: dict[str, Any] = {
        "name": "test_step",
        "command": "echo test",
        "resources": ResourceConfig(
            memory_gb=4,
            runtime="01:00:00",
            project="proj_simscience",
            queue="all.q",
        ),
        "output_directory": output_dir,
        "environment": None,
    }
    return WorkflowConfig(
        name="test_workflow",
        project="proj_simscience",
        queue="all.q",
        output_directory=output_dir,
        default_environment=None,
        steps=[
            ParsedStep(
                step_type="bash",
                name="test_step",
                api_kwargs=step_kwargs,
            )
        ],
    )


def _read_configuration_yaml(output_root: Path) -> dict[str, Any]:
    """Read and parse the configuration.yaml written by ``_write_workflow_configuration``."""
    config_file = output_root / "configuration.yaml"
    assert config_file.exists()
    result: dict[str, Any] = yaml.safe_load(config_file.read_text())
    return result


def test_write_workflow_configuration_writes_round_trippable_yaml(tmp_path: Path) -> None:
    """``_write_workflow_configuration`` writes a YAML that captures every
    top-level workflow field plus the step list."""
    output_dir = tmp_path / "workflow_output"
    output_dir.mkdir()

    from vivarium_cluster_tools.dagger.config.config import (
        ParsedStep,
        ResourceConfig,
        WorkflowConfig,
    )

    step_kwargs: dict[str, Any] = {
        "name": "test_step",
        "command": "pytest tests/",
        "resources": ResourceConfig(
            memory_gb=4,
            runtime="01:00:00",
            project="proj_simscience",
            queue="all.q",
        ),
        "output_directory": output_dir,
        "environment": None,
    }
    workflow_config = WorkflowConfig(
        name="test_workflow",
        project="proj_simscience",
        queue="all.q",
        output_directory=output_dir,
        default_environment=None,
        steps=[
            ParsedStep(
                step_type="bash",
                name=step_kwargs["name"],
                api_kwargs=step_kwargs,
            )
        ],
    )

    _write_workflow_configuration(output_dir, workflow_config)

    config = _read_configuration_yaml(output_dir)
    assert config["workflow"]["name"] == "test_workflow"
    assert config["workflow"]["project"] == "proj_simscience"
    assert config["workflow"]["queue"] == "all.q"
    assert config["workflow"]["output_directory"] == str(output_dir)
    assert config["workflow"]["max_attempts"] == 2
    assert len(config["workflow"]["steps"]) == 1
    assert config["workflow"]["steps"][0]["name"] == "test_step"
    assert config["workflow"]["steps"][0]["command"] == "pytest tests/"


def test_workflow_configuration_includes_cli_overrides(tmp_path: Path) -> None:
    """CLI overrides are reflected in the written configuration.yaml."""
    from vivarium_cluster_tools.dagger.cli import dagger

    output_dir = tmp_path / "workflow_output"
    output_dir.mkdir()

    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text(
        yaml.dump(
            {
                "workflow": {
                    "name": "test_workflow",
                    "project": "proj_simscience",
                    "queue": "all.q",
                    "output_directory": str(output_dir),
                    "steps": [
                        {
                            "name": "test_step",
                            "command": "echo test",
                            "resources": {"memory_gb": 4},
                        }
                    ],
                }
            }
        )
    )

    cli_runner = CliRunner()
    with patch("vivarium_cluster_tools.dagger.runner.run_workflow") as mock_run_workflow:

        def mock_impl(**kwargs: Any) -> None:
            _write_workflow_configuration(output_dir, kwargs["workflow_config"])

        mock_run_workflow.side_effect = mock_impl

        result = cli_runner.invoke(
            dagger,
            [
                "run",
                "--config",
                str(pipeline_yaml),
                "-P",
                "proj_simscience_prod",
                "-q",
                "long.q",
            ],
        )

    assert result.exit_code == 0, result.output

    config = _read_configuration_yaml(output_dir)
    assert config["workflow"]["project"] == "proj_simscience_prod"
    assert config["workflow"]["queue"] == "long.q"


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_run_workflow_fresh_run_generates_workflow_args(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    workflow_config: WorkflowConfig,
) -> None:
    """A fresh run generates a timestamped workflow_args, persists it to disk,
    forwards it to the builder, and tags the Slack notification as "dagger run"."""
    mock_bind_and_run.return_value = ("D", "https://jobmon.example/wf/1")

    run_workflow(workflow_config=workflow_config, resume=False)

    workflow_args = mock_build.call_args.kwargs["workflow_args"]
    pattern = rf"^workflow_{workflow_config.name}_[0-9a-f]{{8}}_\d{{8}}_\d{{6}}$"
    assert re.match(pattern, workflow_args), workflow_args

    args_file = workflow_config.output_directory / WORKFLOW_ARGS_FILENAME
    assert args_file.read_text() == workflow_args

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["command_label"] == "dagger run"
    assert slack_kwargs["status"] == "D"


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_run_workflow_resume_reads_existing_workflow_args(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    workflow_config: WorkflowConfig,
) -> None:
    """Resume reads the prior workflow_args from disk and forwards it to the
    builder; bind_and_run_workflow is invoked with resume=True."""
    prior_args = "workflow_test_workflow_abcd1234_20260101_120000"
    (workflow_config.output_directory / WORKFLOW_ARGS_FILENAME).write_text(prior_args)
    mock_bind_and_run.return_value = ("D", None)

    run_workflow(workflow_config=workflow_config, resume=True)

    assert mock_build.call_args.kwargs["workflow_args"] == prior_args
    assert mock_bind_and_run.call_args.kwargs["resume"] is True


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_run_workflow_raises_when_status_not_done(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    workflow_config: WorkflowConfig,
) -> None:
    """A non-DONE workflow status raises RuntimeError, and the Slack
    notification still fires first with the failure status."""
    mock_bind_and_run.return_value = ("F", "https://jobmon.example/wf/2")

    with pytest.raises(RuntimeError, match="'F'"):
        run_workflow(workflow_config=workflow_config, resume=False)

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["status"] == "F"
    assert slack_kwargs["command_label"] == "dagger run"
