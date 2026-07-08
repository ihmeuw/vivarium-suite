import itertools

import numpy as np
import pandas as pd
import pytest
from vivarium.engine import InteractiveContext

from tests.test_utilities import build_table_with_age
from vivarium.public_health.population import BasePopulation
from vivarium.public_health.results.causal_factor import CategoricalRiskObserver
from vivarium.public_health.results.columns import COLUMNS
from vivarium.public_health.results.stratification import ResultsStratifier
from vivarium.public_health.risks.base_risk import Risk
from vivarium.public_health.utilities import to_years


@pytest.fixture
def risk():
    return Risk("risk_factor.test_risk")


@pytest.fixture(scope="module")
def risk_data() -> dict:
    """Exposure/category/distribution data for the 4-category test_risk.

    Module-scoped so ``categorical_risk_observer_sim`` (also module-scoped) can
    consume it. Consumers only write it into their own sims, never mutate it.
    """
    year_start = 1990
    year_end = 2010
    exposure_data = build_table_with_age(
        0.25,
        parameter_columns={
            "year": (year_start, year_end),
        },
        value_columns=["cat1", "cat2", "cat3", "cat4"],
    ).melt(
        id_vars=("age_start", "age_end", "year_start", "year_end", "sex"),
        var_name="parameter",
        value_name="value",
    )
    return {
        "exposure": exposure_data,
        "categories": {
            "cat1": "severe",
            "cat2": "moderate",
            "cat3": "mild",
            "cat4": "unexposed",
        },
        "distribution": "ordered_polytomous",
    }


@pytest.fixture(scope="module")
def categorical_risk_observer_sim(base_config_factory, base_plugins, risk_data):
    """Return a shared, read-only sim with two independent categorical-risk observers
    (``test_risk`` and ``another_test_risk``); don't step or mutate it."""
    simulation = InteractiveContext(
        components=[
            BasePopulation(),
            ResultsStratifier(),
            Risk("risk_factor.test_risk"),
            CategoricalRiskObserver("test_risk"),
            Risk("risk_factor.another_test_risk"),
            CategoricalRiskObserver("another_test_risk"),
        ],
        configuration=base_config_factory(),
        plugin_configuration=base_plugins,
        setup=False,
    )
    simulation.configuration.update(
        {
            "stratification": {
                "test_risk": {"include": ["sex"]},
                "another_test_risk": {"include": ["sex"]},
            }
        }
    )
    for key, value in risk_data.items():
        simulation._data.write(f"risk_factor.test_risk.{key}", value)
        simulation._data.write(f"risk_factor.another_test_risk.{key}", value)
    simulation.setup()
    simulation.step()
    simulation.finalize()
    simulation.report()
    return simulation


def test_observation_registration(categorical_risk_observer_sim):
    """Each observer registers its own results, stratified by category and sex."""
    simulation = categorical_risk_observer_sim
    results = simulation.get_results()
    assert set(results) == {"person_time_test_risk", "person_time_another_test_risk"}

    expected = set(itertools.product(["cat1", "cat2", "cat3", "cat4"], ["Female", "Male"]))
    for key in ("person_time_test_risk", "person_time_another_test_risk"):
        person_time = results[key]
        assert set(zip(person_time[COLUMNS.SUB_ENTITY], person_time["sex"])) == expected


def test_different_results_per_risk(categorical_risk_observer_sim):
    """Independent exposure draws give each observer different per-cell person-time,
    so a shared/overwritten output would be caught here."""
    results = categorical_risk_observer_sim.get_results()
    # Align on (category, sex) first -- the two observers don't emit rows in the same order.
    test_risk_pt = results["person_time_test_risk"].set_index([COLUMNS.SUB_ENTITY, "sex"])[
        "value"
    ]
    another_risk_pt = results["person_time_another_test_risk"].set_index(
        [COLUMNS.SUB_ENTITY, "sex"]
    )["value"]
    assert (test_risk_pt != another_risk_pt.reindex(test_risk_pt.index)).all()


def test_observation_correctness(categorical_risk_observer_sim, risk_data):
    """Test that person time appear as expected in the results."""
    simulation = categorical_risk_observer_sim
    time_step = pd.Timedelta(days=simulation.configuration.time.step_size)

    exposure_categories = risk_data["categories"].keys()

    pop = simulation.get_population(["sex", "test_risk.exposure"])

    results = simulation.get_results()["person_time_test_risk"]

    # Check columns
    assert set(results.columns) == set(
        [
            "sex",
            COLUMNS.MEASURE,
            COLUMNS.ENTITY_TYPE,
            COLUMNS.ENTITY,
            COLUMNS.SUB_ENTITY,
            COLUMNS.VALUE,
        ]
    )

    assert (results[COLUMNS.MEASURE] == "person_time").all()
    assert (results[COLUMNS.ENTITY_TYPE] == "rei").all()
    assert (results[COLUMNS.ENTITY] == "test_risk").all()
    for category in exposure_categories:
        for sex in ["Male", "Female"]:
            expected_person_time = sum(
                (pop["test_risk.exposure"] == category) & (pop["sex"] == sex)
            ) * to_years(time_step)
            actual_person_time = results.loc[
                (results[COLUMNS.SUB_ENTITY] == category) & (results["sex"] == sex),
                COLUMNS.VALUE,
            ].values[0]
            assert np.isclose(expected_person_time, actual_person_time, rtol=0.001)


@pytest.mark.parametrize("exclusions", [[], ["cat1"], ["cat1", "cat4"]])
def test_category_exclusions(base_config, base_plugins, risk, risk_data, exclusions):
    observer = CategoricalRiskObserver(f"{risk.causal_factor.name}")
    simulation = InteractiveContext(
        components=[
            BasePopulation(),
            ResultsStratifier(),
            risk,
            observer,
        ],
        configuration=base_config,
        plugin_configuration=base_plugins,
        setup=False,
    )
    simulation.configuration.update(
        {
            "stratification": {
                "test_risk": {
                    "include": ["sex"],
                },
                "excluded_categories": {
                    "test_risk": exclusions,
                },
            },
        },
    )

    for key, value in risk_data.items():
        simulation._data.write(f"risk_factor.test_risk.{key}", value)

    simulation.setup()
    simulation.step()
    results = simulation.get_results()["person_time_test_risk"]
    assert set(results["sub_entity"]) == {"cat1", "cat2", "cat3", "cat4"} - set(exclusions)


def test_observer_sources_categories_from_risk_component(
    base_config, base_plugins, categorical_risk
) -> None:
    """The observer stratifies over the categories from the risk component's
    config data source when the ``categories`` artifact key is withheld.
    """
    risk, risk_data = categorical_risk
    observer = CategoricalRiskObserver(f"{risk.causal_factor.name}")
    simulation = InteractiveContext(
        components=[
            BasePopulation(),
            ResultsStratifier(),
            risk,
            observer,
        ],
        configuration=base_config,
        plugin_configuration=base_plugins,
        setup=False,
    )
    categories = pd.DataFrame(
        {
            "category": ["cat1", "cat2", "cat3", "cat4"],
            "description": ["severe", "moderate", "mild", "unexposed"],
        }
    )
    simulation.configuration.update(
        {
            "risk_factor.test_risk": {"data_sources": {"categories": categories}},
            "stratification": {"test_risk": {"include": ["sex"]}},
        }
    )

    # Write every artifact key except ``categories``, which comes from config.
    for key, value in risk_data.items():
        if key == "categories":
            continue
        simulation._data.write(f"risk_factor.test_risk.{key}", value)

    simulation.setup()
    simulation.step()

    results = simulation.get_results()["person_time_test_risk"]
    assert set(results[COLUMNS.SUB_ENTITY]) == {"cat1", "cat2", "cat3", "cat4"}
