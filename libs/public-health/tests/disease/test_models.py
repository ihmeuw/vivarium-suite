"""Tests for the disease-model factory functions in ``disease.models``.

These verify that the optional transition-rate parameters are threaded into the
right transitions, and that leaving every measure unset preserves the historical
behavior of loading it from its default artifact key. The non-rate measures
(prevalence, disability weight, excess mortality rate, birth prevalence, and
cause-specific mortality rate) are supplied through the configuration rather
than the factory, so the factories only default them to their artifact keys.
"""

import pandas as pd
import pytest

from vivarium.public_health.disease import models
from vivarium.public_health.disease.model import DiseaseModel
from vivarium.public_health.disease.state import (
    DiseaseState,
    RecoveredState,
    SusceptibleState,
)
from vivarium.public_health.disease.transition import RateTransition

CAUSE = "test_cause"


def _state(model: DiseaseModel, state_type: type):
    """Return the single state of the given exact type in the model."""
    states = [s for s in model.states if type(s) is state_type]
    assert len(states) == 1, f"expected exactly one {state_type.__name__}, got {len(states)}"
    return states[0]


def _rate_transition(state) -> RateTransition:
    """Return the single rate transition leaving the given state."""
    transitions = [t for t in state.transition_set if isinstance(t, RateTransition)]
    assert len(transitions) == 1, f"expected one RateTransition, got {len(transitions)}"
    return transitions[0]


####################
# Generic contract #
####################

# Every factory builds a DiseaseModel from a cause alone (plus a duration for the
# fixed-duration models). The second element holds each factory's minimal extra args.
# Ordered as the factories appear in disease.models and the disease-model concept docs.
ALL_FACTORIES = [
    (models.SI, {}),
    (models.SIR, {}),
    (models.SIS, {}),
    (models.SIS_fixed_duration, {"duration": "10"}),
    (models.SIR_fixed_duration, {"duration": "10"}),
    (models.NeonatalSWC_without_incidence, {}),
    (models.NeonatalSWC_with_incidence, {}),
]
_FACTORY_IDS = [factory.__name__ for factory, _ in ALL_FACTORIES]


@pytest.mark.parametrize("factory, extra_kwargs", ALL_FACTORIES, ids=_FACTORY_IDS)
def test_factory_builds_disease_model(factory, extra_kwargs):
    # Every factory builds a DiseaseModel with a susceptible and a with-condition state.
    model = factory(CAUSE, **extra_kwargs)

    assert isinstance(model, DiseaseModel)
    assert model.cause == CAUSE
    assert f"susceptible_to_{CAUSE}" in model.state_names
    assert CAUSE in model.state_names
    # State names are unique, with exactly one susceptible and one disease state.
    assert len(model.state_names) == len(set(model.state_names))
    assert isinstance(_state(model, SusceptibleState), SusceptibleState)
    assert isinstance(_state(model, DiseaseState), DiseaseState)


@pytest.mark.parametrize("factory, extra_kwargs", ALL_FACTORIES, ids=_FACTORY_IDS)
def test_factory_prevalence_and_csmr_defaults(factory, extra_kwargs):
    # The factory supplies neither prevalence nor CSMR, so prevalence defaults to
    # its artifact key and CSMR is left for the model to load from the artifact.
    # Either is overridable through the configuration's data_sources block.
    model = factory(CAUSE, **extra_kwargs)
    infected = _state(model, DiseaseState)

    assert infected._prevalence_source == f"cause.{CAUSE}.prevalence"
    assert model._csmr_source is None


############
# SI model #
############


def test_si_structure():
    model = models.SI(CAUSE)
    assert set(model.state_names) == {CAUSE, f"susceptible_to_{CAUSE}"}
    assert isinstance(_state(model, SusceptibleState), SusceptibleState)
    assert isinstance(_state(model, DiseaseState), DiseaseState)


def test_si_incidence_defaults_to_artifact_key():
    # The susceptible->diseased transition's rate defaults to the incidence artifact key.
    model = models.SI(CAUSE)
    healthy = _state(model, SusceptibleState)
    assert _rate_transition(healthy).transition_rate == f"cause.{CAUSE}.incidence_rate"


def test_si_incidence_override():
    # A supplied incidence rate is used on the susceptible->diseased transition.
    model = models.SI(CAUSE, incidence_rate=0.2)
    healthy = _state(model, SusceptibleState)
    assert _rate_transition(healthy).transition_rate == 0.2


#######################
# SIR and SIS models  #
#######################


def test_sir_has_recovered_state():
    model = models.SIR(CAUSE)
    assert set(model.state_names) == {
        CAUSE,
        f"susceptible_to_{CAUSE}",
        f"recovered_from_{CAUSE}",
    }
    assert isinstance(_state(model, RecoveredState), RecoveredState)


@pytest.mark.parametrize("factory", [models.SIR, models.SIS])
def test_remission_rate_default_and_override(factory):
    # Default: remission loads from the artifact remission key.
    default_model = factory(CAUSE)
    default_infected = _state(default_model, DiseaseState)
    assert (
        _rate_transition(default_infected).transition_rate == f"cause.{CAUSE}.remission_rate"
    )

    # Override: the supplied remission rate is used verbatim.
    override_model = factory(CAUSE, remission_rate=0.5)
    override_infected = _state(override_model, DiseaseState)
    assert _rate_transition(override_infected).transition_rate == 0.5


###########################
# Fixed-duration models   #
###########################


@pytest.mark.parametrize("factory", [models.SIS_fixed_duration, models.SIR_fixed_duration])
def test_fixed_duration_parses_duration(factory):
    # A fixed-duration model converts its duration string into a dwell time on
    # the infected state; the call should succeed and build a DiseaseState.
    model = factory(CAUSE, "10.5")
    infected = _state(model, DiseaseState)
    assert infected._dwell_time_source == pd.Timedelta(days=10, hours=12)


#####################
# Neonatal models   #
#####################


@pytest.mark.parametrize(
    "factory", [models.NeonatalSWC_without_incidence, models.NeonatalSWC_with_incidence]
)
def test_neonatal_birth_prevalence_defaults_to_artifact_key(factory):
    model = factory(CAUSE)
    with_condition = _state(model, DiseaseState)
    assert with_condition._birth_prevalence_source == f"cause.{CAUSE}.birth_prevalence"


def test_neonatal_without_incidence_has_no_incidence_transition():
    model = models.NeonatalSWC_without_incidence(CAUSE)
    healthy = _state(model, SusceptibleState)
    rate_transitions = [t for t in healthy.transition_set if isinstance(t, RateTransition)]
    assert rate_transitions == []


def test_neonatal_with_incidence_adds_incidence_transition():
    model = models.NeonatalSWC_with_incidence(CAUSE, incidence_rate=0.7)
    healthy = _state(model, SusceptibleState)
    assert _rate_transition(healthy).transition_rate == 0.7
