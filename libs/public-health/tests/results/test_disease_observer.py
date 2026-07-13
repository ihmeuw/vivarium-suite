import itertools

import numpy as np
import pandas as pd
import pytest
from vivarium.engine import InteractiveContext

from tests.test_utilities import build_table_with_age
from vivarium.public_health.disease import DiseaseModel, DiseaseState
from vivarium.public_health.disease.state import SusceptibleState
from vivarium.public_health.population import BasePopulation
from vivarium.public_health.results.columns import COLUMNS
from vivarium.public_health.results.disease import DiseaseObserver
from vivarium.public_health.results.stratification import ResultsStratifier
from vivarium.public_health.utilities import to_years


@pytest.fixture
def disease() -> str:
    return "t_virus"


def _make_t_virus_model(disease: str, year_start: int, year_end: int) -> DiseaseModel:
    """Build a fresh SI ``DiseaseModel`` where everyone reaches ``with_condition`` quickly."""
    healthy = SusceptibleState("with_condition", allow_self_transition=False)
    with_condition = DiseaseState(
        "with_condition",
        disability_weight=build_table_with_age(
            0.0, parameter_columns={"year": (year_start - 1, year_end)}
        ),
        prevalence=build_table_with_age(
            0.2, parameter_columns={"year": (year_start - 1, year_end)}
        ),
    )
    healthy.add_rate_transition(
        with_condition,
        transition_rate=build_table_with_age(
            0.9, parameter_columns={"year": (year_start - 1, year_end)}
        ),
    )
    return DiseaseModel(disease, residual_state=healthy, states=[healthy, with_condition])


def _build_disease_observer_sim(configuration, base_plugins, components, config_update=None):
    """Build and set up a disease-observer sim; the caller steps it."""
    simulation = InteractiveContext(
        components=components,
        configuration=configuration,
        plugin_configuration=base_plugins,
        setup=False,
    )
    if config_update:
        simulation.configuration.update(config_update)
    simulation.setup()
    return simulation


@pytest.fixture
def model(base_config, disease: str) -> DiseaseModel:
    """A dummy SI model where everyone should be `with_condition` by the third timestep."""
    return _make_t_virus_model(
        disease, base_config.time.start.year, base_config.time.end.year
    )


def _make_vampiris_model() -> DiseaseModel:
    """Build the 3-state vampiris model (human -> turning -> vampire)."""
    healthy = SusceptibleState("human")
    turning = DiseaseState("turning")
    infected = DiseaseState("vampire")
    healthy.add_rate_transition(turning)
    turning.add_rate_transition(infected)
    return DiseaseModel(
        "vampiris", residual_state=healthy, states=[healthy, turning, infected]
    )


@pytest.fixture
def vampiris():
    return _make_vampiris_model()


@pytest.fixture(scope="module")
def disease_observer_sim(base_config_factory, base_plugins):
    """Return a shared, read-only sim with two independent disease observers
    (``t_virus`` and ``vampiris``); don't step or mutate it."""
    config = base_config_factory()
    simulation = _build_disease_observer_sim(
        config,
        base_plugins,
        [
            BasePopulation(),
            _make_t_virus_model("t_virus", config.time.start.year, config.time.end.year),
            _make_vampiris_model(),
            ResultsStratifier(),
            DiseaseObserver("t_virus"),
            DiseaseObserver("vampiris"),
        ],
        config_update={"stratification": {"t_virus": {"include": ["sex"]}}},
    )
    disease_states_at_start = simulation.get_population("t_virus")
    simulation.step()
    return simulation, disease_states_at_start


# Updating the previous state
def test_previous_state_update(base_config, base_plugins, disease, model):
    """Test that the observer previous_state column is updated as expected."""
    observer = DiseaseObserver(disease)
    simulation = _build_disease_observer_sim(
        base_config,
        base_plugins,
        [BasePopulation(), model, ResultsStratifier(), observer],
        config_update={"stratification": {"t_virus": {"include": ["sex"]}}},
    )
    state_cols = [observer.previous_state_column_name, observer.disease]
    pop0 = simulation.get_population(state_cols)

    # Assert that the previous_state column equals the current state column
    assert (pop0[observer.previous_state_column_name] == pop0[observer.disease]).all()
    assert pop0[observer.disease].notna().all()

    simulation.step()
    pop = simulation.get_population(state_cols)

    assert pop[observer.previous_state_column_name].equals(pop0[observer.disease])
    # All simulants are currently but not necessarily previously "with_condition"
    assert (
        pop[observer.previous_state_column_name].isin(
            ["susceptible_to_with_condition", "with_condition"]
        )
    ).all()
    assert (pop[observer.disease] == "with_condition").all()

    simulation.step()
    pop = simulation.get_population(state_cols)

    # All simulants are currently and were previously "with_condition"
    assert (pop[observer.previous_state_column_name] == "with_condition").all()
    assert (pop[observer.disease] == "with_condition").all()


def test_observation_registration(disease_observer_sim):
    """Each observer saves its own results; the t_virus observations are stratified by sex."""
    simulation, _ = disease_observer_sim
    results = simulation.get_results()
    # Each observer saves its own per-disease results.
    assert set(results) == {
        "person_time_t_virus",
        "transition_count_t_virus",
        "person_time_vampiris",
        "transition_count_vampiris",
    }
    person_time = results["person_time_t_virus"]
    transition_count = results["transition_count_t_virus"]

    # Check that all expected observations are present
    assert set(zip(person_time[COLUMNS.SUB_ENTITY], person_time["sex"])) == set(
        itertools.product(
            *[["susceptible_to_with_condition", "with_condition"], ["Female", "Male"]]
        )
    )
    assert set(zip(transition_count[COLUMNS.SUB_ENTITY], transition_count["sex"])) == set(
        itertools.product(
            *[["susceptible_to_with_condition_to_with_condition"], ["Female", "Male"]]
        )
    )


# Person time and all states and transition counts are correct
def test_observation_correctness(disease_observer_sim):
    """Test that person time and event counts appear as expected in the results."""
    simulation, disease_states = disease_observer_sim
    time_step = pd.Timedelta(days=simulation.configuration.time.step_size)

    # All simulants should transition to "with_condition"
    susceptible_at_start = sum(disease_states == "susceptible_to_with_condition")
    expected_susceptible_person_time = susceptible_at_start * to_years(time_step)
    expected_with_condition_person_time = (
        len(disease_states) - susceptible_at_start
    ) * to_years(time_step)

    results = simulation.get_results()
    person_time = results["person_time_t_virus"]
    transition_count = results["transition_count_t_virus"]

    # Check columns
    for measure in ["person_time", "transition_count"]:
        df = eval(measure)
        assert set(df.columns) == set(
            [
                "sex",
                COLUMNS.MEASURE,
                COLUMNS.ENTITY_TYPE,
                COLUMNS.ENTITY,
                COLUMNS.SUB_ENTITY,
                COLUMNS.VALUE,
            ]
        )
        assert (df[COLUMNS.MEASURE] == measure).all()
        assert (df[COLUMNS.ENTITY_TYPE] == "cause").all()
        assert (df[COLUMNS.ENTITY] == "t_virus").all()

    # Check values
    actual_tx_count = transition_count.loc[
        transition_count[COLUMNS.SUB_ENTITY]
        == "susceptible_to_with_condition_to_with_condition",
        COLUMNS.VALUE,
    ].sum()
    actual_person_times = person_time.groupby(COLUMNS.SUB_ENTITY)[COLUMNS.VALUE].sum()
    assert actual_tx_count == susceptible_at_start
    assert np.isclose(
        actual_person_times["susceptible_to_with_condition"],
        expected_susceptible_person_time,
        rtol=0.001,
    )
    assert np.isclose(
        actual_person_times["with_condition"], expected_with_condition_person_time, rtol=0.001
    )


@pytest.mark.parametrize(
    "person_time_exclusions, transition_count_exclusions",
    [
        ([], []),
        (["susceptible_to_human"], ["susceptible_to_human_to_turning"]),
        (["susceptible_to_human", "turning"], []),
    ],
)
def test_category_exclusions(
    vampiris, base_config, base_plugins, person_time_exclusions, transition_count_exclusions
):
    """Test that we can exclude diseases via the model spec."""
    vampiris_observer = DiseaseObserver(vampiris.cause)

    simulation = _build_disease_observer_sim(
        base_config,
        base_plugins,
        [BasePopulation(), vampiris, ResultsStratifier(), vampiris_observer],
        config_update={
            "stratification": {
                "excluded_categories": {
                    "vampiris": person_time_exclusions,
                    "transition_vampiris": transition_count_exclusions,
                }
            }
        },
    )
    simulation.step()
    person_time = simulation.get_results()["person_time_vampiris"]
    transition_count = simulation.get_results()["transition_count_vampiris"]
    assert set(person_time["sub_entity"]) == set(vampiris.state_names) - set(
        person_time_exclusions
    )
    assert set(transition_count["sub_entity"]) == set(vampiris.transition_names) - set(
        transition_count_exclusions
    )
