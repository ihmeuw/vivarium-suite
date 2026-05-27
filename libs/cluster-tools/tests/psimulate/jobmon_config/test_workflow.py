"""Unit tests for the Jobmon workflow builder."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pytest_mock import MockerFixture

from tests.psimulate.conftest import make_job_parameters
from vivarium_cluster_tools.psimulate import TASK_RUNNER_MODULE
from vivarium_cluster_tools.psimulate.jobmon_config.workflow import build_workflow
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.paths import OutputPaths

FROZEN_TIME = datetime(2025, 1, 1)


@pytest.fixture()
def mock_tool_cls(mocker: MockerFixture) -> MagicMock:
    """Patch the Jobmon ``Tool`` class at the façade's import site."""
    return mocker.patch("vivarium_cluster_tools.psimulate.jobmon_config.client.Tool")


@pytest.fixture()
def mock_write_metadata(mocker: MockerFixture) -> MagicMock:
    """Patch ``write_metadata`` at its import site."""
    return mocker.patch(
        "vivarium_cluster_tools.psimulate.jobmon_config.workflow.write_metadata"
    )


@pytest.fixture()
def output_paths(tmp_path: Path) -> OutputPaths:
    """Return an ``OutputPaths`` rooted under ``tmp_path``."""
    with patch("vivarium_cluster_tools.psimulate.paths.datetime") as mock_dt:
        mock_dt.now.return_value = FROZEN_TIME
        return OutputPaths.from_entry_point_args(
            command="restart",
            input_artifact_path=None,
            result_directory=tmp_path,
            input_model_spec_path=None,
        )


@pytest.fixture()
def native_spec() -> MagicMock:
    spec = MagicMock()
    spec.to_jobmon_spec.return_value = {
        "queue": "all.q",
        "project": "proj_simscience",
        "memory": 2.0,
        "runtime": 3600,
        "cores": 1,
    }
    return spec


@pytest.fixture()
def two_jobs() -> list[JobParameters]:
    return [
        make_job_parameters(input_draw=0, random_seed=0),
        make_job_parameters(input_draw=0, random_seed=1),
    ]


class TestBuildWorkflow:
    """Verify that ``build_workflow`` passes its arguments into the Jobmon API correctly."""

    def test_tool_created_with_correct_name(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """The Jobmon Tool is instantiated with the expected name."""
        build_workflow(
            workflow_name="test_workflow",
            command="run",
            job_parameters_list=two_jobs,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=10,
        )
        mock_tool_cls.assert_called_once_with(name="vivarium_cluster_tools")

    def test_task_template_args(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """``get_task_template`` receives the expected arguments."""
        build_workflow(
            workflow_name="test_workflow",
            command="run",
            job_parameters_list=two_jobs,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=10,
        )
        tool_instance = mock_tool_cls.return_value
        tool_instance.get_task_template.assert_called_once()
        kwargs = tool_instance.get_task_template.call_args.kwargs
        assert kwargs["template_name"] == "psimulate"
        assert kwargs["node_args"] == ["task_id"]
        assert kwargs["task_args"] == [
            "metadata_dir",
            "results_dir",
        ]
        assert kwargs["op_args"] == ["command"]
        assert kwargs["default_cluster_name"] == "slurm"
        assert kwargs["default_compute_resources"] == native_spec.to_jobmon_spec.return_value

    def test_command_template_invokes_task_runner(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """The worker command template invokes ``task_runner`` directly.
        Stdout/stderr are routed to the SLURM log files via the compute
        resources dict (see ``NativeSpecification.to_jobmon_spec``), so no
        in-Python wrapper is needed.
        """
        build_workflow(
            workflow_name="test_workflow",
            command="run",
            job_parameters_list=two_jobs,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=10,
        )
        kwargs = mock_tool_cls.return_value.get_task_template.call_args.kwargs
        command_template = kwargs["command_template"]
        assert f"python -m {TASK_RUNNER_MODULE} " in command_template

    def test_write_metadata_called_per_job(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """``write_metadata`` is called once per job with the right args."""
        build_workflow(
            workflow_name="test_workflow",
            command="run",
            job_parameters_list=two_jobs,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=10,
        )
        assert mock_write_metadata.call_count == len(two_jobs)
        mock_write_metadata.assert_has_calls(
            [
                call(
                    metadata_dir=output_paths.metadata_dir,
                    job_parameters=jp,
                )
                for jp in two_jobs
            ]
        )

    def test_tasks_added_to_workflow(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """All created tasks are added to the workflow."""
        build_workflow(
            workflow_name="test_workflow",
            command="run",
            job_parameters_list=two_jobs,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=10,
        )
        task_template = mock_tool_cls.return_value.get_task_template.return_value
        workflow = mock_tool_cls.return_value.create_workflow.return_value

        # create_tasks (plural) is called once with a list of task_ids
        task_template.create_tasks.assert_called_once()
        kwargs = task_template.create_tasks.call_args.kwargs
        assert kwargs["task_id"] == [jp.task_id for jp in two_jobs]
        assert kwargs["max_attempts"] == 3  # default
        assert kwargs["metadata_dir"] == str(output_paths.metadata_dir)
        assert kwargs["results_dir"] == str(output_paths.results_dir)
        assert kwargs["command"] == "run"

        expected_tasks = task_template.create_tasks.return_value
        workflow.add_tasks.assert_called_once_with(expected_tasks)
