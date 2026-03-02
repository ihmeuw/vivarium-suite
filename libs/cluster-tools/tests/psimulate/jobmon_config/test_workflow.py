"""Unit tests for the Jobmon workflow builder."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, call, patch

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.jobmon_config.workflow import build_workflow
from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.paths import OutputPaths

if TYPE_CHECKING:
    from jobmon.client.workflow import Workflow


def _make_job_parameters(
    input_draw: int = 0,
    random_seed: int = 0,
    branch_configuration: dict[str, Any] | None = None,
) -> JobParameters:
    return JobParameters(
        model_specification="/path/to/model_spec.yaml",
        branch_configuration=branch_configuration or {},
        input_draw=input_draw,
        random_seed=random_seed,
        results_path="/path/to/results",
        backup_configuration={},
        extras={},
    )


FROZEN_TIME = datetime(2025, 1, 1)


@pytest.fixture()
def mock_tool_cls(mocker: MockerFixture) -> MagicMock:
    """Patch the Jobmon ``Tool`` class at its import site."""
    return mocker.patch("vivarium_cluster_tools.psimulate.jobmon_config.workflow.Tool")


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
        _make_job_parameters(input_draw=0, random_seed=0),
        _make_job_parameters(input_draw=0, random_seed=1),
    ]


class TestBuildWorkflow:
    """Verify that ``build_workflow`` passes its arguments into the Jobmon API correctly."""

    @staticmethod
    def _call_build_workflow(
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        job_parameters_list: list[JobParameters],
        workflow_name: str = "test_workflow",
        command: str = "run",
        max_workers: int = 10,
    ) -> Workflow:
        """Import and call ``build_workflow`` with standard test args."""

        return build_workflow(
            workflow_name=workflow_name,
            command=command,
            job_parameters_list=job_parameters_list,
            output_paths=output_paths,
            native_specification=native_spec,
            max_workers=max_workers,
        )

    def test_tool_created_with_correct_name(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """The Jobmon Tool is instantiated with the expected name."""
        self._call_build_workflow(
            mock_tool_cls, mock_write_metadata, output_paths, native_spec, two_jobs
        )
        mock_tool_cls.assert_called_once_with(name="vivarium_cluster_tools_psimulate")

    def test_task_template_args(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """``get_task_template`` receives the expected arguments."""
        self._call_build_workflow(
            mock_tool_cls, mock_write_metadata, output_paths, native_spec, two_jobs
        )
        tool_instance = mock_tool_cls.return_value
        tool_instance.get_task_template.assert_called_once()
        kwargs = tool_instance.get_task_template.call_args.kwargs
        assert kwargs["template_name"] == "psimulate_task"
        assert kwargs["node_args"] == ["task_id"]
        assert kwargs["task_args"] == [
            "metadata_dir",
            "results_dir",
            "worker_log_dir",
            "command",
        ]
        assert kwargs["op_args"] == []
        assert kwargs["default_cluster_name"] == "slurm"
        assert kwargs["default_compute_resources"] == native_spec.to_jobmon_spec.return_value

    def test_write_metadata_called_per_job(
        self,
        mock_tool_cls: MagicMock,
        mock_write_metadata: MagicMock,
        output_paths: OutputPaths,
        native_spec: MagicMock,
        two_jobs: list[JobParameters],
    ) -> None:
        """``write_metadata`` is called once per job with the right args."""
        self._call_build_workflow(
            mock_tool_cls, mock_write_metadata, output_paths, native_spec, two_jobs
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
        """All created tasks are added to the workflow in a single call."""
        self._call_build_workflow(
            mock_tool_cls, mock_write_metadata, output_paths, native_spec, two_jobs
        )
        task_template = mock_tool_cls.return_value.get_task_template.return_value
        workflow = mock_tool_cls.return_value.create_workflow.return_value

        expected_tasks = [task_template.create_task.return_value] * len(two_jobs)
        workflow.add_tasks.assert_called_once_with(expected_tasks)
