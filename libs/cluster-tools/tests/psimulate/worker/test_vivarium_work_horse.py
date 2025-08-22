from pathlib import Path
from time import time
from typing import cast

import dill
import pandas as pd
import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext,
    get_backup,
    get_sim_from_backup,
    remove_backups,
)


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
    mocker: MockerFixture,
    tmp_path: Path,
    make_dir: bool,
    has_metadata_file: bool,
    has_backup: bool,
    multiple_backups: bool,
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
        model_specification="dummy",
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
                },
                {
                    "input_draw": [input_draw],
                    "random_seed": [random_seed + 5],
                    "job_id": "different_job",
                    "branch_key": ["branch_value"],
                },
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

        def write_pickle(filename: str, pickle: list[int]) -> None:
            pickle_path = tmp_path / "backups" / f"{filename}.pkl"
            with open(pickle_path, "wb") as f:
                dill.dump(pickle, f)

        if multiple_backups:
            write_pickle("stale_job", [9, 8, 7, 6, 5])
        write_pickle("different_job", [6, 7, 8, 9, 10])
        correct_pickle = [1, 2, 3, 4, 5]
        write_pickle(job_id, correct_pickle)

        backup = cast(list[int], get_backup(job_parameters))
        assert backup == correct_pickle
        assert not (tmp_path / "backups" / "stale_job.pkl").exists()
        assert not (tmp_path / "backups" / f"{job_id}.pkl").exists()
        assert (tmp_path / "backups" / "current_job.pkl").exists()
        assert (tmp_path / "backups" / "different_job.pkl").exists()

    else:
        backup = cast(list[int], get_backup(job_parameters))
        assert not backup


def test_remove_backups(tmp_path: Path) -> None:
    # Ensure deleting non-existent file does not raise an error
    remove_backups(tmp_path / "job_id.pkl")
    # touch a file
    (tmp_path / "job_id.pkl").touch()
    assert (tmp_path / "job_id.pkl").exists()
    # remove the file
    remove_backups(tmp_path / "job_id.pkl")
    assert not (tmp_path / "job_id.pkl").exists()


def test_get_sim_from_backup() -> None:
    backup = ParallelSimulationContext()  # returned by get_backup
    event = {"start": time()}
    sim, exec_time = get_sim_from_backup(event, backup)
    assert isinstance(sim, ParallelSimulationContext)
    assert isinstance(exec_time, dict)
    assert "setup_minutes" in exec_time.keys()
    assert backup == sim
