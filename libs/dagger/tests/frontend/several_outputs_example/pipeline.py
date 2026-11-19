"""An example pipeline definition with a Python step that writes two outputs."""

from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline

HERE = Path(__file__).resolve().parent


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def split_by_age(population: Path) -> dict[str, pd.DataFrame]:
    """Split the population into the children's and the adults' age groups."""
    frame = pd.read_parquet(population)
    is_child = frame["age_group"] == "0-14"
    return {
        "children": frame[is_child].reset_index(drop=True),
        "adults": frame[~is_child].reset_index(drop=True),
    }


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_parquet(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def pipeline() -> Pipeline:
    """Add the three steps, with their outputs and state under ``output`` next to this file."""
    p = Pipeline(HERE / "output", run_name="example")
    p.add_python_step(
        make_population, inputs={}, outputs="population.parquet", name="population"
    )
    p.add_python_step(
        split_by_age,
        inputs={"population": "population.parquet"},
        outputs={"children": "children.csv", "adults": "adults.parquet"},
        name="split",
    )
    p.add_python_step(
        total_adults,
        inputs={"adults": "adults.parquet"},
        outputs="summary.csv",
        name="summary",
    )
    return p
