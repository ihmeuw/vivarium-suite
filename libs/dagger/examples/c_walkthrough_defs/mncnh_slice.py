"""Track C real slice: MNCNH's own loaders as steps, a Nigeria artifact, and its V&V notebook.

Run ``dagger`` from an MNCNH checkout so ``env="vivarium_gates_mncnh_artifact"`` resolves to
that checkout's ``.venv/vivarium_gates_mncnh_artifact``. The pipeline's own env must import
``vivarium_gates_mncnh``, because a ``python_step`` runs with that env's Python: in the
``dagger`` process with ``--local``, and on a compute node through Jobmon. The artifact step
and the notebook step run in the artifact env, as ``make_artifacts`` does. Set
``DAGGER_SLICE_ROOT`` to the folder that holds the slice's handoff and outputs; the
workflow keeps its state under ``output/.dagger``.

``-c mncnh_slice.py`` uses the public API only, so every step is always stale until
MIC-7760; ``-c mncnh_slice.py:stable`` gives every step the harness's ``stable_code_id``
stand-in.
"""

import os
from pathlib import Path

import pandas as pd
from vivarium_gates_mncnh.constants import data_keys
from vivarium_gates_mncnh.data import loader

from vivarium.dagger.frontend import Pipeline, Workflow, handoff_inputs, handoff_key
from vivarium.dagger.harness import with_stable_code_id

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
    """Load IFA coverage with the model's loader, reading the handoff file."""
    return loader.load_coverage_from_file(extract, LOCATION)


def _workflow(state: Path | None = None) -> Workflow:
    """Add the slice's steps to a workflow."""
    wf = Workflow(
        SLICE_ROOT / "output",
        run_name="nigeria",
        root_inputs={
            **handoff_inputs(SLICE_ROOT / "handoff", [LOCATION], PRIMARY_DATA),
            "write_artifact.py": HERE / "write_artifact.py",
        },
        state=state,
        resources=SLICE_RESOURCES,
    )
    wf.python_step(
        postpartum_fraction,
        inputs={},
        produces="postpartum_fraction.parquet",
        name="postpartum_fraction",
    )
    wf.python_step(demography, inputs={}, produces="demography.parquet", name="demography")
    wf.python_step(
        ifa_coverage,
        inputs={"extract": handoff_key(LOCATION, "ifa_coverage.csv")},
        produces="ifa_coverage.parquet",
        name="ifa_coverage",
    )
    wf.command_step(
        "python {script} {postpartum_fraction} {demography} {ifa_coverage} {artifact}",
        inputs={
            "script": "write_artifact.py",
            "postpartum_fraction": "postpartum_fraction.parquet",
            "demography": "demography.parquet",
            "ifa_coverage": "ifa_coverage.parquet",
        },
        produces={"artifact": "artifacts/nigeria.hdf"},
        env=ARTIFACT_ENV,
        name="artifact",
    )
    wf.notebook_step(
        SLICE_ROOT / "notebooks" / "hemorrhage_split_proportions.ipynb",
        executed="notebooks/hemorrhage_split_proportions_executed.ipynb",
        env=ARTIFACT_ENV,
        inputs={"artifact": "artifacts/nigeria.hdf"},
        name="split_proportions_notebook",
    )
    return wf


def pipeline() -> Pipeline:
    """Return the slice built with the public API only."""
    return _workflow().pipeline()


def stable() -> Pipeline:
    """Return the slice with the harness's code-fingerprint stand-in on every step."""
    built = _workflow(state=SLICE_ROOT / "output" / ".dagger" / "nigeria-stable").pipeline()
    tasks = tuple(with_stable_code_id(task) for task in built.tasks)
    return Pipeline(tasks, built.root, built.registry, resources=built.resources)
