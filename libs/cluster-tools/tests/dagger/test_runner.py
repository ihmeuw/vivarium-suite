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

from vivarium_cluster_tools.dagger.cli import dagger
from vivarium_cluster_tools.dagger.config.config import (
    ParsedStep,
    ResourceConfig,
    WorkflowConfig,
)
from vivarium_cluster_tools.dagger.config.serialization import workflow_config_to_dict
from vivarium_cluster_tools.dagger.config.utilities import WORKFLOW_ARGS_FILENAME
from vivarium_cluster_tools.dagger.runner import (
    _write_workflow_configuration,
    restart_workflow,
    run_workflow,
)

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

    run_workflow(workflow_config=workflow_config)

    workflow_args = mock_build.call_args.kwargs["workflow_args"]
    pattern = rf"^workflow_{workflow_config.name}_[0-9a-f]{{8}}_\d{{8}}_\d{{6}}$"
    assert re.match(pattern, workflow_args), workflow_args
    assert mock_build.call_args.kwargs["resume"] is False

    args_file = workflow_config.output_directory / WORKFLOW_ARGS_FILENAME
    assert args_file.read_text() == workflow_args

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["command_label"] == "dagger run"
    assert slack_kwargs["status"] == "D"


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
        run_workflow(workflow_config=workflow_config)

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["status"] == "F"
    assert slack_kwargs["command_label"] == "dagger run"


def _seed_resumable_output(
    results_dir: Path,
    *,
    name: str = "test_workflow",
    project: str = "proj_simscience",
    queue: str = "all.q",
    output_directory: str | None = None,
    workflow_args: str = "workflow_test_workflow_abcd1234_20260101_120000",
    write_args: bool = True,
) -> None:
    """Lay out *results_dir* as a previous ``dagger run`` left it.

    Writes a ``configuration.yaml`` (and, by default, a ``.workflow_args``)
    so the directory looks like a resumable workflow output.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    workflow: dict[str, Any] = {
        "name": name,
        "project": project,
        "queue": queue,
        "output_directory": output_directory or str(results_dir),
        "steps": [
            {"name": "test_step", "command": "echo test", "resources": {"memory_gb": 4}}
        ],
    }
    (results_dir / "configuration.yaml").write_text(yaml.dump({"workflow": workflow}))
    if write_args:
        (results_dir / WORKFLOW_ARGS_FILENAME).write_text(workflow_args)


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_loads_saved_configuration(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """restart reads configuration.yaml from the results dir and builds from it."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir)
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(results_dir)

    assert mock_build.call_args.args[0].name == "test_workflow"


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_reuses_persisted_workflow_args(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """restart reads the persisted .workflow_args, forwards it to the builder,
    and resumes the Jobmon workflow (resume=True)."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir, workflow_args="workflow_reused_args")
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(results_dir)

    assert mock_build.call_args.kwargs["workflow_args"] == "workflow_reused_args"
    assert mock_build.call_args.kwargs["resume"] is True
    assert mock_bind_and_run.call_args.kwargs["resume"] is True


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_forces_output_directory_to_results_dir(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """Even if the saved config points elsewhere, restart uses the given results dir."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir, output_directory="/stale/path")
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(results_dir)

    assert mock_build.call_args.args[0].output_directory == results_dir


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_applies_project_override(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """A project override is merged over the saved config before building."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir, project="proj_simscience")
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(results_dir, project="proj_simscience_prod")

    assert mock_build.call_args.args[0].project == "proj_simscience_prod"


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_notifies_with_restart_label(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """restart sends a Slack notification labelled 'dagger restart'."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir)
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(results_dir)

    assert mock_slack.call_args.kwargs["command_label"] == "dagger restart"


def test_restart_missing_workflow_args_errors(tmp_path: Path) -> None:
    """A results dir without .workflow_args raises a clear error (not resumable)."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir, write_args=False)

    with pytest.raises(FileNotFoundError, match="workflow_args"):
        restart_workflow(results_dir)


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_run_workflow_forwards_slack_options(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    workflow_config: WorkflowConfig,
) -> None:
    """run_workflow threads slack_channel/slack_tag into the Slack notification."""
    mock_bind_and_run.return_value = ("D", "url")

    run_workflow(
        workflow_config=workflow_config,
        slack_channel="my-channel",
        slack_tag="coworker",
        mute_slack=True,
    )

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["slack_channel"] == "my-channel"
    assert slack_kwargs["slack_tag"] == "coworker"
    assert slack_kwargs["mute_slack"] is True


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_restart_workflow_forwards_slack_options(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    tmp_path: Path,
) -> None:
    """restart_workflow threads slack_channel/slack_tag into the Slack notification."""
    results_dir = tmp_path / "results"
    _seed_resumable_output(results_dir)
    mock_bind_and_run.return_value = ("D", "url")

    restart_workflow(
        results_dir,
        slack_channel="my-channel",
        slack_tag="coworker",
        mute_slack=True,
    )

    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["slack_channel"] == "my-channel"
    assert slack_kwargs["slack_tag"] == "coworker"
    assert slack_kwargs["mute_slack"] is True


@patch(f"{_RUNNER}.get_workflow_timeout_seconds", return_value=3600)
@patch(f"{_RUNNER}.send_slack_notification")
@patch(f"{_RUNNER}.client.bind_and_run_workflow")
@patch(f"{_RUNNER}.build_workflow_from_config")
def test_run_then_restart_roundtrip(
    mock_build: Any,
    mock_bind_and_run: Any,
    mock_slack: Any,
    mock_timeout: Any,
    workflow_config: WorkflowConfig,
) -> None:
    """restart rebuilds the configuration that run wrote (full round-trip equivalence)."""
    mock_bind_and_run.return_value = ("D", "url")

    run_workflow(
        workflow_config=workflow_config
    )  # writes configuration.yaml + .workflow_args
    restart_workflow(workflow_config.output_directory)

    rebuilt = mock_build.call_args.args[0]
    assert workflow_config_to_dict(rebuilt) == workflow_config_to_dict(workflow_config)
