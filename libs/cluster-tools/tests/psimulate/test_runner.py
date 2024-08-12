import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from vivarium_cluster_tools.psimulate.runner import (
    report_initial_status,
    write_backup_metadata,
)


def test_report_initial_status():
    number_existing_jobs = 10
    finished_sim_metadata = pd.DataFrame(index=range(number_existing_jobs))
    report_initial_status(number_existing_jobs, finished_sim_metadata, 100)
    with pytest.raises(RuntimeError, match="There are 1 jobs from the previous run"):
        report_initial_status(number_existing_jobs + 1, finished_sim_metadata, 100)


def test_write_backup_metadata(tmp_path):
    metadata_path = tmp_path / "metadata.csv"
    parameters_by_job = {
        "job": {
            "input_draw": 1337,
            "random_seed": 42,
            "branch_configuration": {"category": {"detail": 9}},
        }
    }
    write_backup_metadata(metadata_path, parameters_by_job)
    assert metadata_path.exists()
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {"input_draw": [1337], "random_seed": [42], "job_id": ["job"], "category.detail": [9]}
    )
    assert_frame_equal(metadata, expected_df)

    # Check that we append to the existing metadata
    # upon second execution
    append_parameters_by_job = {
        "job2": {
            "input_draw": 1338,
            "random_seed": 43,
            "branch_configuration": {"category": {"detail": 10}},
        }
    }
    write_backup_metadata(metadata_path, append_parameters_by_job)
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {
            "input_draw": [1337, 1338],
            "random_seed": [42, 43],
            "job_id": ["job", "job2"],
            "category.detail": [9, 10],
        }
    )
    assert_frame_equal(metadata, expected_df)
