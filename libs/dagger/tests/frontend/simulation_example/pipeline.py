"""An example pipeline definition with a simulation step: a toy psimulate run and a summary."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, python_step, simulation_step

HERE = Path(__file__).resolve().parent

# The environment psimulate runs in. A model names its own environment here; this example
# uses the environment dagger runs in, so that it needs no other one.
ENV = sys.prefix


def summarize_deaths(simulation: Path) -> pd.DataFrame:
    """Sum the deaths of every simulation by input draw."""
    results = sorted((simulation / "results" / "dead").glob("*.parquet"))
    frame = pd.concat([pd.read_parquet(path) for path in results], ignore_index=True)
    return frame.groupby("input_draw")["value"].sum().reset_index()


def pipeline() -> Pipeline:
    """Wire the simulation and its summary, and keep their outputs and state next to this file."""
    registry = ArtifactRegistry(HERE / "output", run_name="example")
    tasks = [
        simulation_step(
            spec=HERE / "toy_model.yaml",
            branches=HERE / "branches.yaml",
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
