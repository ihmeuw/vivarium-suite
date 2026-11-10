"""Track C real slice: MNCNH's own loaders as steps, a Nigeria artifact, and its V&V notebook.

Run ``dagger`` from an MNCNH checkout so ``env="vivarium_gates_mncnh_artifact"`` resolves to
that checkout's ``.venv/vivarium_gates_mncnh_artifact``. The pipeline's own env must import
``vivarium_gates_mncnh``, because a ``python_step`` runs in the pipeline's process.

Functions for ``-c mncnh_slice.py:<function>``:

- ``pipeline``: the public API only, so every step is always stale (MIC-7760).
- ``stable``: the same steps with the harness's ``stable_code_id`` stand-in.
- ``bad_key``: the IFA step names a handoff file that is not declared.
- ``bad_handoff``: the handoff root whose IFA file has a renamed column.
"""

import dataclasses
import os
from pathlib import Path

import pandas as pd
from vivarium.artifact import Artifact
from vivarium_gates_mncnh.constants import data_keys
from vivarium_gates_mncnh.data import loader

from vivarium.dagger.backend.task import Task
from vivarium.dagger.frontend import (
    ArtifactRegistry,
    Pipeline,
    handoff_inputs,
    handoff_key,
    notebook_step,
    python_step,
)
from vivarium.dagger.harness import stable_code_id

SLICE_ROOT = Path(
    os.environ.get("DAGGER_SLICE_ROOT", "/ihme/homes/pnast/scratch/dagger-evidence/slice")
)
LOCATION = "Nigeria"
PRIMARY_DATA = {
    "ifa_coverage.csv": ["location_id", "year_id", "age_group_id", "sex_id", "draw_0"]
}
NOTEBOOK_ENV = "vivarium_gates_mncnh_artifact"


def postpartum_fraction() -> pd.DataFrame:
    """Load Nigeria's postpartum fraction of maternal hemorrhage with the model's loader."""
    return loader.get_data(data_keys.MATERNAL_HEMORRHAGE.POSTPARTUM_FRACTION, LOCATION)


def demography() -> pd.DataFrame:
    """Load Nigeria's demographic dimensions with the model's loader."""
    return loader.get_data(data_keys.POPULATION.DEMOGRAPHY, LOCATION)


def ifa_coverage(extract: Path) -> pd.DataFrame:
    """Load IFA coverage with the model's loader, reading the handoff file."""
    return loader.load_coverage_from_file(extract, LOCATION)


def write_artifact(
    postpartum_fraction: Path, demography: Path, ifa_coverage: Path, artifact: Path
) -> None:
    """Write the three loaded keys into a fresh artifact, as ``make_artifacts`` would."""
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.unlink(missing_ok=True)
    hdf = Artifact(artifact)
    hdf.write(data_keys.METADATA_LOCATIONS, [LOCATION])
    hdf.write(data_keys.POPULATION.DEMOGRAPHY, pd.read_parquet(demography))
    hdf.write(
        data_keys.MATERNAL_HEMORRHAGE.POSTPARTUM_FRACTION,
        pd.read_parquet(postpartum_fraction),
    )
    hdf.write(data_keys.IFA_SUPPLEMENTATION.COVERAGE, pd.read_parquet(ifa_coverage))


def _tasks(
    handoff_root: Path, extract_file: str = "ifa_coverage.csv"
) -> tuple[list[Task], ArtifactRegistry]:
    """Compile the slice's steps against one handoff root."""
    registry = ArtifactRegistry(
        SLICE_ROOT / "output",
        run_name="nigeria",
        root_inputs=handoff_inputs(handoff_root, [LOCATION], PRIMARY_DATA),
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
            inputs={"extract": handoff_key(LOCATION, extract_file)},
            produces="ifa_coverage.parquet",
            registry=registry,
            name="ifa_coverage",
        ),
    ]
    # No frontend step writes an HDF yet (command_step is MIC-7740), so this one is a
    # hand-built backend Task with a callable run.
    inputs = {
        "postpartum_fraction": "postpartum_fraction.parquet",
        "demography": "demography.parquet",
        "ifa_coverage": "ifa_coverage.parquet",
    }
    tasks.append(
        Task(
            name="artifact",
            run=write_artifact,
            inputs={param: registry.resolve_input(key) for param, key in inputs.items()},
            outputs={"artifact": registry.register_output("artifacts/nigeria.hdf")},
            resources={},
            env=None,
        )
    )
    tasks.append(
        notebook_step(
            SLICE_ROOT / "notebooks" / "hemorrhage_split_proportions.ipynb",
            executed="notebooks/hemorrhage_split_proportions_executed.ipynb",
            registry=registry,
            env=NOTEBOOK_ENV,
            inputs={"artifact": "artifacts/nigeria.hdf"},
            name="split_proportions_notebook",
        )
    )
    return tasks, registry


def pipeline() -> Pipeline:
    """Return the slice built with the public API only."""
    tasks, registry = _tasks(SLICE_ROOT / "handoff")
    return Pipeline(tasks, SLICE_ROOT / "state", registry)


def stable() -> Pipeline:
    """Return the slice with the harness's code-fingerprint stand-in on every step."""
    tasks, registry = _tasks(SLICE_ROOT / "handoff")
    tasks = [dataclasses.replace(t, code_id=stable_code_id(f"{t.name}::code")) for t in tasks]
    return Pipeline(tasks, SLICE_ROOT / "state_stable", registry)


def bad_key() -> Pipeline:
    """Return the slice with the IFA step naming a misspelled handoff file."""
    tasks, registry = _tasks(SLICE_ROOT / "handoff", extract_file="ifa_coverge.csv")
    return Pipeline(tasks, SLICE_ROOT / "state_bad", registry)


def bad_handoff() -> Pipeline:
    """Return the slice reading a handoff whose IFA file renamed ``year_id`` to ``year``."""
    tasks, registry = _tasks(SLICE_ROOT / "handoff_bad")
    return Pipeline(tasks, SLICE_ROOT / "state_bad", registry)
