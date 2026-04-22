"""Unit tests for the workflow builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.workflow_config.builder import WorkflowBuilder
from vivarium_cluster_tools.psimulate.workflow_config.config import (
    ResourceConfig,
    StepConfig,
    WorkflowConfig,
)


@pytest.fixture()
def three_step_config() -> WorkflowConfig:
    """A ``WorkflowConfig`` with three sequential raw-command steps."""
    return WorkflowConfig(
        name="test_workflow",
        project="proj_simscience",
        queue="all.q",
        output_directory=Path("/tmp/results"),
        default_environment=None,
        steps=[
            StepConfig(
                name="step1", resources=ResourceConfig(memory_gb=1), command="echo step1"
            ),
            StepConfig(
                name="step2", resources=ResourceConfig(memory_gb=1), command="echo step2"
            ),
            StepConfig(
                name="step3", resources=ResourceConfig(memory_gb=1), command="echo step3"
            ),
        ],
    )


@pytest.fixture()
def mock_tool_cls(mocker: MockerFixture) -> MagicMock:
    """Patch the Jobmon ``Tool`` class at the builder's import site."""
    return mocker.patch("vivarium_cluster_tools.psimulate.workflow_config.builder.Tool")


class TestWorkflowBuilder:
    """Verify that ``WorkflowBuilder`` builds a correct Jobmon workflow."""

    def test_valid_config_builds_workflow(
        self,
        three_step_config: WorkflowConfig,
        mock_tool_cls: MagicMock,
    ) -> None:
        """A valid config produces a Jobmon Workflow with tasks added."""
        builder = WorkflowBuilder(three_step_config)
        workflow = builder.build()

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

        builder = WorkflowBuilder(three_step_config)
        builder.build()

        # Each config step produces exactly one task
        assert template_mock.create_task.call_count == 3

        # step1 has no upstream
        task1.add_upstream.assert_not_called()
        # step2 depends on step1
        task2.add_upstream.assert_called_once_with(task1)
        # step3 depends on step2
        task3.add_upstream.assert_called_once_with(task2)


class TestResourceDefaults:
    """Verify that ``WorkflowBuilder`` passes resource values through to Jobmon."""

    def test_default_resources(self, mock_tool_cls: MagicMock) -> None:
        """Steps with default ResourceConfig get the expected compute resources."""
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment=None,
            steps=[
                StepConfig(
                    name="s1", resources=ResourceConfig(memory_gb=1), command="echo hi"
                )
            ],
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        WorkflowBuilder(config).build()

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["compute_resources"]["memory"] == 1
        assert call_kwargs["compute_resources"]["runtime"] == "01:00:00"
        assert call_kwargs["compute_resources"]["cores"] == 1

    def test_custom_resources(self, mock_tool_cls: MagicMock) -> None:
        """Steps with custom ResourceConfig values are passed through."""
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment=None,
            steps=[
                StepConfig(
                    name="s1",
                    resources=ResourceConfig(memory_gb=16, runtime="04:00:00", cores=8),
                    command="echo hi",
                ),
            ],
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        WorkflowBuilder(config).build()

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["compute_resources"]["memory"] == 16
        assert call_kwargs["compute_resources"]["runtime"] == "04:00:00"
        assert call_kwargs["compute_resources"]["cores"] == 8


class TestEnvironmentResolution:
    """Verify the environment fallback chain in ``WorkflowBuilder.build``."""

    def test_step_environment_takes_priority(self, mock_tool_cls: MagicMock) -> None:
        """A step's own environment overrides the workflow default."""
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment="workflow_env",
            steps=[
                StepConfig(
                    name="s1",
                    resources=ResourceConfig(memory_gb=1),
                    command="echo hi",
                    environment="step_env",
                ),
            ],
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        WorkflowBuilder(config).build()

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env"] == "step_env"

    def test_workflow_default_environment(self, mock_tool_cls: MagicMock) -> None:
        """When step has no environment, workflow default_environment is used."""
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment="workflow_env",
            steps=[
                StepConfig(
                    name="s1", resources=ResourceConfig(memory_gb=1), command="echo hi"
                )
            ],
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        WorkflowBuilder(config).build()

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env"] == "workflow_env"

    def test_conda_env_variable_fallback(
        self, mock_tool_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no step or workflow env, falls back to CONDA_DEFAULT_ENV."""
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "conda_env")
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment=None,
            steps=[
                StepConfig(
                    name="s1", resources=ResourceConfig(memory_gb=1), command="echo hi"
                )
            ],
        )
        template_mock = mock_tool_cls.return_value.get_task_template.return_value

        WorkflowBuilder(config).build()

        call_kwargs = template_mock.create_task.call_args[1]
        assert call_kwargs["env"] == "conda_env"

    def test_base_fallback(
        self, mock_tool_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When nothing is set, raises ValueError for base environment."""
        monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
        config = WorkflowConfig(
            name="test",
            project="proj_simscience",
            queue="all.q",
            output_directory=Path("/tmp/results"),
            default_environment=None,
            steps=[
                StepConfig(
                    name="s1", resources=ResourceConfig(memory_gb=1), command="echo hi"
                )
            ],
        )

        with pytest.raises(ValueError, match="non-base conda environment is required"):
            WorkflowBuilder(config).build()
