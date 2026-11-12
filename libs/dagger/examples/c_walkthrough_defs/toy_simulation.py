"""Track C simulation definition: a toy artifact, a toy psimulate run on it, and a summary.

Run ``dagger`` from a directory whose ``.venv/simulation`` is an env with ``psimulate`` and
Jobmon, on a Slurm compute node; psimulate refuses other hosts.
"""

from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import (
    ArtifactRegistry,
    Pipeline,
    command_step,
    python_step,
    simulation_step,
)

HERE = Path(__file__).resolve().parent
ENV = "simulation"


def summarize_deaths(simulation: Path) -> pd.DataFrame:
    """Sum the deaths of every simulation by input draw."""
    deaths = pd.read_parquet(simulation / "results" / "dead")
    return deaths.groupby("input_draw")["value"].sum().reset_index()


def pipeline() -> Pipeline:
    """Wire the artifact, the simulation and its summary."""
    registry = ArtifactRegistry(
        HERE / "output",
        run_name="toy",
        root_inputs={"make_toy_artifact.py": HERE / "make_toy_artifact.py"},
    )
    tasks = [
        command_step(
            "python {script} {artifact}",
            inputs={"script": "make_toy_artifact.py"},
            produces={"artifact": "artifacts/toy.hdf"},
            registry=registry,
            env=ENV,
            name="artifact",
        ),
        simulation_step(
            spec=HERE / "toy_model.yaml",
            branches=HERE / "branches.yaml",
            artifact="artifacts/toy.hdf",
            produces="sims/toy",
            registry=registry,
            env=ENV,
            options={
                "project": "proj_simscience",
                "max_runtime": "00:10:00",
                "peak_memory": 1,
                "no_slack": True,
            },
        ),
        python_step(
            summarize_deaths,
            inputs={"simulation": "sims/toy"},
            produces="summary.csv",
            registry=registry,
            name="summary",
        ),
    ]
    return Pipeline(tasks, HERE / "state", registry)
