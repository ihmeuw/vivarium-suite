"""
===================
The Model Menagerie
===================

This module contains a collection of frequently used parameterizations of
disease models.

Each factory accepts optional transition-rate data sources (incidence and,
where applicable, remission). A rate source may be a scalar, a
:class:`pandas.DataFrame`, a callable, or an artifact key string; when left as
``None`` the rate is loaded from the artifact at its default key. The
remaining measures - prevalence, disability weight, excess mortality rate,
birth prevalence, and cause-specific mortality rate - are supplied through the
simulation configuration's ``data_sources`` blocks (see the disease tutorial),
which is how a model is made artifactless.

"""

import pandas as pd

from vivarium.engine.framework.engine import Builder
from vivarium.engine.types import DataInput

from vivarium.public_health.disease.model import DiseaseModel
from vivarium.public_health.disease.state import (
    DiseaseState,
    RecoveredState,
    SusceptibleState,
)


def SI(cause: str, incidence_rate: DataInput | None = None) -> DiseaseModel:
    """Create a Susceptible-Infected disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate. When ``None``,
        loads from the artifact at ``cause.{cause}.incidence_rate``.

    Returns
    -------
        A configured SI disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)

    return DiseaseModel(cause, states=[healthy, infected])


def SIR(
    cause: str,
    incidence_rate: DataInput | None = None,
    remission_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a Susceptible-Infected-Recovered disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate. When ``None``,
        loads from the artifact at ``cause.{cause}.incidence_rate``.
    remission_rate
        Source for the infected-to-recovered remission rate. When ``None``,
        loads from the artifact at ``cause.{cause}.remission_rate``.

    Returns
    -------
        A configured SIR disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(cause)
    recovered = RecoveredState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_rate_transition(recovered, transition_rate=remission_rate)

    return DiseaseModel(cause, states=[healthy, infected, recovered])


def SIS(
    cause: str,
    incidence_rate: DataInput | None = None,
    remission_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a Susceptible-Infected-Susceptible disease model.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-infected incidence rate. When ``None``,
        loads from the artifact at ``cause.{cause}.incidence_rate``.
    remission_rate
        Source for the infected-to-susceptible remission rate. When ``None``,
        loads from the artifact at ``cause.{cause}.remission_rate``.

    Returns
    -------
        A configured SIS disease model.
    """
    healthy = SusceptibleState(cause)
    infected = DiseaseState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_rate_transition(healthy, transition_rate=remission_rate)

    return DiseaseModel(cause, states=[healthy, infected])


def SIS_fixed_duration(
    cause: str,
    duration: str,
    incidence_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create an SIS disease model with a fixed infection duration.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    duration
        The duration of infection in days.
    incidence_rate
        Source for the susceptible-to-infected incidence rate. When ``None``,
        loads from the artifact at ``cause.{cause}.incidence_rate``.

    Returns
    -------
        A configured SIS disease model with fixed duration.
    """
    duration = pd.Timedelta(days=float(duration) // 1, hours=(float(duration) % 1) * 24.0)

    healthy = SusceptibleState(cause)
    infected = DiseaseState(cause, dwell_time=duration)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_dwell_time_transition(healthy)

    return DiseaseModel(cause, states=[healthy, infected])


def SIR_fixed_duration(
    cause: str,
    duration: str,
    incidence_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create an SIR disease model with a fixed infection duration.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    duration
        The duration of infection in days.
    incidence_rate
        Source for the susceptible-to-infected incidence rate. When ``None``,
        loads from the artifact at ``cause.{cause}.incidence_rate``.

    Returns
    -------
        A configured SIR disease model with fixed duration.
    """
    duration = pd.Timedelta(days=float(duration) // 1, hours=(float(duration) % 1) * 24.0)

    healthy = SusceptibleState(cause)
    infected = DiseaseState(cause, dwell_time=duration)
    recovered = RecoveredState(cause)

    healthy.add_rate_transition(infected, transition_rate=incidence_rate)
    infected.add_dwell_time_transition(recovered)

    return DiseaseModel(cause, states=[healthy, infected, recovered])


def NeonatalSWC_without_incidence(cause: str) -> DiseaseModel:
    """Create a neonatal model with birth prevalence but no incidence.

    Parameters
    ----------
    cause
        The name of the cause of disease.

    Returns
    -------
        A configured neonatal disease model without incidence.
    """
    healthy = SusceptibleState(cause)
    with_condition = DiseaseState(cause, birth_prevalence=f"cause.{cause}.birth_prevalence")

    return DiseaseModel(cause, states=[healthy, with_condition])


def NeonatalSWC_with_incidence(
    cause: str,
    incidence_rate: DataInput | None = None,
) -> DiseaseModel:
    """Create a neonatal model with both birth prevalence and incidence.

    Parameters
    ----------
    cause
        The name of the cause of disease.
    incidence_rate
        Source for the susceptible-to-with-condition incidence rate. When
        ``None``, loads from the artifact at ``cause.{cause}.incidence_rate``.

    Returns
    -------
        A configured neonatal disease model with incidence.
    """
    healthy = SusceptibleState(cause)
    with_condition = DiseaseState(cause, birth_prevalence=f"cause.{cause}.birth_prevalence")

    healthy.add_rate_transition(with_condition, transition_rate=incidence_rate)

    return DiseaseModel(cause, states=[healthy, with_condition])
