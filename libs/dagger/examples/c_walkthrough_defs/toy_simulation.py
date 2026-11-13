"""Track C simulation definition: a toy artifact, a toy psimulate run on it, and a summary.

Run ``dagger`` from a directory whose ``.venv/simulation`` is an env with ``psimulate`` and
Jobmon, on a Slurm compute node; psimulate refuses other hosts.
"""

from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, Workflow

HERE = Path(__file__).resolve().parent
ENV = "simulation"


def summarize_deaths(simulation: Path) -> pd.DataFrame:
    """Sum the deaths of every simulation by input draw."""
    deaths = pd.read_parquet(simulation / "results" / "dead")
    return deaths.groupby("input_draw")["value"].sum().reset_index()


def pipeline() -> Pipeline:
    """Wire the artifact, the simulation and its summary."""
    wf = Workflow(
        HERE / "output",
        run_name="toy",
        root_inputs={"make_toy_artifact.py": HERE / "make_toy_artifact.py"},
    )
    wf.command_step(
        "python {script} {artifact}",
        inputs={"script": "make_toy_artifact.py"},
        produces={"artifact": "artifacts/toy.hdf"},
        env=ENV,
        name="artifact",
    )
    wf.simulation_step(
        spec=HERE / "toy_model.yaml",
        branches=HERE / "branches.yaml",
        artifact="artifacts/toy.hdf",
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
