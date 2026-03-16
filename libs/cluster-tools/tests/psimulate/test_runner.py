from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from vivarium_cluster_tools.psimulate.jobs import JobParameters, generate_task_id
from vivarium_cluster_tools.psimulate.runner import (
    report_initial_status,
    write_backup_metadata,
)


def test_report_initial_status() -> None:
    number_existing_jobs = 10
    finished_sim_metadata = pd.DataFrame(index=range(number_existing_jobs))
    report_initial_status(number_existing_jobs, finished_sim_metadata, 100)
    with pytest.raises(RuntimeError, match="There are 1 jobs from the previous run"):
        report_initial_status(number_existing_jobs + 1, finished_sim_metadata, 100)


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
            backup_configuration={},
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
            backup_configuration={},
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
