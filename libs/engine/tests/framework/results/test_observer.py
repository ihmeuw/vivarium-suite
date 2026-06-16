from typing import Any

import pytest
from pytest_mock import MockerFixture
from vivarium.config_tree.main import ConfigTree

from tests.framework.results.helpers import HARRY_POTTER_CONFIG, Hogwarts
from vivarium.engine import InteractiveContext
from vivarium.engine.framework.components.manager import ComponentConfigError
from vivarium.engine.framework.engine import Builder
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


@pytest.mark.xfail(reason="not implemented: MicrodataObserver registration")
def test_microdata_observer_can_be_registered() -> None:
    """The observer sets up in any sim and registers a single named observation."""
    sim = InteractiveContext(
        configuration=HARRY_POTTER_CONFIG,
        components=[Hogwarts(), MicrodataObserver()],
    )
    assert "microdata_observer" in sim._results._results_context.observations


@pytest.mark.xfail(reason="not implemented: records all attributes across timesteps")
def test_microdata_observer_records_all_attributes() -> None:
    """Recorded microdata has a column for every attribute and one row per simulant per step."""
    sim = InteractiveContext(
        configuration=HARRY_POTTER_CONFIG,
        components=[Hogwarts(), MicrodataObserver()],
    )
    expected_attributes = set(sim.get_attribute_names())
    n_simulants = len(sim.get_population())

    sim.step()
    one_step = sim.get_results()["microdata_observer"]
    sim.step()
    two_steps = sim.get_results()["microdata_observer"]

    assert expected_attributes <= set(one_step.columns)
    assert "event_time" in one_step.columns
    assert len(one_step) == n_simulants
    assert len(two_steps) == 2 * n_simulants
