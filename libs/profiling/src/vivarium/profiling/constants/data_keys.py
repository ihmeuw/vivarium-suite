from typing import NamedTuple

from vivarium_public_health.utilities import TargetString

#############
# Data Keys #
#############

METADATA_LOCATIONS = "metadata.locations"


class __Population(NamedTuple):
    LOCATION: str = "population.location"
    STRUCTURE: str = "population.structure"
    AGE_BINS: str = "population.age_bins"
    DEMOGRAPHY: str = "population.demographic_dimensions"
    TMRLE: str = "population.theoretical_minimum_risk_life_expectancy"
    ACMR: str = "cause.all_causes.cause_specific_mortality_rate"
    LIVE_BIRTH_RATE: str = "covariate.live_births_by_sex.estimate"

    @property
    def name(self):
        return "population"

    @property
    def log_name(self):
        return "population"


POPULATION = __Population()


class __LRI(NamedTuple):

    # Keys that will be loaded into the artifact. must have a colon type declaration
    PREVALENCE: TargetString = TargetString("cause.lower_respiratory_infections.prevalence")
    INCIDENCE_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections.incidence_rate"
    )
    REMISSION_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections.remission_rate"
    )
    DISABILITY_WEIGHT: TargetString = TargetString(
        "cause.lower_respiratory_infections.disability_weight"
    )
    EMR: TargetString = TargetString(
        "cause.lower_respiratory_infections.excess_mortality_rate"
    )
    CSMR: TargetString = TargetString(
        "cause.lower_respiratory_infections.cause_specific_mortality_rate"
    )
    RESTRICTIONS: TargetString = TargetString(
        "cause.lower_respiratory_infections.restrictions"
    )

    @property
    def name(self):
        return "lower_respiratory_infections"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


LRI = __LRI()


class __LRI2(NamedTuple):

    # Keys that will be loaded into the artifact. must have a colon type declaration
    PREVALENCE: TargetString = TargetString("cause.lower_respiratory_infections_2.prevalence")
    INCIDENCE_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.incidence_rate"
    )
    REMISSION_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.remission_rate"
    )
    DISABILITY_WEIGHT: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.disability_weight"
    )
    EMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.excess_mortality_rate"
    )
    CSMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.cause_specific_mortality_rate"
    )
    RESTRICTIONS: TargetString = TargetString(
        "cause.lower_respiratory_infections_2.restrictions"
    )

    @property
    def name(self):
        return "lower_respiratory_infections_2"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


LRI2 = __LRI2()


class __LRI3(NamedTuple):

    # Keys that will be loaded into the artifact. must have a colon type declaration
    PREVALENCE: TargetString = TargetString("cause.lower_respiratory_infections_3.prevalence")
    INCIDENCE_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.incidence_rate"
    )
    REMISSION_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.remission_rate"
    )
    DISABILITY_WEIGHT: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.disability_weight"
    )
    EMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.excess_mortality_rate"
    )
    CSMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.cause_specific_mortality_rate"
    )
    RESTRICTIONS: TargetString = TargetString(
        "cause.lower_respiratory_infections_3.restrictions"
    )

    @property
    def name(self):
        return "lower_respiratory_infections_3"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


LRI3 = __LRI3()


class __LRI4(NamedTuple):

    # Keys that will be loaded into the artifact. must have a colon type declaration
    PREVALENCE: TargetString = TargetString("cause.lower_respiratory_infections_4.prevalence")
    INCIDENCE_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.incidence_rate"
    )
    REMISSION_RATE: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.remission_rate"
    )
    DISABILITY_WEIGHT: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.disability_weight"
    )
    EMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.excess_mortality_rate"
    )
    CSMR: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.cause_specific_mortality_rate"
    )
    RESTRICTIONS: TargetString = TargetString(
        "cause.lower_respiratory_infections_4.restrictions"
    )

    @property
    def name(self):
        return "lower_respiratory_infections_4"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


LRI4 = __LRI4()


class __SBP(NamedTuple):
    DISTRIBUTION: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.distribution"
    )
    EXPOSURE_MEAN: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.exposure"
    )
    EXPOSURE_SD: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.exposure_standard_deviation"
    )
    EXPOSURE_WEIGHTS: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.exposure_distribution_weights"
    )
    RELATIVE_RISK: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.relative_risk"
    )
    PAF: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.population_attributable_fraction"
    )
    TMRED: TargetString = TargetString("risk_factor.high_systolic_blood_pressure.tmred")
    RELATIVE_RISK_SCALAR: TargetString = TargetString(
        "risk_factor.high_systolic_blood_pressure.relative_risk_scalar"
    )

    @property
    def name(self):
        return "high_systolic_blood_pressure"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


SBP = __SBP()


class __Water(NamedTuple):
    DISTRIBUTION: TargetString = TargetString("risk_factor.unsafe_water_source.distribution")
    EXPOSURE: TargetString = TargetString("risk_factor.unsafe_water_source.exposure")
    CATEGORIES: TargetString = TargetString("risk_factor.unsafe_water_source.categories")
    RELATIVE_RISK: TargetString = TargetString("risk_factor.unsafe_water_source.relative_risk")
    PAF: TargetString = TargetString("risk_factor.unsafe_water_source.population_attributable_fraction")

    @property
    def name(self):
        return "unsafe_water_source"

    @property
    def log_name(self):
        return self.name.replace("_", " ")


WATER = __Water()


MAKE_ARTIFACT_KEY_GROUPS = [
    POPULATION,
    LRI,
    LRI2,
    LRI3,
    LRI4,
    SBP,
    WATER,
]
