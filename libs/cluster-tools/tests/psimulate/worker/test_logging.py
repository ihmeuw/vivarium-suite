from pathlib import Path
from typing import Any

import pytest
from layered_config_tree.main import LayeredConfigTree
from vivarium import Component

from vivarium_cluster_tools.psimulate.jobs import JobParameters
from vivarium_cluster_tools.psimulate.worker.vivarium_work_horse import (
    ParallelSimulationContext as ParallelSimulationContext_,
)


@pytest.mark.parametrize("log_level", [0, 1, 2])
def test_logging_level(tmp_path, log_level) -> None:

    input_draw = 1
    random_seed = 2
    branch_configuration = {"branch_key": "branch_value"}
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
        extras={
            "sim_verbosity": log_level,
        },
    )

    class ParallelSimulationContext(ParallelSimulationContext_):
        """A subclass of ParallelSimulationContext for testing that makes the logging level accessible."""

        def __init__(
            self,
            model_specification: str | Path | LayeredConfigTree | None = None,
            components: list[Component] | dict[str, Any] | LayeredConfigTree | None = None,
            configuration: dict[str, Any] | LayeredConfigTree | None = None,
            plugin_configuration: dict[str, Any] | LayeredConfigTree | None = None,
            sim_name: str | None = None,
            logging_verbosity: int = 1,
        ):
            super().__init__()
            self._logging_level = logging_verbosity

    sim = ParallelSimulationContext(
        job_parameters.model_specification,
        configuration=job_parameters.sim_config,
        logging_verbosity=job_parameters.extras["sim_verbosity"],
    )
    assert sim._logging_level == log_level
