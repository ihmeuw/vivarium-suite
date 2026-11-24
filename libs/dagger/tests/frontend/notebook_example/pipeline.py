"""An example pipeline definition with a notebook step that reads a Python step's output."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent

# The environment the notebook runs in, which must have papermill and ipykernel. A model
# names its own environment here; this example uses the environment dagger runs in, so
# that it needs no other one.
ENV = sys.prefix


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def pipeline() -> Pipeline:
    """Add the two steps, with their outputs and state under ``output`` next to this file."""
    p = Pipeline(HERE / "output", run_name="example")
    p.add_python_step(make_population, inputs={}, outputs="population.csv", name="population")
    p.add_notebook_step(
        HERE / "summarize.ipynb",
        executed="executed/summarize.ipynb",
        env=ENV,
        inputs={"population": "population.csv"},
        outputs={"summary": "summary.csv"},
        parameters={"excluded_age_group": "0-14"},
        name="summary",
    )
    return p
