"""Track D walkthrough definition: two root inputs, three python steps and a command step."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent


def read_census(census: Path) -> pd.DataFrame:
    """Return the census counts by age group."""
    return pd.read_csv(census)[["age_group", "count"]]


def select_children(population: Path) -> pd.DataFrame:
    """Keep the age group 0-14."""
    frame = pd.read_csv(population)
    return frame[frame["age_group"] == "0-14"].reset_index(drop=True)


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_csv(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def pipeline() -> Pipeline:
    """Return the walkthrough pipeline, with its outputs and state under ``output`` next to this file."""
    p = Pipeline(
        HERE / "output",
        run_name="walk",
        root_inputs={
            "census.csv": HERE / "inputs" / "census.csv",
            "select_adults.py": HERE / "select_adults.py",
        },
    )
    p.add_python_step(
        read_census,
        inputs={"census": "census.csv"},
        outputs="population.csv",
        name="population",
    )
    p.add_python_step(
        select_children,
        inputs={"population": "population.csv"},
        outputs="children.csv",
        name="children",
    )
    p.add_command_step(
        "python {script} {population} {adults}",
        inputs={"script": "select_adults.py", "population": "population.csv"},
        outputs={"adults": "adults.csv"},
        env=sys.prefix,
        name="adults",
    )
    p.add_python_step(
        total_adults, inputs={"adults": "adults.csv"}, outputs="summary.csv", name="summary"
    )
    return p
