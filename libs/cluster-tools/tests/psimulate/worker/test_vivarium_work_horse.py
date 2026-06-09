import json
from pathlib import Path
from time import time
from typing import cast

import dill
import pandas as pd
import pytest
from pytest_mock import MockerFixture

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate.branches import Keyspace
from vivarium.cluster_tools.psimulate.jobs import JobParameters, build_job_list
from vivarium.cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext,
    get_backup,
    get_sim_from_backup,
    remove_backups,
)


@pytest.mark.parametrize("backup_freq", [None, 300])
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
    backup_freq: int | None,
) -> None:
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
            "backup_dir": str(tmp_path / "backups"),
            "backup_metadata_path": str(tmp_path / "backups" / "backup_metadata.csv"),
        },
    )
    # Patch sleep so we can assert it is skipped on the no-backup rename path
    # and so passing rows do not actually wait 5 seconds.
    sleep_mock = mocker.patch(
        "vivarium.cluster_tools.psimulate.worker.vivarium_work_horse.sleep"
    )
    if make_dir:
        (tmp_path / "backups").mkdir(exist_ok=False)
        if has_metadata_file:
            metadata_draw = input_draw if has_backup else 7
            metadata = pd.DataFrame(
                [
                    {
                        "input_draw": metadata_draw,
                        "random_seed": random_seed,
                        "job_id": job_id,
                        "branch_key": "branch_value",
                    },
                    {
                        "input_draw": input_draw,
                        "random_seed": random_seed + 5,
                        "job_id": "different_job",
                        "branch_key": "branch_value",
                    },
                ]
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
        # Stale pickle cleanup happens outside the backup_freq gate.
        assert not (tmp_path / "backups" / "stale_job.pkl").exists()
        assert (tmp_path / "backups" / "different_job.pkl").exists()
        if backup_freq is None:
            # No rename when backups are disabled: the pickle keeps its original
            # filename and sleep is skipped.
            assert (tmp_path / "backups" / f"{job_id}.pkl").exists()
            assert not (tmp_path / "backups" / f"{job_parameters.task_id}.pkl").exists()
            sleep_mock.assert_not_called()
        else:
            assert not (tmp_path / "backups" / f"{job_id}.pkl").exists()
            assert (tmp_path / "backups" / f"{job_parameters.task_id}.pkl").exists()
            sleep_mock.assert_called_once()

    else:
        backup = cast(list[int], get_backup(job_parameters))
        assert not backup


def test_build_job_list_config_consumed_by_get_backup(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """End-to-end: a backup_configuration produced by build_job_list survives the
    production JSON round-trip (which turns the Path config values into strings)
    and is consumed by get_backup, which coerces them back with ``Path(...)`` to
    locate, load, and rename a matching backup pickle. This would catch a
    regression where someone drops the ``Path(...)`` coercion in the worker.
    """
    input_draw = 1
    random_seed = 2
    job_id = "prev_job"
    backup_dir = tmp_path / "backups"
    backup_metadata_path = backup_dir / "backup_metadata.csv"

    keyspace = Keyspace(
        branches=[{}],
        keyspace={"input_draw": [input_draw], "random_seed": [random_seed]},
    )
    jobs, _num_completed = build_job_list(
        model_specification_path=tmp_path / "model_spec.yaml",
        output_root=tmp_path / "results",
        keyspace=keyspace,
        finished_sim_metadata=pd.DataFrame(),
        backup_freq=300,
        backup_dir=backup_dir,
        backup_metadata_path=backup_metadata_path,
        worker_logging_root=tmp_path / "logs",
        extras={},
    )
    assert len(jobs) == 1

    # Round-trip through JSON exactly as production does before handing the job
    # to the worker: paths arrive at get_backup as strings, not Path objects.
    serialized = json.loads(json.dumps(jobs[0].to_dict(), default=str))
    job_parameters = JobParameters(**serialized)

    # Patch sleep so the rename path (backup_freq is not None) does not wait.
    mocker.patch("vivarium.cluster_tools.psimulate.worker.vivarium_work_horse.sleep")

    backup_dir.mkdir(exist_ok=False)
    metadata = pd.DataFrame(
        [
            {
                "input_draw": input_draw,
                "random_seed": random_seed,
                "job_id": job_id,
            }
        ]
    )
    metadata.to_csv(backup_metadata_path, index=False)

    correct_pickle = [1, 2, 3, 4, 5]
    with open((backup_dir / job_id).with_suffix(".pkl"), "wb") as f:
        dill.dump(correct_pickle, f)

    backup = cast(list[int], get_backup(job_parameters))
    assert backup == correct_pickle
    # backup_freq=300 -> get_backup renames the pickle to <task_id>.pkl.
    assert not (backup_dir / job_id).with_suffix(".pkl").exists()
    assert (backup_dir / job_parameters.task_id).with_suffix(".pkl").exists()


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
