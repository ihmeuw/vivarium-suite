"""Illustrative location inputs: one location's coverage file, averaged by one step."""

from pathlib import Path

import pandas as pd

from vivarium.dagger.frontend import Pipeline, location_inputs, location_key

HERE = Path(__file__).resolve().parent
INPUTS_ROOT = HERE / "inputs"
LOCATIONS = ["example_location"]
PRIMARY_DATA = {"coverage.csv": ["location_id", "year_id", "coverage"]}


def mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average one location's coverage over its years."""
    frame = pd.read_csv(coverage)
    return pd.DataFrame({"mean_coverage": [frame["coverage"].mean()]})


def pipeline(
    output_root: Path = HERE / "output", inputs_root: Path = INPUTS_ROOT
) -> Pipeline:
    """Check the location inputs and add one mean-coverage step per location."""
    p = Pipeline(
        output_root,
        run_name="example",
        root_inputs=location_inputs(inputs_root, LOCATIONS, PRIMARY_DATA),
    )
    for location in LOCATIONS:
        p.add_python_step(
            mean_coverage,
            inputs={"coverage": location_key(location, "coverage.csv")},
            outputs=f"{location}/mean_coverage.parquet",
        )
    return p
