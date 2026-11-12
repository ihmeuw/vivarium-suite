"""An example pipeline definition with a command step between two Python steps."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import ArtifactRegistry, Pipeline, command_step, python_step

HERE = Path(__file__).resolve().parent


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_csv(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def pipeline() -> Pipeline:
    """Wire the three steps and keep their outputs and state next to this file."""
    registry = ArtifactRegistry(
        HERE / "output",
        run_name="example",
        root_inputs={"select_adults.py": HERE / "select_adults.py"},
    )
    tasks = [
        python_step(
            make_population,
            inputs={},
            produces="population.csv",
            registry=registry,
            name="population",
        ),
        command_step(
            "python {script} {population} {adults}",
            inputs={"script": "select_adults.py", "population": "population.csv"},
            produces={"adults": "adults.csv"},
            registry=registry,
            env=sys.prefix,
            name="adults",
        ),
        python_step(
            total_adults,
            inputs={"adults": "adults.csv"},
            produces="summary.csv",
            registry=registry,
            name="summary",
        ),
    ]
    return Pipeline(tasks, HERE / "state", registry)
