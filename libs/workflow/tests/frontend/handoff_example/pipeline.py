"""An illustrative primary-data handoff: one location's coverage file, averaged by one step."""

from pathlib import Path

import pandas as pd

from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend import handoff_inputs, handoff_key, python_step

HANDOFF_ROOT = Path(__file__).parent / "handoff"
LOCATIONS = ["example_location"]
PRIMARY_DATA = {"coverage.csv": ["location_id", "year_id", "coverage"]}


def mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average one location's coverage over its years."""
    frame = pd.read_csv(coverage)
    return pd.DataFrame({"mean_coverage": [frame["coverage"].mean()]})


def build_tasks(output_root: Path, handoff_root: Path = HANDOFF_ROOT) -> list[Task]:
    """Validate the handoff and compile one mean-coverage step per location."""
    registry = ArtifactRegistry(
        output_root,
        run_name="example",
        root_inputs=handoff_inputs(handoff_root, LOCATIONS, PRIMARY_DATA),
    )
    return [
        python_step(
            mean_coverage,
            inputs={"coverage": handoff_key(location, "coverage.csv")},
            produces=f"{location}/mean_coverage.parquet",
            registry=registry,
        )
        for location in LOCATIONS
    ]
