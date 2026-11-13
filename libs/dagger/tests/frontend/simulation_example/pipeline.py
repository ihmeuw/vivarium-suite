"""An example pipeline definition with a simulation step: a toy psimulate run and a summary."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, Workflow

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
    """Add the simulation and its summary, with their outputs and state next to this file."""
    wf = Workflow(HERE / "output", run_name="example")
    wf.simulation_step(
        spec=HERE / "toy_model.yaml",
        branches=HERE / "branches.yaml",
        produces="sims/toy",
        env=ENV,
        options={
            "project": "proj_simscience",
            "max_runtime": "00:10:00",
            "peak_memory": 1,
            "no_slack": True,
        },
    )
    wf.python_step(
        summarize_deaths,
        inputs={"simulation": "sims/toy"},
        produces="summary.csv",
        name="summary",
    )
    return wf.pipeline()
