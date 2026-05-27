"""Unit tests for the workflow builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.workflow_config.builder import (
    build_workflow_from_config,
)
from vivarium_cluster_tools.psimulate.workflow_config.config import (
    ParsedStep,
    ResourceConfig,
    WorkflowConfig,
)


def _bash_parsed_step(
    *,
    name: str,
    resources: ResourceConfig,
    command: str,
    output_directory: Path,
    environment: str | None = None,
) -> ParsedStep:
    """Build a ``ParsedStep`` carrying a bash step's API kwargs."""
    api_kwargs = {
        "name": name,
        "resources": resources,
        "command": command,
        "output_directory": output_directory,
        "environment": environment,
    }
    return ParsedStep(
        step_type="bash",
        name=name,
        api_kwargs=api_kwargs,
    )


@pytest.fixture()
def three_step_config() -> WorkflowConfig:
    """A ``WorkflowConfig`` with three sequential bash steps."""
    return WorkflowConfig(
        name="test_workflow",
        project="proj_simscience",
        queue="all.q",
        output_directory=Path("/tmp/results"),
        default_environment=None,
        steps=[
            _bash_parsed_step(
                name=f"step{i}",
                resources=ResourceConfig(
                    memory_gb=1, project="proj_simscience", queue="all.q"
                ),
                command=f"echo step{i}",
                output_directory=Path("/tmp/results"),
            )
            for i in (1, 2, 3)
        ],
    )


@pytest.fixture()
def mock_tool_cls(mocker: MockerFixture) -> MagicMock:
    """Patch the Jobmon ``Tool`` class at the façade's import site."""
    return mocker.patch("vivarium_cluster_tools.psimulate.jobmon_config.client.Tool")


@pytest.fixture(autouse=True)
def mock_resolve_env_prefix(mocker: MockerFixture) -> MagicMock:
    """Patch ``resolve_env_prefix`` to echo back its input as the prefix.

    Lets tests assert the env resolution chain by checking what name was
    passed through, without invoking the real ``conda env list`` lookup.
    """
    return mocker.patch(
        "vivarium_cluster_tools.psimulate.workflow_config.utilities.resolve_env_prefix",
        side_effect=lambda env: env,
    )


@pytest.fixture(autouse=True)
def mock_build_timestamp(mocker: MockerFixture) -> str:
    """Patch the build timestamp so tests don't write to the filesystem.

    The interface API functions import ``get_or_create_build_timestamp``
    from utilities; patching the imported name here intercepts every call.
    """
    ts = "2026_04_24_10_00_00"
    mocker.patch(
        "vivarium_cluster_tools.psimulate.workflow_config.interface.get_or_create_build_timestamp",
        return_value=ts,
    )
    mocker.patch(
        "vivarium_cluster_tools.psimulate.workflow_config.builder.is_resume",
        return_value=False,
    )
    return ts


def _make_single_step_config(
    *,
    resources: ResourceConfig | None = None,
    default_environment: str | None = None,
    step_environment: str | None = None,
) -> WorkflowConfig:
    """Build a WorkflowConfig with one bash ParsedStep for simple tests."""
    return WorkflowConfig(
        name="test",
        project="proj_simscience",
        queue="all.q",
        output_directory=Path("/tmp/results"),
        default_environment=default_environment,
        steps=[
            _bash_parsed_step(
                name="s1",
                resources=resources
                or ResourceConfig(memory_gb=1, project="proj_simscience", queue="all.q"),
                command="echo hi",
                output_directory=Path("/tmp/results"),
                environment=step_environment,
            )
        ],
    )


class TestBuildWorkflowFromConfig:
    """Verify that ``build_workflow_from_config`` builds a correct Jobmon workflow."""

    def test_valid_config_builds_workflow(
        self,
        three_step_config: WorkflowConfig,
        mock_tool_cls: MagicMock,
    ) -> None:
        """A valid config produces a Jobmon Workflow with tasks added."""
        workflow = build_workflow_from_config(
            three_step_config, workflow_args="test_workflow_args"
        )

        expected_workflow = mock_tool_cls.return_value.create_workflow.return_value
        assert workflow is expected_workflow

    def test_dag_preserves_step_ordering(
        self,
        three_step_config: WorkflowConfig,
        mock_tool_cls: MagicMock,
    ) -> None:
        """A 3-step workflow produces a DAG: step1 -> step2 -> step3."""
        task1 = MagicMock(name="task_step1")
        task2 = MagicMock(name="task_step2")
        task3 = MagicMock(name="task_step3")

        template_mock = mock_tool_cls.return_value.get_task_template.return_value
        template_mock.create_task.side_effect = [task1, task2, task3]

        build_workflow_from_config(three_step_config, workflow_args="test_workflow_args")

        # Each config step produces exactly one task
        assert template_mock.create_task.call_count == 3

        # step1 has no upstream
        task1.add_upstream.assert_not_called()
        # step2 depends on step1
        task2.add_upstream.assert_called_once_with(task1)
        # step3 depends on step2
        task3.add_upstream.assert_called_once_with(task2)


class TestResourceDefaults:
    """Verify that ``build_workflow_from_config`` passes resource values through to Jobmon."""

    def test_default_resources(self, mock_tool_cls: MagicMock) -> None:
        """Steps with default ResourceConfig get the expected compute resources."""
        config = _make_single_step_config()
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        build_workflow_from_config(config, workflow_args="test_workflow_args")

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["compute_resources"]["memory"] == 1.0
        assert call_kwargs["compute_resources"]["runtime"] == 3600
        assert call_kwargs["compute_resources"]["cores"] == 1

    def test_custom_resources(self, mock_tool_cls: MagicMock) -> None:
        """Steps with custom ResourceConfig values are passed through."""
        config = _make_single_step_config(
            resources=ResourceConfig(
                memory_gb=16,
                runtime="04:00:00",
                cores=8,
                project="proj_simscience",
                queue="all.q",
            ),
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        build_workflow_from_config(config, workflow_args="test_workflow_args")

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["compute_resources"]["memory"] == 16.0
        assert call_kwargs["compute_resources"]["runtime"] == 14400
        assert call_kwargs["compute_resources"]["cores"] == 8


class TestEnvironmentResolution:
    """Verify the environment fallback chain in ``build_workflow_from_config``."""

    def test_step_environment_takes_priority(self, mock_tool_cls: MagicMock) -> None:
        """A step's own environment overrides the workflow default."""
        config = _make_single_step_config(
            default_environment="workflow_env",
            step_environment="step_env",
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        build_workflow_from_config(config, workflow_args="test_workflow_args")

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env_prefix"] == "step_env"

    def test_workflow_default_environment(self, mock_tool_cls: MagicMock) -> None:
        """When step has no environment, workflow default_environment is used."""
        config = _make_single_step_config(default_environment="workflow_env")
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        build_workflow_from_config(config, workflow_args="test_workflow_args")

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env_prefix"] == "workflow_env"

    def test_conda_env_variable_fallback(
        self, mock_tool_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no step or workflow env, falls back to CONDA_DEFAULT_ENV."""
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "conda_env")
        config = _make_single_step_config()
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        build_workflow_from_config(config, workflow_args="test_workflow_args")

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env_prefix"] == "conda_env"

    def test_base_fallback(
        self, mock_tool_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When nothing is set, raises ValueError for base environment."""
        monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
        config = _make_single_step_config()

        with pytest.raises(ValueError, match="non-base conda environment is required"):
            build_workflow_from_config(config, workflow_args="test_workflow_args")
