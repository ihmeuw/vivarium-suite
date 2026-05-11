from typing import NamedTuple

import pandas as pd

####################
# Project metadata #
####################

# Underscore form is intentional: this string is used to build paths on the
# shared cluster filesystem (e.g. /mnt/team/.../{PROJECT_NAME}/artifacts/).
# Changing it would break access to existing artifacts.
PROJECT_NAME = "vivarium_profiling"
CLUSTER_PROJECT = "proj_simscience_prod"

CLUSTER_QUEUE = "all.q"
MAKE_ARTIFACT_MEM = 10  # GB
MAKE_ARTIFACT_CPU = 1
MAKE_ARTIFACT_RUNTIME = "3:00:00"
MAKE_ARTIFACT_SLEEP = 10

LOCATIONS = [
    "Pakistan",
]

ARTIFACT_INDEX_COLUMNS = [
    "sex",
    "age_start",
    "age_end",
    "year_start",
    "year_end",
]

DRAW_COUNT = 1000
ARTIFACT_COLUMNS = pd.Index([f"draw_{i}" for i in range(DRAW_COUNT)])


class __Scenarios(NamedTuple):
    baseline: str = "baseline"
    # TODO - add scenarios here


SCENARIOS = __Scenarios()
