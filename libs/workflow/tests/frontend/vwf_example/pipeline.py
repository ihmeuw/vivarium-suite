"""An example pipeline definition for the ``vwf`` command: three steps in a chain."""

from pathlib import Path

import pandas as pd

from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend import Pipeline, python_step

HERE = Path(__file__).resolve().parent


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def select_adults(population: Path) -> pd.DataFrame:
    """Keep the age groups of 15 and older."""
    frame = pd.read_parquet(population)
    return frame[frame["age_group"] != "0-14"].reset_index(drop=True)


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_parquet(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def pipeline() -> Pipeline:
    """Wire the three steps and keep their outputs and state next to this file."""
    registry = ArtifactRegistry(HERE / "output", run_name="example")
    tasks = [
        python_step(
            make_population,
            inputs={},
            produces="population.parquet",
            registry=registry,
            name="population",
        ),
        python_step(
            select_adults,
            inputs={"population": "population.parquet"},
            produces="adults.parquet",
            registry=registry,
            name="adults",
        ),
        python_step(
            total_adults,
            inputs={"adults": "adults.parquet"},
            produces="summary.csv",
            registry=registry,
            name="summary",
        ),
    ]
    return Pipeline(tasks, HERE / "state", registry)
