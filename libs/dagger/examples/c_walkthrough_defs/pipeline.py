"""Track C walkthrough definition: python steps, one with two products, location inputs, a notebook step and a command step."""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, location_inputs, location_key

HERE = Path(__file__).resolve().parent
LOCATIONS = ["kenya", "nigeria"]
PRIMARY_DATA = {"coverage.csv": ["location_id", "year_id", "coverage"]}


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def split_by_age(population: Path) -> dict[str, pd.DataFrame]:
    """Split the population into the children's and the adults' age groups."""
    frame = pd.read_parquet(population)
    is_child = frame["age_group"] == "0-14"
    return {
        "children": frame[is_child][["age_group", "count"]].reset_index(drop=True),
        "adults": frame[~is_child][["age_group", "count"]].reset_index(drop=True),
    }


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_parquet(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average one location's coverage over its years."""
    return pd.DataFrame({"mean_coverage": [pd.read_csv(coverage)["coverage"].mean()]})


def pipeline() -> Pipeline:
    """Return the walkthrough pipeline."""
    p = Pipeline(
        HERE / "output",
        run_name="walk",
        root_inputs=location_inputs(HERE / "inputs", LOCATIONS, PRIMARY_DATA),
    )
    p.add_python_step(
        make_population, inputs={}, produces="population.parquet", name="population"
    )
    p.add_python_step(
        split_by_age,
        inputs={"population": "population.parquet"},
        produces={"children": "children.csv", "adults": "adults.parquet"},
        name="adults",
    )
    p.add_python_step(
        total_adults,
        inputs={"adults": "adults.parquet"},
        produces="summary.csv",
        name="summary",
    )
    for location in LOCATIONS:
        p.add_python_step(
            mean_coverage,
            inputs={"coverage": location_key(location, "coverage.csv")},
            produces=f"{location}/mean_coverage.parquet",
            name=f"coverage_{location}",
        )
    p.add_notebook_step(
        HERE / "report.ipynb",
        executed="report_executed.ipynb",
        env=sys.prefix,
        inputs={"summary": "summary.csv"},
        produces={"report": "report.csv"},
        parameters={"title": "Adults"},
        name="report",
    )
    p.add_command_step(
        "cp {report} {copy}",
        inputs={"report": "report.csv"},
        produces={"copy": "archive/report.csv"},
        name="archive",
    )
    return p
