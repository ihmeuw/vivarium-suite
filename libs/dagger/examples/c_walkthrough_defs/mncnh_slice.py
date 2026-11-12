"""Track C real slice: MNCNH's own loaders as steps, a Nigeria artifact, and its V&V notebook.

Run ``dagger`` from an MNCNH checkout so ``env="vivarium_gates_mncnh_artifact"`` resolves to
that checkout's ``.venv/vivarium_gates_mncnh_artifact``. The pipeline's own env must import
``vivarium_gates_mncnh``, because a ``python_step`` runs in the pipeline's process. The
artifact step and the notebook step run in the artifact env, as ``make_artifacts`` does.

``-c mncnh_slice.py`` uses the public API only, so every step is always stale until
MIC-7760; ``-c mncnh_slice.py:stable`` gives every step the harness's ``stable_code_id``
stand-in.
"""

import os
from pathlib import Path

import pandas as pd
from vivarium_gates_mncnh.constants import data_keys
from vivarium_gates_mncnh.data import loader

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

SLICE_ROOT = Path(
    os.environ.get("DAGGER_SLICE_ROOT", "/ihme/homes/pnast/scratch/dagger-evidence/slice")
)
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
    """Load IFA coverage with the model's loader, reading the handoff file."""
    return loader.load_coverage_from_file(extract, LOCATION)


def _tasks() -> tuple[list[Task], ArtifactRegistry]:
    """Compile the slice's steps."""
    registry = ArtifactRegistry(
        SLICE_ROOT / "output",
        run_name="nigeria",
        root_inputs={
            **handoff_inputs(SLICE_ROOT / "handoff", [LOCATION], PRIMARY_DATA),
            "write_artifact.py": HERE / "write_artifact.py",
        },
    )
    tasks = [
        python_step(
            postpartum_fraction,
            inputs={},
            produces="postpartum_fraction.parquet",
            registry=registry,
            name="postpartum_fraction",
        ),
        python_step(
            demography,
            inputs={},
            produces="demography.parquet",
            registry=registry,
            name="demography",
        ),
        python_step(
            ifa_coverage,
            inputs={"extract": handoff_key(LOCATION, "ifa_coverage.csv")},
            produces="ifa_coverage.parquet",
            registry=registry,
            name="ifa_coverage",
        ),
        command_step(
            "python {script} {postpartum_fraction} {demography} {ifa_coverage} {artifact}",
            inputs={
                "script": "write_artifact.py",
                "postpartum_fraction": "postpartum_fraction.parquet",
                "demography": "demography.parquet",
                "ifa_coverage": "ifa_coverage.parquet",
            },
            produces={"artifact": "artifacts/nigeria.hdf"},
            registry=registry,
            env=ARTIFACT_ENV,
            name="artifact",
        ),
        notebook_step(
            SLICE_ROOT / "notebooks" / "hemorrhage_split_proportions.ipynb",
            executed="notebooks/hemorrhage_split_proportions_executed.ipynb",
            registry=registry,
            env=ARTIFACT_ENV,
            inputs={"artifact": "artifacts/nigeria.hdf"},
            name="split_proportions_notebook",
        ),
    ]
    return tasks, registry


def pipeline() -> Pipeline:
    """Return the slice built with the public API only."""
    tasks, registry = _tasks()
    return Pipeline(tasks, SLICE_ROOT / "state", registry)


def stable() -> Pipeline:
    """Return the slice with the harness's code-fingerprint stand-in on every step."""
    tasks, registry = _tasks()
    return Pipeline(
        [with_stable_code_id(t) for t in tasks], SLICE_ROOT / "state_stable", registry
    )
