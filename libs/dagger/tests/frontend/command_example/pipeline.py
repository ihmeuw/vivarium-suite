"""An example pipeline definition with a command step between two Python steps."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, Workflow

HERE = Path(__file__).resolve().parent


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_csv(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def pipeline() -> Pipeline:
    """Add the three steps, with their outputs and state under ``output`` next to this file."""
    wf = Workflow(
        HERE / "output",
        run_name="example",
        root_inputs={"select_adults.py": HERE / "select_adults.py"},
    )
    wf.python_step(make_population, inputs={}, produces="population.csv", name="population")
    wf.command_step(
        "python {script} {population} {adults}",
        inputs={"script": "select_adults.py", "population": "population.csv"},
        produces={"adults": "adults.csv"},
        env=sys.prefix,
        name="adults",
    )
    wf.python_step(
        total_adults, inputs={"adults": "adults.csv"}, produces="summary.csv", name="summary"
    )
    return wf.pipeline()
