from __future__ import annotations

from enum import Enum
from typing import NamedTuple

DRAW_PREFIX = "draw_"

DRAW_INDEX = "input_draw"
SEED_INDEX = "random_seed"


class DataSource(Enum):
    SIM = "sim"
    GBD = "gbd"
    ARTIFACT = "artifact"
    CUSTOM = "custom"

    @classmethod
    def from_str(cls, source: str) -> DataSource:
        try:
            return cls(source)
        except ValueError:
            raise ValueError(f"Source {source} not recognized. Must be one of {DataSource}")


LOCATION_ARTIFACT_KEY = "population.location"
POPULATION_STRUCTURE_ARTIFACT_KEY = "population.structure"


class InputDataIndexNames(NamedTuple):
    LOCATION_ID: str = "location_id"
    SEX_ID: str = "sex_id"
    AGE_GROUP_ID: str = "age_group_id"
    YEAR_ID: str = "year_id"
    PARAMETER: str = "parameter"
    CAUSE_ID: str = "cause_id"
    AFFECTED_ENTITY: str = "affected_entity"
    AFFECTED_MEASURE: str = "affected_measure"
    LOCATION: str = "location"
    SEX: str = "sex"
    AGE_GROUP: str = "age_group"
    AGE_START: str = "age_start"
    AGE_END: str = "age_end"
    YEAR_START: str = "year_start"
    YEAR_END: str = "year_end"
    ENTITY: str = "entity"
    MEASURE: str = "measure"


INPUT_DATA_INDEX_NAMES = InputDataIndexNames()
