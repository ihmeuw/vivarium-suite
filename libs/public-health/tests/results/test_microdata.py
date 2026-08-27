from typing import Any

import pandas as pd
import pytest
from vivarium.config_tree import ConfigTree
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.components import ComponentConfigError
from vivarium.engine.framework.configuration import build_simulation_configuration
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.population import SimulantData
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

from vivarium.public_health.population import BasePopulation
from vivarium.public_health.results import MicrodataObserver

# base_config runs 1990-2010 with 30.5-day steps starting 1990-01-01, so the collect_metrics
# event_time is 1990-08-01 on the first step and 1990-09-01 on the second.
FIRST_EVENT_TIME = "1990-08-01"
SECOND_EVENT_TIME = "1990-09-01"
# With no `timesteps`, the observer estimates the step count from the time configuration:
# ceil((2010-01-01 - 1990-01-01) / 30.5 days) = 240 steps.
ESTIMATED_TIMESTEPS = 240


class _SimulantID(Component):
    """Add a stable per-simulant integer id, so tests can track which simulants are recorded."""

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self._initialize, columns=["simulant_id"]
        )

    def _initialize(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.DataFrame({"simulant_id": range(len(pop_data.index))}, index=pop_data.index)
        )


class _Disqualifier(Component):
    """Mark all simulants eligible, then disqualify half of them before the second step."""

    def setup(self, builder: Builder) -> None:
        self._steps = 0
        builder.population.register_initializer(
            initializer=self._initialize, columns=["eligible"]
        )

    def _initialize(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.Series(True, index=pop_data.index, name="eligible")
        )

    def on_time_step(self, event: Event) -> None:
        self._steps += 1
        if self._steps == 2:  # after the first observed step, before the second
            self.population_view.update("eligible", self._disqualify_half)

    @staticmethod
    def _disqualify_half(eligible: pd.Series) -> pd.Series:
        eligible.loc[eligible.index[::2]] = False
        return eligible


# One config per observer in the shared sim, keyed by label (None = the unlabeled default
# observer). Each test reads the observation of the scenario it exercises.
_SCENARIOS: dict[str | None, dict[str, Any]] = {
    None: {"columns": ["age", "sex"]},
    "filtered": {"columns": ["age", "sex"], "filter": ['sex == "Female"', "age >= 20"]},
    "second_step": {"columns": ["age"], "timesteps": [SECOND_EVENT_TIME]},
    "capped": {
        # 2 observed timesteps -> 200 // 2 = 100 rows each
        "columns": ["simulant_id"],
        "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
        "row_limit": 200,
    },
    "estimated": {
        # row_limit 2 * ESTIMATED_TIMESTEPS -> 2 rows per step
        "columns": ["simulant_id"],
        "row_limit": 2 * ESTIMATED_TIMESTEPS,
    },
    "cohort": {
        # 2 observed timesteps -> a closed cohort of 200 // 2 = 100 simulants
        "columns": ["simulant_id"],
        "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
        "row_limit": 200,
        "single_random_sample": True,
    },
    "dropout": {
        # like 'cohort', but members leaving the filter drop out (the _Disqualifier
        # flips half the population ineligible between the two observed steps)
        "columns": ["simulant_id"],
        "filter": ["eligible == True"],
        "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
        "row_limit": 200,
        "single_random_sample": True,
    },
}


@pytest.fixture(scope="module")
def microdata_observer_sim(base_config_factory, base_plugins) -> InteractiveContext:
    """Shared read-only sim with one observer per scenario, stepped twice; don't step or mutate it."""
    observers = {label: MicrodataObserver(label) for label in _SCENARIOS}
    config = base_config_factory()
    config.update(
        {
            "population": {"population_size": 250},
            **{observers[label].name: settings for label, settings in _SCENARIOS.items()},
        },
        layer="model_override",
        source="test_microdata",
    )
    sim = InteractiveContext(
        components=[BasePopulation(), _SimulantID(), _Disqualifier(), *observers.values()],
        configuration=config,
        plugin_configuration=base_plugins,
        observe=True,
    )
    sim.step()
    sim.step()
    return sim


def _observation(sim: InteractiveContext, label: str | None = None) -> pd.DataFrame:
    """Get the shared sim's results for the observer with the given label."""
    return sim.get_results()[MicrodataObserver(label).name]


def _build_microdata_sim(
    base_config: ConfigTree, base_plugins: ConfigTree, microdata: dict
) -> InteractiveContext:
    """Build a single-observer sim with the unlabeled observer configured by ``microdata``."""
    base_config.update(
        {"microdata_observer": microdata}, layer="model_override", source="test_microdata"
    )
    return InteractiveContext(
        components=[BasePopulation(), MicrodataObserver()],
        configuration=base_config,
        plugin_configuration=base_plugins,
        observe=True,
    )


def test_microdata_observer_label_sets_component_name() -> None:
    """A label suffixes the component name with ``.<label>``; unlabeled keeps the default name."""
    assert MicrodataObserver().name == "microdata_observer"
    assert MicrodataObserver("my_label").name == "microdata_observer.my_label"


def test_duplicate_microdata_observers_raise(base_config, base_plugins) -> None:
    """Two observers with the same label (or both unlabeled) fail fast on the name collision."""
    with pytest.raises(ComponentConfigError, match="already been set"):
        InteractiveContext(
            components=[MicrodataObserver(), MicrodataObserver()],
            configuration=base_config,
            plugin_configuration=base_plugins,
        )
    # Constructing a sim mutates the configuration it is given, so the labeled
    # pair needs a fresh config rather than reusing base_config.
    with pytest.raises(ComponentConfigError, match="already been set"):
        InteractiveContext(
            components=[MicrodataObserver("twin"), MicrodataObserver("twin")],
            configuration=build_simulation_configuration(),
            plugin_configuration=base_plugins,
        )


def test_microdata_observer_can_be_registered(microdata_observer_sim) -> None:
    """Every observer, labeled and unlabeled, registers an observation under its own name."""
    expected = {MicrodataObserver(label).name for label in _SCENARIOS}
    assert expected <= set(microdata_observer_sim.get_results())


def test_microdata_observer_records_configured_columns(microdata_observer_sim) -> None:
    """The unlabeled observer records exactly its columns (+ event_time) for every simulant, each step."""
    n_simulants = len(microdata_observer_sim.get_population_index())
    results = _observation(microdata_observer_sim)

    assert set(results.columns) == {"age", "sex", "event_time"}
    assert results["event_time"].nunique() == 2  # both steps observed
    # every observed step records the whole fixed-size population
    assert (results.groupby("event_time").size() == n_simulants).all()
    assert len(results) == 2 * n_simulants


@pytest.mark.parametrize(
    "microdata, match",
    [
        ({"columns": []}, "columns"),
        (
            # 1 // 2 observed timesteps floors to 0 rows per timestep
            {
                "columns": ["age"],
                "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
                "row_limit": 1,
            },
            "row_limit",
        ),
        # row_limit below the estimated step count
        ({"columns": ["age"], "row_limit": 1}, "row_limit"),
        # single_random_sample with no row_limit
        ({"columns": ["age"], "single_random_sample": True}, "single_random_sample"),
    ],
    ids=[
        "empty_columns",
        "row_limit_below_timestep_count",
        "row_limit_below_step_estimate",
        "single_random_sample_without_row_limit",
    ],
)
def test_microdata_observer_invalid_config_raises(
    base_config, base_plugins, microdata, match
) -> None:
    """Invalid microdata_observer configs raise a ResultsConfigurationError at setup."""
    with pytest.raises(ResultsConfigurationError, match=match):
        _build_microdata_sim(base_config, base_plugins, microdata)


def test_microdata_observer_filter_subsets_simulants(microdata_observer_sim) -> None:
    """The 'filtered' observer records only the simulants matching its AND-combined filters."""
    n_simulants = len(microdata_observer_sim.get_population_index())
    result = _observation(microdata_observer_sim, "filtered")

    assert not result.empty
    # the filters actually removed some simulants over the two observed steps
    assert len(result) < 2 * n_simulants
    assert (result["sex"] == "Female").all()
    assert (result["age"] >= 20).all()


def test_microdata_observer_observes_only_configured_timesteps(
    microdata_observer_sim,
) -> None:
    """The 'second_step' observer records rows only for its configured timestep."""
    result = _observation(microdata_observer_sim, "second_step")

    assert not result.empty
    # The sim also stepped through FIRST_EVENT_TIME, but only the configured
    # timestep was recorded.
    recorded_times = {pd.Timestamp(time).normalize() for time in result["event_time"]}
    assert recorded_times == {pd.Timestamp(SECOND_EVENT_TIME)}


def test_microdata_observer_row_limit_randomly_samples_per_timestep(
    microdata_observer_sim,
) -> None:
    """The 'capped' observer records row_limit // n_timesteps rows, freshly resampled each step."""
    result = _observation(microdata_observer_sim, "capped")

    samples = result.groupby("event_time")["simulant_id"].apply(set)
    assert len(samples) == 2  # both configured timesteps recorded
    assert all(len(sample) == 100 for sample in samples)  # 200 // 2 rows each
    # A fresh random sample each step - not the same simulants both steps.
    assert samples.iloc[0] != samples.iloc[1]


def test_microdata_observer_row_limit_without_timesteps_uses_step_estimate(
    microdata_observer_sim,
) -> None:
    """The 'estimated' observer divides row_limit by the estimated step count for its per-step cap."""
    result = _observation(microdata_observer_sim, "estimated")

    assert result["event_time"].nunique() == 2  # both steps observed
    # (2 * ESTIMATED_TIMESTEPS) // ESTIMATED_TIMESTEPS = 2 rows per step
    assert (result.groupby("event_time").size() == 2).all()


def test_microdata_observer_warns_and_deduplicates_timesteps(
    base_config, base_plugins, caplog
) -> None:
    """Duplicate dates in `timesteps` are deduplicated with a warning, not an error."""
    _build_microdata_sim(
        base_config,
        base_plugins,
        {"columns": ["age"], "timesteps": [FIRST_EVENT_TIME, FIRST_EVENT_TIME]},
    )
    assert "duplicate" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_microdata_observer_single_random_sample_records_fixed_cohort(
    microdata_observer_sim,
) -> None:
    """The 'cohort' observer records the same once-sampled cohort at every observed step."""
    result = _observation(microdata_observer_sim, "cohort")

    recorded = result.groupby("event_time")["simulant_id"].apply(set)
    assert len(recorded) == 2  # both configured timesteps recorded
    assert len(recorded.iloc[0]) == 100  # row_limit // n_observed_timesteps
    # The same simulants both steps - a fixed closed cohort, not a fresh sample each step.
    assert recorded.iloc[0] == recorded.iloc[1]


def test_microdata_observer_single_random_sample_drops_members_leaving_filter(
    microdata_observer_sim,
) -> None:
    """The 'dropout' observer drops cohort members that leave the filter and never refills."""
    result = _observation(microdata_observer_sim, "dropout")

    recorded = result.groupby("event_time")["simulant_id"].apply(set)
    assert len(recorded.iloc[0]) == 100  # the full cohort, all eligible on the first step
    # No new simulants enter the cohort (no refill)...
    assert recorded.iloc[1].issubset(recorded.iloc[0])
    # ...and the members the _Disqualifier flipped ineligible really did drop out.
    assert len(recorded.iloc[1]) < len(recorded.iloc[0])
