"""Track C walkthrough definition: python steps, a handoff, a notebook step and a command step.

``-c pipeline.py`` uses the public API only, so every step is always stale until MIC-7760.
``-c pipeline.py:stable`` gives every step the harness's ``stable_code_id`` stand-in.
"""

import sys
from pathlib import Path

import pandas as pd

from vivarium.dagger.backend.task import Task
from vivarium.dagger.frontend import (
    ArtifactRegistry,
    Pipeline,
    command_step,
    handoff_inputs,
    handoff_key,
    notebook_step,
    python_step,
)
from vivarium.dagger.harness import with_stable_code_id

HERE = Path(__file__).resolve().parent
LOCATIONS = ["kenya", "nigeria"]
PRIMARY_DATA = {"coverage.csv": ["location_id", "year_id", "coverage"]}


def make_population() -> pd.DataFrame:
    """Return a small population table by age group."""
    return pd.DataFrame({"age_group": ["0-14", "15-64", "65+"], "count": [30, 55, 15]})


def select_adults(population: Path) -> pd.DataFrame:
    """Keep the age groups of 15 and older."""
    frame = pd.read_parquet(population)
    return frame[frame["age_group"] != "0-14"][["age_group", "count"]].reset_index(drop=True)


def total_adults(adults: Path) -> pd.DataFrame:
    """Sum the adult population."""
    frame = pd.read_parquet(adults)
    return pd.DataFrame({"adults": [frame["count"].sum()]})


def mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average one location's coverage over its years."""
    return pd.DataFrame({"mean_coverage": [pd.read_csv(coverage)["coverage"].mean()]})


def _tasks() -> tuple[list[Task], ArtifactRegistry]:
    """Compile the walkthrough's steps."""
    registry = ArtifactRegistry(
        HERE / "output",
        run_name="walk",
        root_inputs=handoff_inputs(HERE / "handoff", LOCATIONS, PRIMARY_DATA),
    )
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
        *(
            python_step(
                mean_coverage,
                inputs={"coverage": handoff_key(location, "coverage.csv")},
                produces=f"{location}/mean_coverage.parquet",
                registry=registry,
                name=f"coverage_{location}",
            )
            for location in LOCATIONS
        ),
        notebook_step(
            HERE / "report.ipynb",
            executed="report_executed.ipynb",
            registry=registry,
            env=sys.prefix,
            inputs={"summary": "summary.csv"},
            produces={"report": "report.csv"},
            parameters={"title": "Adults"},
            name="report",
        ),
        command_step(
            "cp {report} {copy}",
            inputs={"report": "report.csv"},
            produces={"copy": "archive/report.csv"},
            registry=registry,
            name="archive",
        ),
    ]
    return tasks, registry


def pipeline() -> Pipeline:
    """Return the walkthrough pipeline built with the public API only."""
    tasks, registry = _tasks()
    return Pipeline(tasks, HERE / "state", registry)


def stable() -> Pipeline:
    """Return the walkthrough pipeline with the code-fingerprint stand-in on every step."""
    tasks, registry = _tasks()
    return Pipeline([with_stable_code_id(t) for t in tasks], HERE / "state_stable", registry)
