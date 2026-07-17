"""vivarium.public_health

Components for modeling diseases, risks, and interventions with vivarium.

Part of the `vivarium-suite monorepo <https://github.com/ihmeuw/vivarium-suite>`_;
the previously-standalone ``ihmeuw/vivarium_public_health`` GitHub repository
has been archived. The import path changed from ``vivarium_public_health`` to
``vivarium.public_health``; update your imports accordingly.

"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-public-health")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.public_health.disease import (
    SI,
    SIR,
    SIS,
    DiseaseModel,
    DiseaseState,
    NeonatalSWC_with_incidence,
    NeonatalSWC_without_incidence,
    RecoveredState,
    RiskAttributableDisease,
    SIR_fixed_duration,
    SIS_fixed_duration,
    SusceptibleState,
    TransientDiseaseState,
)
from vivarium.public_health.plugins import CausesConfigurationParser
from vivarium.public_health.population import (
    BasePopulation,
    FertilityAgeSpecificRates,
    FertilityCrudeBirthRate,
    FertilityDeterministic,
    Mortality,
    ScaledPopulation,
)
from vivarium.public_health.results import (
    CategoricalCausalFactorObserver,
    CategoricalInterventionObserver,
    CategoricalRiskObserver,
    DisabilityObserver,
    DiseaseObserver,
    MortalityObserver,
    ResultsStratifier,
)
from vivarium.public_health.risks import (
    LBWSGRisk,
    LBWSGRiskEffect,
    NonLogLinearRiskEffect,
    Risk,
    RiskEffect,
)
from vivarium.public_health.treatment import AbsoluteShift, LinearScaleUp, TherapeuticInertia
