from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import work_horse


@pytest.mark.parametrize("log_level", [0, 1, 2])
def test_logging_level(mocker: MockerFixture, tmp_path: Path, log_level: int) -> None:

    input_draw = 1
    random_seed = 2
    branch_configuration = {"branch_key": "branch_value"}
    job_parameters = JobParameters(
        model_specification="test_model_spec.yaml",
        branch_configuration=branch_configuration,
        input_draw=input_draw,
        random_seed=random_seed,
        results_path="~/tmp",
        backup_configuration={
            "backup_freq": 300,
            "backup_dir": tmp_path / "backups",
            "backup_metadata_path": tmp_path / "backups" / "backup_metadata.csv",
        },
        extras={
            "sim_verbosity": log_level,
        },
    )

    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.ENV_VARIABLES",
    )
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.get_current_job",
    )
    mocker.patch("vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.run_simulation")
    mocker.patch("vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.remove_backups")
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.get_sim_results"
    )
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.format_and_record_details"
    )
    with patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.ParallelSimulationContext"
    ) as mock_parallel_sim:
        work_horse(job_parameters.to_dict())
        mock_parallel_sim.assert_called_once_with(
            job_parameters.model_specification,
            configuration=job_parameters.sim_config,
            logging_verbosity=log_level,
        )
