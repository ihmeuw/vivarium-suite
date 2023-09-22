import pytest

from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import setup_sim


def test_setup_sim(mocker):
    mocker.patch(
        "vivarium_cluster_tools.psimulate.worker.vivarium_work_horse.get_current_job",
        return_value=mocker.Mock(id="test_string"),
    )

    job_parameters = JobParameters(
        model_specification=None,
        branch_configuration={
            "input_data": {"artifact_path": "~/vivarium.yaml"},
            "time": {"end": {"year": 2020}},
        },
        input_draw=1,
        random_seed=2,
        results_path="~/tmp",
        extras={},
    )
    sim_config = setup_sim(job_parameters).configuration

    assert sim_config["time"]["end"]["year"] == 2020
    assert sim_config["input_data"]["artifact_path"] == "~/vivarium.yaml"
