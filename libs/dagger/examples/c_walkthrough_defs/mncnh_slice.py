"""Track C real slice: MNCNH's own loaders as steps, a Nigeria artifact, and its V&V notebook.

Run ``dagger`` from an MNCNH checkout so ``env="vivarium_gates_mncnh_artifact"`` resolves to
that checkout's ``.venv/vivarium_gates_mncnh_artifact``. The pipeline's own env must import
``vivarium_gates_mncnh``, because a Python step runs with that env's Python: in the
``dagger`` process with ``--local``, and on a compute node through Jobmon. The artifact step
and the notebook step run in the artifact env, as ``make_artifacts`` does. Set
``DAGGER_SLICE_ROOT`` to the folder that holds the slice's location inputs and outputs; the
pipeline keeps its state under ``output/.dagger``.
"""

import os
from pathlib import Path

import pandas as pd
from vivarium_gates_mncnh.constants import data_keys
from vivarium_gates_mncnh.data import loader

from vivarium.dagger.frontend import Pipeline, location_inputs, location_key

SLICE_ROOT = Path(os.environ["DAGGER_SLICE_ROOT"])
SLICE_RESOURCES = {"memory": 8}
"""Compute resources for every step run through Jobmon; the model's loaders need more
memory than dagger's default. The project comes from the ``dagger`` command's ``-P``."""
LOCATION = "Nigeria"
PRIMARY_DATA = {
    "ifa_coverage.csv": ["location_id", "year_id", "age_group_id", "sex_id", "draw_0"]
}
HERE = Path(__file__).resolve().parent
ARTIFACT_ENV = "vivarium_gates_mncnh_artifact"


def postpartum_fraction() -> pd.DataFrame:
    """Load Nigeria's postpartum fraction of maternal hemorrhage with the model's loader."""
    return loader.get_data(data_keys.MATERNAL_HEMORRHAGE.POSTPARTUM_FRACTION, LOCATION)


def demography() -> pd.DataFrame:
    """Load Nigeria's demographic dimensions with the model's loader."""
    return loader.get_data(data_keys.POPULATION.DEMOGRAPHY, LOCATION)


def ifa_coverage(extract: Path) -> pd.DataFrame:
    """Load IFA coverage with the model's loader, reading its location input file."""
    return loader.load_coverage_from_file(extract, LOCATION)


def pipeline() -> Pipeline:
    """Return the Nigeria slice."""
    p = Pipeline(
        SLICE_ROOT / "output",
        run_name="nigeria",
        root_inputs={
            **location_inputs(SLICE_ROOT / "inputs", [LOCATION], PRIMARY_DATA),
            "write_artifact.py": HERE / "write_artifact.py",
        },
        resources=SLICE_RESOURCES,
    )
    p.add_python_step(
        postpartum_fraction,
        inputs={},
        outputs="postpartum_fraction.parquet",
        name="postpartum_fraction",
    )
    p.add_python_step(demography, inputs={}, outputs="demography.parquet", name="demography")
    p.add_python_step(
        ifa_coverage,
        inputs={"extract": location_key(LOCATION, "ifa_coverage.csv")},
        outputs="ifa_coverage.parquet",
        name="ifa_coverage",
    )
    p.add_command_step(
        "python {script} {postpartum_fraction} {demography} {ifa_coverage} {artifact}",
        inputs={
            "script": "write_artifact.py",
            "postpartum_fraction": "postpartum_fraction.parquet",
            "demography": "demography.parquet",
            "ifa_coverage": "ifa_coverage.parquet",
        },
        outputs={"artifact": "artifacts/nigeria.hdf"},
        env=ARTIFACT_ENV,
        name="artifact",
    )
    p.add_notebook_step(
        SLICE_ROOT / "notebooks" / "hemorrhage_split_proportions.ipynb",
        executed="notebooks/hemorrhage_split_proportions_executed.ipynb",
        env=ARTIFACT_ENV,
        inputs={"artifact": "artifacts/nigeria.hdf"},
        name="split_proportions_notebook",
    )
    return p
