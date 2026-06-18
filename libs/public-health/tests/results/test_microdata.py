import pytest
from vivarium.config_tree import ConfigTree
from vivarium.engine import InteractiveContext
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

from vivarium.public_health.population import BasePopulation
from vivarium.public_health.results import MicrodataObserver


def _configure(base_config: ConfigTree, columns: list[str]) -> ConfigTree:
    base_config.update(
        {"microdata_observer": {"columns": columns}},
        layer="model_override",
        source="test_microdata",
    )
    return base_config


def test_microdata_observer_can_be_registered(base_config, base_plugins) -> None:
    """The observer sets up in any sim and registers a single named observation."""
    config = _configure(base_config, ["age"])
    sim = InteractiveContext(
        components=[BasePopulation(), MicrodataObserver()],
        configuration=config,
        plugin_configuration=base_plugins,
    )
    assert "microdata_observer" in sim._results._results_context.observations


def test_microdata_observer_records_configured_columns(base_config, base_plugins) -> None:
    """Records exactly the configured columns (+ event_time) for every simulant, each step."""
    config = _configure(base_config, ["age", "sex"])
    sim = InteractiveContext(
        components=[BasePopulation(), MicrodataObserver()],
        configuration=config,
        plugin_configuration=base_plugins,
    )
    n_simulants = len(sim.get_population_index())

    sim.step()
    one_step = sim.get_results()["microdata_observer"]
    sim.step()
    two_steps = sim.get_results()["microdata_observer"]

    assert set(one_step.columns) == {"age", "sex", "event_time"}
    assert len(one_step) == n_simulants
    assert len(two_steps) == 2 * n_simulants  # fixed-size population over two steps
    assert two_steps["event_time"].nunique() == 2


def test_microdata_observer_requires_columns(base_config, base_plugins) -> None:
    """An empty `columns` list raises a configuration error at setup."""
    config = _configure(base_config, [])
    with pytest.raises(ResultsConfigurationError, match="columns"):
        InteractiveContext(
            components=[BasePopulation(), MicrodataObserver()],
            configuration=config,
            plugin_configuration=base_plugins,
        )
