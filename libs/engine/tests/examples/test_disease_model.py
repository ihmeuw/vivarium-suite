from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from vivarium.config_tree import ConfigTree
from vivarium.fuzzy_checker import FuzzyChecker
from vivarium.interactive import Simulation

from vivarium.engine.framework.utilities import from_yearly

LRI = "lower_respiratory_infections"
INFECTED = f"infected_with_{LRI}"
INFECTED_KEY = f"disease_state.{INFECTED}"
INCIDENCE_KEY = f"disease_transition.{INFECTED}.incidence_rate"
REMISSION_KEY = f"disease_transition.{INFECTED}.remission_rate"
STEP_SIZE = timedelta(days=0.5)


def _disease_config(
    mortality_rate: float,
    incidence_rate: float,
    remission_rate: float,
    excess_mortality_rate: float,
) -> dict[str, Any]:
    return {
        "mortality": {"data_sources": {"mortality_rate": mortality_rate}},
        INCIDENCE_KEY: {"data_sources": {"incidence_rate": incidence_rate}},
        REMISSION_KEY: {"data_sources": {"remission_rate": remission_rate}},
        INFECTED_KEY: {"data_sources": {"excess_mortality_rate": excess_mortality_rate}},
    }


def _expected_csmr(
    incidence_rate: float, remission_rate: float, excess_mortality_rate: float
) -> float:
    return incidence_rate * excess_mortality_rate / (excess_mortality_rate + remission_rate)


def test_disease_model(fuzzy_checker: FuzzyChecker, disease_model_spec: Path) -> None:
    config = ConfigTree(disease_model_spec, layers=["base", "override"])
    config.update(
        {
            "configuration": _disease_config(
                mortality_rate=20.0,
                incidence_rate=25.0,
                remission_rate=50.0,
                excess_mortality_rate=0.01,
            ),
        }
    )

    simulation = Simulation(config)

    all_attributes = simulation.list_attributes()
    pop = simulation.get_population(all_attributes)
    expected_columns = {
        "is_alive",
        "previous_alive",
        "age",
        "sex",
        "entrance_time",
        "mortality_rate",
        "lower_respiratory_infections",
        "lower_respiratory_infections.cause_specific_mortality_rate",
        "child_wasting_propensity",
        "child_wasting.proportion_exposed",
        "child_wasting.base_proportion_exposed",
        "child_wasting.exposure",
        "susceptible_to_lower_respiratory_infections.initialization_weights",
        "susceptible_to_lower_respiratory_infections.excess_mortality_rate",
        "susceptible_to_lower_respiratory_infections.excess_mortality_rate.population_attributable_fraction",
        "infected_with_lower_respiratory_infections.initialization_weights",
        "infected_with_lower_respiratory_infections.incidence_rate.population_attributable_fraction",
        "infected_with_lower_respiratory_infections.remission_rate",
        "infected_with_lower_respiratory_infections.excess_mortality_rate.population_attributable_fraction",
        "infected_with_lower_respiratory_infections.excess_mortality_rate",
        "infected_with_lower_respiratory_infections.incidence_rate",
        "infected_with_lower_respiratory_infections.remission_rate.population_attributable_fraction",
        "effect_of_child_wasting_on_infected_with_lower_respiratory_infections.incidence_rate.relative_risk",
        "sqlns.effect_size",
    }
    assert set(pop.columns) == expected_columns
    assert len(pop) == 100_000
    assert np.all(pop["is_alive"] == True)
    assert np.all((pop["age"] >= 0) & (pop["age"] <= 5))
    assert np.all(pop["entrance_time"] == datetime(2021, 12, 31, 12))

    for sex in ["Female", "Male"]:
        fuzzy_checker.assert_proportion(
            observed_numerator=(pop["sex"] == sex).sum(),
            observed_denominator=len(pop),
            target_proportion=0.5,
            # todo: remove this parameter when MIC-5412 is resolved
            name=f"{sex}_proportion",
        )

    assert np.all(
        pop["lower_respiratory_infections"] == "susceptible_to_lower_respiratory_infections"
    )
    assert np.all(
        (pop["child_wasting_propensity"] >= 0) & (pop["child_wasting_propensity"] <= 1)
    )

    simulation.step()
    pop = simulation.get_population(["is_alive", "lower_respiratory_infections"])
    is_alive = pop["is_alive"] == True

    alive_target = from_yearly(20, timedelta(days=0.5))
    assert isinstance(alive_target, float)
    fuzzy_checker.assert_proportion(
        observed_numerator=(len(pop[~is_alive])),
        observed_denominator=len(pop),
        target_proportion=alive_target,
        # todo: remove this parameter when MIC-5412 is resolved
        name="alive_proportion",
    )

    has_lri = (
        pop["lower_respiratory_infections"] == "infected_with_lower_respiratory_infections"
    )
    lri_target = from_yearly(25, timedelta(days=0.5))
    assert isinstance(lri_target, float)
    fuzzy_checker.assert_proportion(
        observed_numerator=(len(pop[is_alive & has_lri])),
        observed_denominator=len(pop[is_alive]),
        target_proportion=lri_target,
        # todo: remove this parameter when MIC-5412 is resolved
        name="lri_proportion",
    )

    # todo test remission and excess mortality
    # todo test risk factor and intervention


def test_configured_rates_reach_mortality(disease_model_spec: Path) -> None:
    config = ConfigTree(disease_model_spec, layers=["base", "override"])
    configuration = config.configuration
    mortality_rate = configuration.mortality.data_sources.mortality_rate
    incidence_rate = configuration[INCIDENCE_KEY].data_sources.incidence_rate
    remission_rate = configuration[REMISSION_KEY].data_sources.remission_rate
    excess_mortality_rate = configuration[INFECTED_KEY].data_sources.excess_mortality_rate

    simulation = Simulation(config)
    simulation.step()
    pop = simulation.get_population(["mortality_rate", LRI])
    is_infected = pop[LRI] == INFECTED
    assert is_infected.any() and (~is_infected).any()

    background_rate = mortality_rate - _expected_csmr(
        incidence_rate, remission_rate, excess_mortality_rate
    )
    assert np.allclose(
        pop.loc[~is_infected, "mortality_rate"], from_yearly(background_rate, STEP_SIZE)
    )
    assert np.allclose(
        pop.loc[is_infected, "mortality_rate"],
        from_yearly(background_rate + excess_mortality_rate, STEP_SIZE),
    )


def test_configured_remission_reaches_pipeline(disease_model_spec: Path) -> None:
    config = ConfigTree(disease_model_spec, layers=["base", "override"])
    config.update(
        {"configuration": {REMISSION_KEY: {"data_sources": {"remission_rate": 12.3}}}}
    )

    simulation = Simulation(config)
    remission = simulation.get_population([f"{INFECTED}.remission_rate"])
    assert np.allclose(remission, from_yearly(12.3, STEP_SIZE))


@pytest.mark.parametrize("excess_mortality_rate", [0.06, 5.0])
def test_excess_mortality_only_affects_infected(
    disease_model_spec: Path, excess_mortality_rate: float
) -> None:
    config = ConfigTree(disease_model_spec, layers=["base", "override"])
    config.update(
        {
            "configuration": _disease_config(
                mortality_rate=10.0,
                incidence_rate=25.0,
                remission_rate=45.1,
                excess_mortality_rate=excess_mortality_rate,
            ),
        }
    )

    simulation = Simulation(config)
    simulation.step()
    pop = simulation.get_population(["mortality_rate", LRI])
    is_infected = pop[LRI] == INFECTED
    assert is_infected.any() and (~is_infected).any()

    infected_rate = pop.loc[is_infected, "mortality_rate"]
    susceptible_rate = pop.loc[~is_infected, "mortality_rate"]
    assert (susceptible_rate > 0).all()
    assert infected_rate.min() > susceptible_rate.max()
    assert np.allclose(
        infected_rate - susceptible_rate.iloc[0],
        from_yearly(excess_mortality_rate, STEP_SIZE),
    )
