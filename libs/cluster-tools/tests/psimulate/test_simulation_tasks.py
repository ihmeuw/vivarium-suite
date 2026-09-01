"""Unit tests for the shared parallel-simulation task pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

pytest.importorskip("jobmon")

import yaml
from pandas.testing import assert_frame_equal
from pytest_mock import MockerFixture

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate import COMMANDS
from vivarium.cluster_tools.psimulate.jobs import (
    BackupConfiguration,
    JobParameters,
    generate_task_id,
)
from vivarium.cluster_tools.psimulate.paths import InputPaths
from vivarium.cluster_tools.psimulate.simulation_tasks import (
    SimulationRun,
    build_simulation_tasks,
    report_initial_status,
    resolve_output_paths,
    resolve_simulation_run,
    write_backup_metadata,
)

_MODULE = "vivarium.cluster_tools.psimulate.simulation_tasks"

_BRANCH_CONFIG: dict[str, Any] = {
    "input_draw_count": 2,
    "random_seed_count": 2,
}


@pytest.fixture()
def model_spec_file(tmp_path: Path) -> Path:
    """A minimal model specification the framework can build."""
    path = tmp_path / "model_spec.yaml"
    path.write_text(
        yaml.dump({"components": {}, "configuration": {"input_data": {}}}),
    )
    return path


@pytest.fixture()
def branch_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "branches.yaml"
    path.write_text(yaml.dump(_BRANCH_CONFIG))
    return path


@pytest.fixture()
def run_input_paths(
    tmp_path: Path, model_spec_file: Path, branch_config_file: Path
) -> InputPaths:
    return InputPaths.from_entry_point_args(
        result_directory=tmp_path / "results",
        input_model_specification_path=model_spec_file,
        input_branch_configuration_path=branch_config_file,
    )


class TestResolveSimulationRun:
    """Verify what a resolved run writes into its output directory."""

    def test_run_persists_keyspace_branches_and_model_spec(
        self, run_input_paths: InputPaths
    ) -> None:
        """A ``run`` writes the audit trail a later restart reads back."""
        output_paths = resolve_output_paths(command=COMMANDS.run, input_paths=run_input_paths)
        run = resolve_simulation_run(
            command=COMMANDS.run,
            input_paths=run_input_paths,
            output_paths=output_paths,
            extra_args={},
        )
        assert output_paths.keyspace.exists()
        assert output_paths.branches.exists()
        assert output_paths.model_specification.exists()
        assert len(run.keyspace) == 4
        assert run.finished_sim_metadata.empty

    def test_model_spec_records_results_directory(self, run_input_paths: InputPaths) -> None:
        """The persisted spec points the simulation at the run's own root."""
        output_paths = resolve_output_paths(command=COMMANDS.run, input_paths=run_input_paths)
        resolve_simulation_run(
            command=COMMANDS.run,
            input_paths=run_input_paths,
            output_paths=output_paths,
            extra_args={},
        )
        persisted = yaml.safe_load(output_paths.model_specification.read_text())
        assert persisted["configuration"]["output_data"]["results_directory"] == str(
            output_paths.root
        )

    def test_artifact_path_reaches_the_persisted_model_spec(
        self, tmp_path: Path, model_spec_file: Path, branch_config_file: Path
    ) -> None:
        """An artifact given on the command line lands in the spec the workers read."""
        artifact = tmp_path / "artifact.hdf"
        artifact.touch()
        input_paths = InputPaths.from_entry_point_args(
            result_directory=tmp_path / "results",
            input_model_specification_path=model_spec_file,
            input_branch_configuration_path=branch_config_file,
            input_artifact_path=artifact,
        )
        output_paths = resolve_output_paths(command=COMMANDS.run, input_paths=input_paths)
        resolve_simulation_run(
            command=COMMANDS.run,
            input_paths=input_paths,
            output_paths=output_paths,
            extra_args={},
        )
        persisted = yaml.safe_load(output_paths.model_specification.read_text())
        assert persisted["configuration"]["input_data"]["artifact_path"] == str(artifact)

    def test_restart_reads_the_persisted_keyspace(self, run_input_paths: InputPaths) -> None:
        """A restart rebuilds its keyspace from the run's files, not the inputs."""
        output_paths = resolve_output_paths(command=COMMANDS.run, input_paths=run_input_paths)
        resolve_simulation_run(
            command=COMMANDS.run,
            input_paths=run_input_paths,
            output_paths=output_paths,
            extra_args={},
        )

        restart_input_paths = InputPaths.from_entry_point_args(
            result_directory=output_paths.root
        )
        restart_output_paths = resolve_output_paths(
            command=COMMANDS.restart, input_paths=restart_input_paths
        )
        assert restart_output_paths.root == output_paths.root

        restart_run = resolve_simulation_run(
            command=COMMANDS.restart,
            input_paths=restart_input_paths,
            output_paths=restart_output_paths,
            extra_args={},
        )
        assert len(restart_run.keyspace) == 4

    def test_restart_without_persisted_state_names_what_is_missing(
        self, tmp_path: Path
    ) -> None:
        """A run directory predating keyspace persistence is rejected clearly.

        Without this, the resume reads an empty keyspace and the failure
        surfaces as a ConfigurationError blaming the caller for a model
        specification they never supplied.
        """
        input_paths = InputPaths.from_entry_point_args(result_directory=tmp_path)
        output_paths = resolve_output_paths(command=COMMANDS.restart, input_paths=input_paths)
        with pytest.raises(FileNotFoundError, match="cannot be resumed"):
            resolve_simulation_run(
                command=COMMANDS.restart,
                input_paths=input_paths,
                output_paths=output_paths,
                extra_args={},
            )


class TestBuildSimulationTasks:
    """Verify the task list built from a resolved run."""

    @staticmethod
    def _run(tmp_path: Path, command: str = COMMANDS.run) -> SimulationRun:
        output_paths = resolve_output_paths(
            command=COMMANDS.restart,
            input_paths=InputPaths.from_entry_point_args(result_directory=tmp_path),
        )
        keyspace = MagicMock()
        keyspace.__iter__.return_value = iter(
            [(draw, seed, {}) for draw in (0, 1) for seed in (0, 1)]
        )
        keyspace.__len__.return_value = 4
        return SimulationRun(
            command=command,
            output_paths=output_paths,
            keyspace=keyspace,
            finished_sim_metadata=pd.DataFrame(),
        )

    def test_backup_metadata_written_when_backups_enabled(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """The lookup table the workers read to resume is written up front."""
        mocker.patch(f"{_MODULE}.get_task_list", return_value=[MagicMock()])
        run = self._run(tmp_path)
        build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=1800.0,
            extra_args={},
        )
        assert run.output_paths.backup_metadata_path.exists()

    def test_no_backup_metadata_when_backups_disabled(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """With backups off there is nothing for a worker to look up."""
        mocker.patch(f"{_MODULE}.get_task_list", return_value=[MagicMock()])
        run = self._run(tmp_path)
        build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=None,
            extra_args={},
        )
        assert not run.output_paths.backup_metadata_path.exists()

    def test_max_attempts_reaches_the_tasks(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A caller's attempt cap is applied per task, not silently defaulted."""
        get_task_list = mocker.patch(f"{_MODULE}.get_task_list", return_value=[MagicMock()])
        build_simulation_tasks(
            MagicMock(),
            self._run(tmp_path),
            native_specification=MagicMock(),
            backup_freq=None,
            extra_args={},
            max_attempts=7,
        )
        assert get_task_list.call_args.kwargs["max_attempts"] == 7

    def test_workers_run_the_persisted_model_spec(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Tasks point at the run's resolved spec, not the raw input file."""
        get_task_list = mocker.patch(f"{_MODULE}.get_task_list", return_value=[MagicMock()])
        run = self._run(tmp_path)
        build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=None,
            extra_args={},
        )
        job_parameters = get_task_list.call_args.kwargs["job_parameters_list"]
        assert all(
            jp.model_specification == str(run.output_paths.model_specification)
            for jp in job_parameters
        )

    def test_no_tasks_when_every_job_is_complete(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A fully finished keyspace produces no tasks and no Jobmon call."""
        get_task_list = mocker.patch(f"{_MODULE}.get_task_list")
        mocker.patch(f"{_MODULE}.jobs.build_job_list", return_value=([], 4))
        run = self._run(tmp_path)._replace(finished_sim_metadata=pd.DataFrame(index=range(4)))
        sim_tasks = build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=None,
            extra_args={},
        )
        assert sim_tasks.tasks == []
        assert sim_tasks.num_jobs_completed == 4
        get_task_list.assert_not_called()

    def test_restart_counts_completed_from_collected_metadata(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A restart reports the real completed count, not the filtered one."""
        mocker.patch(f"{_MODULE}.get_task_list", return_value=[MagicMock()])
        mocker.patch(
            f"{_MODULE}.jobs.build_job_list",
            return_value=([make_job_parameters()], 0),
        )
        run = self._run(tmp_path, command=COMMANDS.restart)._replace(
            finished_sim_metadata=pd.DataFrame(index=range(3))
        )
        sim_tasks = build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=None,
            extra_args={},
        )
        assert sim_tasks.num_jobs_completed == 3


def test_report_initial_status() -> None:
    number_existing_jobs = 10
    finished_sim_metadata = pd.DataFrame(index=range(number_existing_jobs))
    report_initial_status(number_existing_jobs, finished_sim_metadata, 100)
    with pytest.raises(RuntimeError, match="There are 1 jobs from the previous run"):
        report_initial_status(number_existing_jobs + 1, finished_sim_metadata, 100)


def test_inconsistent_prior_run_is_rejected_before_anything_is_written(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """The consistency check runs before the backup table and task metadata.

    Otherwise an aborted run leaves task metadata and appended backup rows
    behind for a directory it just refused to use.
    """
    get_task_list = mocker.patch(f"{_MODULE}.get_task_list")
    mocker.patch(f"{_MODULE}.jobs.build_job_list", return_value=([make_job_parameters()], 4))
    run = TestBuildSimulationTasks._run(tmp_path)._replace(
        finished_sim_metadata=pd.DataFrame(index=range(2))
    )
    with pytest.raises(RuntimeError, match="jobs from the previous run"):
        build_simulation_tasks(
            MagicMock(),
            run,
            native_specification=MagicMock(),
            backup_freq=1800.0,
            extra_args={},
        )
    assert not run.output_paths.backup_metadata_path.exists()
    get_task_list.assert_not_called()


def test_write_backup_metadata(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.csv"
    job_parameters_list = [
        JobParameters(
            model_specification="test_model_spec.yaml",
            branch_configuration={"category": {"detail": 9}},
            input_draw=1337,
            random_seed=42,
            results_path="~/tmp",
            worker_logging_root="/tmp/worker_logs",
            backup_configuration=BackupConfiguration(
                backup_dir="", backup_freq=None, backup_metadata_path=""
            ),
            extras={},
        ),
    ]
    expected_task_id_1 = generate_task_id(1337, 42, {"category": {"detail": 9}})
    write_backup_metadata(metadata_path, job_parameters_list)
    assert metadata_path.exists()
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {
            "input_draw": [1337],
            "random_seed": [42],
            "job_id": [expected_task_id_1],
            "category.detail": [9],
        }
    )
    assert_frame_equal(metadata, expected_df)

    # Check that we append to the existing metadata
    # upon second execution
    append_job_parameters_list = [
        JobParameters(
            model_specification="test_model_spec.yaml",
            branch_configuration={"category": {"detail": 10}},
            input_draw=1338,
            random_seed=43,
            results_path="~/tmp",
            worker_logging_root="/tmp/worker_logs",
            backup_configuration=BackupConfiguration(
                backup_dir="", backup_freq=None, backup_metadata_path=""
            ),
            extras={},
        ),
    ]
    expected_task_id_2 = generate_task_id(1338, 43, {"category": {"detail": 10}})
    write_backup_metadata(metadata_path, append_job_parameters_list)
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {
            "input_draw": [1337, 1338],
            "random_seed": [42, 43],
            "job_id": [expected_task_id_1, expected_task_id_2],
            "category.detail": [9, 10],
        }
    )
    assert_frame_equal(metadata, expected_df)
