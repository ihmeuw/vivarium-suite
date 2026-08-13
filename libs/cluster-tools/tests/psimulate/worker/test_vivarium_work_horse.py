import io
from pathlib import Path
from time import time
from typing import cast

import dill
import pandas as pd
import pytest
from pytest_mock import MockerFixture

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate.jobs import JobParameters
from vivarium.cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext,
    get_backup,
    get_sim_from_backup,
    remove_backups,
    work_horse,
)

_MODULE = "vivarium.cluster_tools.psimulate.worker.vivarium_work_horse"


@pytest.mark.parametrize(
    "make_dir, has_metadata_file, has_backup, multiple_backups, backup_freq",
    [
        (False, False, False, False, 300),
        (True, False, False, False, 300),
        (True, True, False, False, 300),
        (True, True, True, False, 300),
        (True, True, True, True, 300),
        # Skip backups
        (True, True, True, False, None),
    ],
)
def test_get_backup(
    tmp_path: Path,
    make_dir: bool,
    has_metadata_file: bool,
    has_backup: bool,
    multiple_backups: bool,
    backup_freq: int | None,
) -> None:
    task_id = "test_task_id"
    input_draw = 1
    random_seed = 2
    branch_configuration = {"branch_key": "branch_value"}
    job_id = "prev_job"
    job_parameters = make_job_parameters(
        model_specification="dummy",
        branch_configuration=branch_configuration,
        input_draw=input_draw,
        random_seed=random_seed,
        backup_configuration={
            "backup_freq": backup_freq,
            "backup_dir": tmp_path / "backups",
            "backup_metadata_path": tmp_path / "backups" / "backup_metadata.csv",
        },
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
        assert (tmp_path / "backups" / "different_job.pkl").exists()
        if backup_freq is not None:
            assert not (tmp_path / "backups" / f"{job_id}.pkl").exists()
            assert (tmp_path / "backups" / f"{job_parameters.task_id}.pkl").exists()
        else:
            # No rename occurs, so the original pickle stays and no log is emitted.
            assert (tmp_path / "backups" / f"{job_id}.pkl").exists()
            assert not (tmp_path / "backups" / f"{job_parameters.task_id}.pkl").exists()
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


def test_work_horse_does_not_report_its_own_failure(
    mocker: MockerFixture, captured_logs: io.StringIO
) -> None:
    """A failing simulation propagates out of the work horse unreported, leaving
    ``main()`` as the single place a task failure is logged."""
    job_parameters = make_job_parameters()
    mocker.patch(f"{_MODULE}.ENV_VARIABLES")
    mocker.patch(f"{_MODULE}.get_backup", return_value=None)
    mocker.patch(f"{_MODULE}.initialize_new_sim", side_effect=RuntimeError("sim-boom"))

    with pytest.raises(RuntimeError, match="sim-boom"):
        work_horse(job_parameters)

    # Non-empty because the work horse still logs its start and exit; an empty
    # buffer would make the assertion below pass for the wrong reason.
    assert captured_logs.getvalue() != ""
    assert "Unhandled exception in worker" not in captured_logs.getvalue()


def test_get_sim_from_backup() -> None:
    backup = ParallelSimulationContext()  # returned by get_backup
    event = {"start": time()}
    sim, exec_time = get_sim_from_backup(event, backup)
    assert isinstance(sim, ParallelSimulationContext)
    assert isinstance(exec_time, dict)
    assert "setup_minutes" in exec_time.keys()
    assert backup == sim
