from pathlib import Path

import vivarium_profiling
from vivarium_profiling.constants import metadata

BASE_DIR = Path(vivarium_profiling.__file__).resolve().parent

ARTIFACT_ROOT = Path(
    f"/mnt/team/simulation_science/pub/models/{metadata.PROJECT_NAME}/artifacts/"
)
