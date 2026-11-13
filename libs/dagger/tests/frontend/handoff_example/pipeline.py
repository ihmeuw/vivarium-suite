"""An illustrative primary-data handoff: one location's coverage file, averaged by one step."""

from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, Workflow, handoff_inputs, handoff_key

HERE = Path(__file__).resolve().parent
HANDOFF_ROOT = HERE / "handoff"
LOCATIONS = ["example_location"]
PRIMARY_DATA = {"coverage.csv": ["location_id", "year_id", "coverage"]}


def mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average one location's coverage over its years."""
    frame = pd.read_csv(coverage)
    return pd.DataFrame({"mean_coverage": [frame["coverage"].mean()]})


def pipeline(
    output_root: Path = HERE / "output", handoff_root: Path = HANDOFF_ROOT
) -> Pipeline:
    """Validate the handoff and add one mean-coverage step per location."""
    wf = Workflow(
        output_root,
        run_name="example",
        root_inputs=handoff_inputs(handoff_root, LOCATIONS, PRIMARY_DATA),
    )
    for location in LOCATIONS:
        wf.python_step(
            mean_coverage,
            inputs={"coverage": handoff_key(location, "coverage.csv")},
            produces=f"{location}/mean_coverage.parquet",
        )
    return wf.pipeline()
