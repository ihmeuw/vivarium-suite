import dill
import pandas as pd
import pytest

from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext,
    add_manager_configurations,
    get_backup,
    parameter_update_format,
    remove_backups,
)


def test_add_manager_configurations(mocker):
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.get_current_job",
        return_value=mocker.Mock(id="test_string"),
    )
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.time",
        return_value=1337,
    )
    initial_job_parameters = JobParameters(
        model_specification=None,
        branch_configuration={
            "input_data": {"artifact_path": "~/vivarium.yaml"},
            "time": {"end": {"year": 2020}},
        },
        input_draw=1,
        random_seed=2,
        results_path="~/tmp",
        backup_configuration={},
        extras={},
    )

    initial_branch_config = initial_job_parameters.branch_configuration
    update_dict = parameter_update_format(initial_job_parameters)
    updated_job_parameters = add_manager_configurations(initial_job_parameters)
    sim = ParallelSimulationContext(
        updated_job_parameters.model_specification,
        configuration=updated_job_parameters.branch_configuration,
    )
    sim_config = sim.configuration.to_dict()
    updated_branch_config = updated_job_parameters.branch_configuration

    ## Check that for all nested key, value pairs in ref_dict,
    ## the test dict has that key with the same value.
    def compare_dicts(test_dict, ref_dict):
        for k, v in ref_dict.items():
            if isinstance(v, dict):
                compare_dicts(test_dict[k], v)
            else:
                assert test_dict[k] == v

    for test_dict in [sim_config, updated_branch_config]:
        for ref_dict in [initial_branch_config, update_dict]:
            compare_dicts(test_dict, ref_dict)


@pytest.mark.parametrize(
    "make_dir, has_metadata_file, has_backup, multiple_backups",
    [
        (False, False, False, False),
        (True, False, False, False),
        (True, True, False, False),
        (True, True, True, False),
        (True, True, True, True),
    ],
)
def test_get_backup(
    mocker, tmp_path, make_dir, has_metadata_file, has_backup, multiple_backups
) -> None:
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.get_current_job",
        return_value=mocker.Mock(id="current_job"),
    )
    input_draw = 1
    random_seed = 2
    branch_configuration = {"branch_key": "branch_value"}
    job_id = "prev_job"
    job_parameters = JobParameters(
        model_specification=None,
        branch_configuration=branch_configuration,
        input_draw=input_draw,
        random_seed=random_seed,
        results_path="~/tmp",
        backup_configuration={
            "backup_freq": 300,
            "backup_dir": tmp_path / "backups",
            "backup_metadata_path": tmp_path / "backups" / "backup_metadata.csv",
        },
        extras={},
    )
    if make_dir:
        (tmp_path / "backups").mkdir(exist_ok=False)
        if has_metadata_file:
            metadata_draw = input_draw if has_backup else 7
            metadata = pd.DataFrame(
                {
                    "input_draw": [metadata_draw],
                    "random_seed": [random_seed],
                    "job_id": job_id,
                    "branch_key": ["branch_value"],
                }
            )
            if multiple_backups:
                new_row = pd.DataFrame(
                    {
                        "input_draw": [input_draw],
                        "random_seed": [random_seed],
                        "job_id": "stale_job",
                        "branch_key": ["branch_value"],
                    }
                )
                metadata = pd.concat([metadata, new_row])
            metadata.to_csv(tmp_path / "backups" / "backup_metadata.csv", index=False)

    if make_dir and has_metadata_file and has_backup:
        if multiple_backups:
            stale_pickle_path = tmp_path / "backups" / "stale_job.pkl"
            stale_pickle = [9, 8, 7, 6, 5]
            with open(stale_pickle_path, "wb") as f:
                dill.dump(stale_pickle, f)
        pickle_path = tmp_path / "backups" / f"{job_id}.pkl"
        pickle = [1, 2, 3, 4, 5]
        with open(pickle_path, "wb") as f:
            dill.dump(pickle, f)
        backup = get_backup(job_parameters)
        assert backup == pickle
        assert not (tmp_path / "backups" / "stale_job.pkl").exists()
        assert not (tmp_path / "backups" / "prev_job.pkl").exists()
        assert (tmp_path / "backups" / "current_job.pkl").exists()

    else:
        backup = get_backup(job_parameters)
        assert not backup


def test_remove_backups(tmp_path) -> None:
    # Ensure deleting non-existent file does not raise an error
    remove_backups(tmp_path / "job_id.pkl")
    # touch a file
    (tmp_path / "job_id.pkl").touch()
    assert (tmp_path / "job_id.pkl").exists()
    # remove the file
    remove_backups(tmp_path / "job_id.pkl")
    assert not (tmp_path / "job_id.pkl").exists()
