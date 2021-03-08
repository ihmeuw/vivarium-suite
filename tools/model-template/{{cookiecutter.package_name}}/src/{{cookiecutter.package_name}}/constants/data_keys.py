from typing import NamedTuple

from vivarium_public_health.utilities import TargetString


#############
# Data Keys #
#############

METADATA_LOCATIONS = 'metadata.locations'


class __Population(NamedTuple):
    LOCATION: str = 'population.location'
    STRUCTURE: str = 'population.structure'
    AGE_BINS: str = 'population.age_bins'
    DEMOGRAPHY: str = 'population.demographic_dimensions'
    TMRLE: str = 'population.theoretical_minimum_risk_life_expectancy'
    ACMR: str = 'cause.all_causes.cause_specific_mortality_rate'

    @property
    def name(self):
        return 'population'

    @property
    def log_name(self):
        return 'population'


POPULATION = __Population()


# TODO - sample key group used to identify keys in model
# For more information see the tutorial:
# https://vivarium-inputs.readthedocs.io/en/latest/tutorials/pulling_data.html#entity-measure-data
class __SomeDisease(NamedTuple):

    # Keys that will be loaded into the artifact. must have a colon type declaration
    SOME_DISEASE_PREVALENCE: TargetString = TargetString('cause.some_disease.prevalence')
    SOME_DISEASE_INCIDENCE_RATE: TargetString = TargetString('cause.some_disease.incidence_rate')
    SOME_DISEASE_REMISSION_RATE: TargetString = TargetString('cause.some_disease.remission_rate')
    DISABILITY_WEIGHT: TargetString = TargetString('cause.some_disease.disability_weight')
    EMR: TargetString = TargetString('cause.some_disease.excess_mortality_rate')
    CSMR: TargetString = TargetString('cause.some_disease.cause_specific_mortality_rate')
    RESTRICTIONS: TargetString = TargetString('cause.some_disease.restrictions')

    # Useful keys not for the artifact - distinguished by not using the colon type declaration
    RAW_DISEASE_PREVALENCE = TargetString('sequela.raw_disease.prevalence')
    RAW_DISEASE_INCIDENCE_RATE = TargetString('sequela.raw_disease.incidence_rate')

    @property
    def name(self):
        return 'some_disease'

    @property
    def log_name(self):
        return 'some disease'


SOME_DISEASE = __SomeDisease()

MAKE_ARTIFACT_KEY_GROUPS = [
    POPULATION,
    # TODO: list all key groups here
    # SOME_DISEASE
]
