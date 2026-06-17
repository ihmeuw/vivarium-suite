from typing import Any

import pytest
from pytest_mock import MockerFixture
from vivarium.config_tree.main import ConfigTree

from tests.framework.results.helpers import HARRY_POTTER_CONFIG, Hogwarts
from vivarium.engine import InteractiveContext
from vivarium.engine.framework.components.manager import ComponentConfigError
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError
from vivarium.engine.framework.results.observer import MicrodataObserver, Observer


class TestObserver(Observer):
    def register_observations(self, builder: Builder) -> None:
        pass


class TestDefaultObserverStratifications(Observer):
    def register_observations(self, builder: Builder) -> None:
        pass


class TestObserverStratifications(Observer):
    @property
    def configuration_defaults(self) -> dict[str, Any]:
        return {
            "stratification": {
                self.get_configuration_name(): {
                    "exclude": ["baz"],
                    "include": ["foo"],
                },
            },
        }

    def register_observations(self, builder: Builder) -> None:
        pass


def test_observer_instantiation() -> None:
    observer = TestObserver()
    assert observer.name == "test_observer"


@pytest.mark.parametrize(
    "is_interactive, results_dir",
    [
        (False, "/some/results/dir"),
        (True, None),
    ],
)
def test_set_results_dir(
    is_interactive: bool, results_dir: str | None, mocker: MockerFixture
) -> None:
    builder = mocker.Mock()
    if is_interactive:
        builder.configuration = ConfigTree()
    else:
        builder.configuration = ConfigTree(
            {
                "output_data": {"results_directory": results_dir},
            }
        )

    observer = TestObserver()
    observer.set_results_dir(builder)

    assert observer.results_dir == results_dir


def test_observer_get_configuration(
    base_config: ConfigTree,
) -> None:

    observer = TestObserverStratifications()
    sim = InteractiveContext(
        base_config,
        components=[observer],
    )
    sim_observer_config = sim.configuration["stratification"][
        observer.get_configuration_name()
    ]
    # Observer.configuration calls get_configuration
    observer_config = observer.configuration
    assert observer_config is not None
    assert observer_config.to_dict() == dict(sim_observer_config)


def test_duplicated_observer_error(base_config: ConfigTree) -> None:
    observer1 = TestObserverStratifications()
    observer2 = TestObserverStratifications()
    with pytest.raises(
        ComponentConfigError,
        match="is attempting to set the configuration value test, but it has already been set",
    ):
        InteractiveContext(
            base_config,
            components=[observer1, observer2],
        )


def test_microdata_observer_can_be_registered() -> None:
    """The observer sets up in any sim and registers a single named observation."""
    config = {**HARRY_POTTER_CONFIG, "microdata_observer": {"columns": ["student_house"]}}
    sim = InteractiveContext(
        configuration=config,
        components=[Hogwarts(), MicrodataObserver()],
    )
    assert "microdata_observer" in sim._results._results_context.observations


def test_microdata_observer_records_configured_columns() -> None:
    """Records exactly the configured columns (+ event_time) for every simulant, each step."""
    config = {
        **HARRY_POTTER_CONFIG,
        "microdata_observer": {"columns": ["student_house", "exam_score"]},
    }
    sim = InteractiveContext(
        configuration=config,
        components=[Hogwarts(), MicrodataObserver()],
    )
    n_simulants = len(sim.get_population_index())

    sim.step()
    one_step = sim.get_results()["microdata_observer"]
    sim.step()
    two_steps = sim.get_results()["microdata_observer"]

    assert set(one_step.columns) == {"student_house", "exam_score", "event_time"}
    assert len(one_step) == n_simulants
    assert len(two_steps) == 2 * n_simulants  # fixed-size population over two steps
    assert two_steps["event_time"].nunique() == 2


def test_microdata_observer_requires_columns() -> None:
    """An empty `columns` list raises a configuration error at setup."""
    config = {**HARRY_POTTER_CONFIG, "microdata_observer": {"columns": []}}
    with pytest.raises(ResultsConfigurationError, match="columns"):
        InteractiveContext(configuration=config, components=[Hogwarts(), MicrodataObserver()])


def test_microdata_observer_filter_subsets_simulants() -> None:
    """`filter` entries restrict recording to matching simulants, AND-combined."""
    config = {
        **HARRY_POTTER_CONFIG,
        "microdata_observer": {
            "columns": ["student_house", "power_level"],
            "filter": ['student_house == "gryffindor"', "power_level >= 60"],
        },
    }
    sim = InteractiveContext(
        configuration=config, components=[Hogwarts(), MicrodataObserver()]
    )
    sim.step()
    result = sim.get_results()["microdata_observer"]

    assert (result["student_house"] == "gryffindor").all()
    assert (result["power_level"] >= 60).all()


def test_microdata_observer_observes_only_configured_timesteps() -> None:
    """Only timesteps listed in `timesteps` are recorded."""
    # HARRY_POTTER_CONFIG starts 2024-04-22 with 365-day steps, so the collect_metrics
    # event_time is 2025-04-22 on the first step and 2026-04-22 on the second.
    config = {
        **HARRY_POTTER_CONFIG,
        "microdata_observer": {"columns": ["student_house"], "timesteps": ["2026-04-22"]},
    }
    sim = InteractiveContext(
        configuration=config, components=[Hogwarts(), MicrodataObserver()]
    )

    sim.step()  # 2025-04-22 -> not in timesteps
    assert sim.get_results()["microdata_observer"].empty
    sim.step()  # 2026-04-22 -> in timesteps
    assert not sim.get_results()["microdata_observer"].empty


def test_microdata_observer_row_limit_randomly_samples_per_timestep() -> None:
    """`row_limit` caps to row_limit // n_observed_timesteps rows, randomly resampled each step."""
    config = {
        **HARRY_POTTER_CONFIG,
        "microdata_observer": {
            "columns": ["student_id"],
            "timesteps": [
                "2026-04-22",
                "2027-04-22",
            ],  # two observed steps -> 20 // 2 = 10 rows per step
            "row_limit": 20,
        },
    }
    sim = InteractiveContext(
        configuration=config, components=[Hogwarts(), MicrodataObserver()]
    )

    sim.step()  # 2025-04-22 -> not observed
    sim.step()  # 2026-04-22 -> observed
    sim.step()  # 2027-04-22 -> observed
    result = sim.get_results()["microdata_observer"]

    cohorts = result.groupby("event_time")["student_id"].apply(set)
    assert all(len(cohort) == 10 for cohort in cohorts)
    # A fresh random sample each step - not the same first-N simulants.
    assert cohorts.iloc[0] != cohorts.iloc[1]
