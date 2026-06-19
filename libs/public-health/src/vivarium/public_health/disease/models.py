"""
===================
The Model Menagerie
===================

This module contains a collection of frequently used parameterizations of
disease models.

Each factory accepts optional data sources for the measures its model uses.
A data source may be a scalar, a :class:`pandas.DataFrame`, a callable, or an
artifact key string; when left as ``None`` the measure is loaded from the
artifact at its default key. Supplying scalars lets a model run without an
artifact.

"""

import pandas as pd

# Imported so DataInput's "Builder" forward reference resolves in the API docs.
from vivarium.engine.framework.engine import Builder  # noqa: F401
from vivarium.engine.types import DataInput

from vivarium.public_health.disease.model import DiseaseModel
from vivarium.public_health.disease.state import (
    DiseaseState,
    RecoveredState,
    SusceptibleState,
)


def SI(
    cause: str,
    incidence_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a Susceptible-Infected disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate.
    prevalence
        Source for the infected-state prevalence.
    disability_weight
        Source for the infected-state disability weight.
    excess_mortality_rate
        Source for the infected-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured SI disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(
        cause,
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)

    return DiseaseModel(
        cause,
        states=[healthy, infected],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def SIR(
    cause: str,
    incidence_rate: DataInput | None = None,
    remission_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a Susceptible-Infected-Recovered disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate.
    remission_rate
        Source for the infected-to-recovered remission rate.
    prevalence
        Source for the infected-state prevalence.
    disability_weight
        Source for the infected-state disability weight.
    excess_mortality_rate
        Source for the infected-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured SIR disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(
        cause,
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )
    recovered = RecoveredState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_rate_transition(recovered, transition_rate=remission_rate)

    return DiseaseModel(
        cause,
        states=[healthy, infected, recovered],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def SIS(
    cause: str,
    incidence_rate: DataInput | None = None,
    remission_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a Susceptible-Infected-Susceptible disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate.
    remission_rate
        Source for the infected-to-susceptible remission rate.
    prevalence
        Source for the infected-state prevalence.
    disability_weight
        Source for the infected-state disability weight.
    excess_mortality_rate
        Source for the infected-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured SIS disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(
        cause,
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_rate_transition(healthy, transition_rate=remission_rate)

    return DiseaseModel(
        cause,
        states=[healthy, infected],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def SIS_fixed_duration(
    cause: str,
    duration: str,
    incidence_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create an SIS disease model with a fixed infection duration.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    duration
        The duration of infection in days.
    incidence_rate
        Source for the susceptible-to-infected incidence rate.
    prevalence
        Source for the infected-state prevalence.
    disability_weight
        Source for the infected-state disability weight.
    excess_mortality_rate
        Source for the infected-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured SIS disease model with fixed duration.
    """
    duration = pd.Timedelta(days=float(duration) // 1, hours=(float(duration) % 1) * 24.0)

    healthy = SusceptibleState(cause)
    infected = DiseaseState(
        cause,
        dwell_time=duration,
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_dwell_time_transition(healthy)

    return DiseaseModel(
        cause,
        states=[healthy, infected],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def SIR_fixed_duration(
    cause: str,
    duration: str,
    incidence_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create an SIR disease model with a fixed infection duration.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    duration
        The duration of infection in days.
    incidence_rate
        Source for the susceptible-to-infected incidence rate.
    prevalence
        Source for the infected-state prevalence.
    disability_weight
        Source for the infected-state disability weight.
    excess_mortality_rate
        Source for the infected-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured SIR disease model with fixed duration.
    """
    duration = pd.Timedelta(days=float(duration) // 1, hours=(float(duration) % 1) * 24.0)

    healthy = SusceptibleState(cause)
    infected = DiseaseState(
        cause,
        dwell_time=duration,
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )
    recovered = RecoveredState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_dwell_time_transition(recovered)

    return DiseaseModel(
        cause,
        states=[healthy, infected, recovered],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def NeonatalSWC_without_incidence(
    cause: str,
    birth_prevalence: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a neonatal model with birth prevalence but no incidence.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    birth_prevalence
        Source for the birth prevalence. Defaults to the artifact key
        ``cause.{cause}.birth_prevalence``.
    prevalence
        Source for the with-condition-state prevalence.
    disability_weight
        Source for the with-condition-state disability weight.
    excess_mortality_rate
        Source for the with-condition-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured neonatal disease model without incidence.
    """
    healthy = SusceptibleState(cause)
    with_condition = DiseaseState(
        cause,
        birth_prevalence=(
            birth_prevalence
            if birth_prevalence is not None
            else f"cause.{cause}.birth_prevalence"
        ),
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )

    return DiseaseModel(
        cause,
        states=[healthy, with_condition],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )


def NeonatalSWC_with_incidence(
    cause: str,
    birth_prevalence: DataInput | None = None,
    incidence_rate: DataInput | None = None,
    prevalence: DataInput | None = None,
    disability_weight: DataInput | None = None,
    excess_mortality_rate: DataInput | None = None,
    cause_specific_mortality_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a neonatal model with both birth prevalence and incidence.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    birth_prevalence
        Source for the birth prevalence. Defaults to the artifact key
        ``cause.{cause}.birth_prevalence``.
    incidence_rate
        Source for the susceptible-to-with-condition incidence rate.
    prevalence
        Source for the with-condition-state prevalence.
    disability_weight
        Source for the with-condition-state disability weight.
    excess_mortality_rate
        Source for the with-condition-state excess mortality rate.
    cause_specific_mortality_rate
        Source for the cause-specific mortality rate.

    Returns
    -------
        A configured neonatal disease model with incidence.
    """
    healthy = SusceptibleState(cause)
    with_condition = DiseaseState(
        cause,
        birth_prevalence=(
            birth_prevalence
            if birth_prevalence is not None
            else f"cause.{cause}.birth_prevalence"
        ),
        prevalence=prevalence,
        disability_weight=disability_weight,
        excess_mortality_rate=excess_mortality_rate,
    )

    healthy.add_rate_transition(with_condition, transition_rate=incidence_rate)

    return DiseaseModel(
        cause,
        states=[healthy, with_condition],
        cause_specific_mortality_rate=cause_specific_mortality_rate,
    )
