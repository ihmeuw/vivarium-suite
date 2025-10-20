"""Loads, standardizes and validates input data for the simulation.

Abstract the extract and transform pieces of the artifact ETL.
The intent here is to provide a uniform interface around this portion
of artifact creation. The value of this interface shows up when more
complicated data needs are part of the project. See the BEP project
for an example.

`BEP <https://github.com/ihmeuw/vivarium_gates_bep/blob/master/src/vivarium_gates_bep/data/loader.py>`_

.. admonition::

   No logging is done here. Logging is done in vivarium inputs itself and forwarded.
"""
import numpy as np
import pandas as pd
from gbd_mapping import causes, covariates, risk_factors
from vivarium.framework.artifact import EntityKey
from vivarium_gbd_access import gbd
from vivarium_inputs import globals as vi_globals
from vivarium_inputs import interface
from vivarium_inputs import utilities as vi_utils
from vivarium_inputs import utility_data
from vivarium_inputs.mapping_extension import alternative_risk_factors

from vivarium_profiling.constants import data_keys


def get_data(
    lookup_key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    """Retrieves data from an appropriate source.

    Parameters
    ----------
    lookup_key
        The key that will eventually get put in the artifact with
        the requested data.
    location
        The location to get data for.

    Returns
    -------
        The requested data.

    """
    mapping = {
        # Population
        data_keys.POPULATION.LOCATION: load_population_location,
        data_keys.POPULATION.STRUCTURE: load_population_structure,
        data_keys.POPULATION.AGE_BINS: load_age_bins,
        data_keys.POPULATION.DEMOGRAPHY: load_demographic_dimensions,
        data_keys.POPULATION.TMRLE: load_theoretical_minimum_risk_life_expectancy,
        data_keys.POPULATION.ACMR: load_standard_data,
        data_keys.POPULATION.LIVE_BIRTH_RATE: load_standard_data,

        # Lower Respiratory Infections
        data_keys.LRI.PREVALENCE: load_standard_data,
        data_keys.LRI.INCIDENCE_RATE: load_standard_data,
        data_keys.LRI.REMISSION_RATE: load_standard_data,
        data_keys.LRI.CSMR: load_standard_data,
        data_keys.LRI.EMR: load_standard_data,
        data_keys.LRI.DISABILITY_WEIGHT: load_standard_data,
        data_keys.LRI.RESTRICTIONS: load_metadata,

        # Lower Respiratory Infections 2
        data_keys.LRI2.PREVALENCE: load_standard_data_duplicate_key,
        data_keys.LRI2.INCIDENCE_RATE: load_standard_data_duplicate_key,
        data_keys.LRI2.REMISSION_RATE: load_standard_data_duplicate_key,
        data_keys.LRI2.CSMR: load_standard_data_duplicate_key,
        data_keys.LRI2.EMR: load_standard_data_duplicate_key,
        data_keys.LRI2.DISABILITY_WEIGHT: load_standard_data_duplicate_key,
        data_keys.LRI2.RESTRICTIONS: load_metadata_duplicate_key,

        # Lower Respiratory Infections 3
        data_keys.LRI3.PREVALENCE: load_standard_data_duplicate_key,
        data_keys.LRI3.INCIDENCE_RATE: load_standard_data_duplicate_key,
        data_keys.LRI3.REMISSION_RATE: load_standard_data_duplicate_key,
        data_keys.LRI3.CSMR: load_standard_data_duplicate_key,
        data_keys.LRI3.EMR: load_standard_data_duplicate_key,
        data_keys.LRI3.DISABILITY_WEIGHT: load_standard_data_duplicate_key,
        data_keys.LRI3.RESTRICTIONS: load_metadata_duplicate_key,

        # Lower Respiratory Infections 4
        data_keys.LRI4.PREVALENCE: load_standard_data_duplicate_key,
        data_keys.LRI4.INCIDENCE_RATE: load_standard_data_duplicate_key,
        data_keys.LRI4.REMISSION_RATE: load_standard_data_duplicate_key,
        data_keys.LRI4.CSMR: load_standard_data_duplicate_key,
        data_keys.LRI4.EMR: load_standard_data_duplicate_key,
        data_keys.LRI4.DISABILITY_WEIGHT: load_standard_data_duplicate_key,
        data_keys.LRI4.RESTRICTIONS: load_metadata_duplicate_key,

        # High systolic blood pressure
        data_keys.SBP.DISTRIBUTION: load_metadata,
        data_keys.SBP.EXPOSURE_MEAN: load_standard_data,
        data_keys.SBP.EXPOSURE_SD: load_standard_data,
        data_keys.SBP.EXPOSURE_WEIGHTS: load_standard_data,
        data_keys.SBP.RELATIVE_RISK: load_standard_data,
        data_keys.SBP.PAF: load_standard_data,
        data_keys.SBP.TMRED: load_metadata,
        data_keys.SBP.RELATIVE_RISK_SCALAR: load_metadata,

        # High systolic blood pressure 2 - 4
        #   copied manually from SBP above in artifact

        # Unsafe water source
        data_keys.WATER.DISTRIBUTION: load_metadata,
        data_keys.WATER.EXPOSURE: load_standard_data,
        data_keys.WATER.CATEGORIES: load_metadata,
        data_keys.WATER.RELATIVE_RISK: load_standard_data,
        data_keys.WATER.PAF: load_categorical_paf,
    }
    data = mapping[lookup_key](lookup_key, location, years)
    data = _modify_affected_entities(data, lookup_key)
    return data


def _modify_affected_entities(data: str | pd.DataFrame, key: str) -> str | pd.DataFrame:
    key = EntityKey(key)
    if (
        key.name in ["high_systolic_blood_pressure", "unsafe_water_source"]
        and key.measure in ["relative_risk", "population_attributable_fraction"]
        and key.name not in data.index.get_level_values("affected_entity")
    ):
        # Rename the first type of "affected_entity" to "high_systolic_blood_pressure"
        data = data.rename(
            index={
                data.index.get_level_values("affected_entity")[
                    0
                ]: "lower_respiratory_infections"
            }
        )
        # subset to only affected_entity == key.name
        data = data[
            data.index.get_level_values("affected_entity") == "lower_respiratory_infections"
        ]
    return data


def load_population_location(
    key: str, location: str, years: int | str | list[int] | None = None
) -> str:
    if key != data_keys.POPULATION.LOCATION:
        raise ValueError(f"Unrecognized key {key}")

    return location


def load_population_structure(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    return interface.get_population_structure(location, years)


def load_age_bins(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    return interface.get_age_bins()


def load_demographic_dimensions(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    return interface.get_demographic_dimensions(location, years)


def load_theoretical_minimum_risk_life_expectancy(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    return interface.get_theoretical_minimum_risk_life_expectancy()


def load_standard_data(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    key = EntityKey(key)
    entity = get_entity(key)
    return interface.get_measure(entity, key.measure, location, years).droplevel("location")


def load_standard_data_duplicate_key(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    """Strip off the _X"""
    key = key.replace(EntityKey(key).name, "lower_respiratory_infections")
    return load_standard_data(key, location, years)


def load_metadata(key: str, location: str, years: int | str | list[int] | None = None):
    key = EntityKey(key)
    entity = get_entity(key)
    entity_metadata = entity[key.measure]
    if hasattr(entity_metadata, "to_dict"):
        entity_metadata = entity_metadata.to_dict()
    return entity_metadata


def load_metadata_duplicate_key(
    key: str, location: str, years: int | str | list[int] | None = None
):
    """Strip off the _X"""
    key = key.replace(EntityKey(key).name, "lower_respiratory_infections")
    return load_metadata(key, location, years)


def load_categorical_paf(
    key: str, location: str, years: int | str | list[int] | None = None
) -> pd.DataFrame:
    try:
        risk = {
            data_keys.WATER.PAF: data_keys.WATER,
        }[key]
    except KeyError:
        raise ValueError(f"Unrecognized key {key}")

    distribution_type = get_data(risk.DISTRIBUTION, location)

    if distribution_type != "dichotomous" and "polytomous" not in distribution_type:
        raise NotImplementedError(
            f"Unrecognized distribution {distribution_type} for {risk.name}. Only dichotomous and "
            f"polytomous are recognized categorical distributions."
        )

    exp = get_data(risk.EXPOSURE, location)
    rr = get_data(risk.RELATIVE_RISK, location)

    # paf = (sum_categories(exp * rr) - 1) / sum_categories(exp * rr)
    sum_exp_x_rr = (
        (exp * rr)
        .groupby(list(set(rr.index.names) - {"parameter"}))
        .sum()
        .reset_index()
        .set_index(rr.index.names[:-1])
    )
    paf = (sum_exp_x_rr - 1) / sum_exp_x_rr
    return paf


def _load_em_from_meid(location, meid, measure):
    location_id = utility_data.get_location_id(location)
    data = gbd.get_modelable_entity_draws(meid, location_id)
    data = data[data.measure_id == vi_globals.MEASURES[measure]]
    data = vi_utils.normalize(data, fill_value=0)
    data = data.filter(vi_globals.DEMOGRAPHIC_COLUMNS + vi_globals.DRAW_COLUMNS)
    data = vi_utils.reshape(data)
    data = vi_utils.scrub_gbd_conventions(data, location)
    data = vi_utils.split_interval(data, interval_column="age", split_column_prefix="age")
    data = vi_utils.split_interval(data, interval_column="year", split_column_prefix="year")
    return vi_utils.sort_hierarchical_data(data).droplevel("location")


# TODO - add project-specific data functions here


def get_entity(key: str | EntityKey):
    # Map of entity types to their gbd mappings.
    type_map = {
        "cause": causes,
        "covariate": covariates,
        "risk_factor": risk_factors,
        "alternative_risk_factor": alternative_risk_factors,
    }
    key = EntityKey(key)
    return type_map[key.type][key.name]
