import pandas as pd
import pytest
from vivarium.config_tree import ConfigTree
from vivarium.engine import Component, InteractiveContext
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


def _configure(
    base_config: ConfigTree, microdata: dict, population_size: int | None = None
) -> ConfigTree:
    overrides: dict = {"microdata_observer": microdata}
    if population_size is not None:
        overrides["population"] = {"population_size": population_size}
    base_config.update(overrides, layer="model_override", source="test_microdata")
    return base_config


def _build_microdata_sim(
    base_config: ConfigTree,
    base_plugins: ConfigTree,
    microdata: dict,
    *,
    components: list[Component] | None = None,
    population_size: int | None = None,
) -> InteractiveContext:
    """Build a sim with a MicrodataObserver configured by ``microdata``."""
    return InteractiveContext(
        components=components or [BasePopulation(), MicrodataObserver()],
        configuration=_configure(base_config, microdata, population_size=population_size),
        plugin_configuration=base_plugins,
    )


@pytest.fixture(scope="module")
def microdata_observer_sim(base_config_factory, base_plugins) -> InteractiveContext:
    """Shared read-only sim recording [age, sex] over two steps; don't step or mutate it."""
    sim = _build_microdata_sim(
        base_config_factory(), base_plugins, {"columns": ["age", "sex"]}
    )
    sim.step()
    sim.step()
    return sim


def test_microdata_observer_can_be_registered(microdata_observer_sim) -> None:
    """The observer sets up and registers a single named observation."""
    observations = microdata_observer_sim._results._results_context.observations
    assert "microdata_observer" in observations


def test_microdata_observer_records_configured_columns(microdata_observer_sim) -> None:
    """Records exactly the configured columns (+ event_time) for every simulant, each step."""
    n_simulants = len(microdata_observer_sim.get_population_index())
    results = microdata_observer_sim.get_results()["microdata_observer"]

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


def test_microdata_observer_filter_subsets_simulants(base_config, base_plugins) -> None:
    """`filter` entries restrict recording to matching simulants, AND-combined."""
    sim = _build_microdata_sim(
        base_config,
        base_plugins,
        {"columns": ["age", "sex"], "filter": ['sex == "Female"', "age >= 20"]},
        population_size=250,
    )
    n_simulants = len(sim.get_population_index())
    sim.step()
    result = sim.get_results()["microdata_observer"]

    assert not result.empty
    assert len(result) < n_simulants  # the filter actually removed some simulants
    assert (result["sex"] == "Female").all()
    assert (result["age"] >= 20).all()


def test_microdata_observer_observes_only_configured_timesteps(
    base_config, base_plugins
) -> None:
    """Only timesteps whose event time matches `timesteps` are recorded."""
    sim = _build_microdata_sim(
        base_config, base_plugins, {"columns": ["age"], "timesteps": [SECOND_EVENT_TIME]}
    )

    sim.step()  # event_time FIRST_EVENT_TIME -> not in timesteps
    assert sim.get_results()["microdata_observer"].empty
    sim.step()  # event_time SECOND_EVENT_TIME -> in timesteps
    assert not sim.get_results()["microdata_observer"].empty


def test_microdata_observer_row_limit_randomly_samples_per_timestep(
    base_config, base_plugins
) -> None:
    """`row_limit` caps to row_limit // n_observed_timesteps rows, randomly resampled each step."""
    sim = _build_microdata_sim(
        base_config,
        base_plugins,
        # 2 observed timesteps -> 200 // 2 = 100 rows each
        {
            "columns": ["simulant_id"],
            "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
            "row_limit": 200,
        },
        components=[BasePopulation(), _SimulantID(), MicrodataObserver()],
        population_size=250,
    )

    sim.step()  # FIRST_EVENT_TIME -> observed
    sim.step()  # SECOND_EVENT_TIME -> observed
    result = sim.get_results()["microdata_observer"]

    cohorts = result.groupby("event_time")["simulant_id"].apply(set)
    assert all(len(cohort) == 100 for cohort in cohorts)
    # A fresh random sample each step - not the same simulants both steps.
    assert cohorts.iloc[0] != cohorts.iloc[1]


def test_microdata_observer_row_limit_without_timesteps_uses_step_estimate(
    base_config, base_plugins
) -> None:
    """With no `timesteps`, the per-step cap divides `row_limit` by the estimated step count."""
    sim = _build_microdata_sim(
        base_config,
        base_plugins,
        # row_limit 2 * ESTIMATED_TIMESTEPS -> 2 rows per step
        {"columns": ["simulant_id"], "row_limit": 2 * ESTIMATED_TIMESTEPS},
        components=[BasePopulation(), _SimulantID(), MicrodataObserver()],
        population_size=250,
    )

    sim.step()
    sim.step()
    result = sim.get_results()["microdata_observer"]

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
    base_config, base_plugins
) -> None:
    """single_random_sample records the same once-sampled cohort at every observed step."""
    sim = _build_microdata_sim(
        base_config,
        base_plugins,
        # 2 observed timesteps -> 200 // 2 = 100 rows each
        {
            "columns": ["simulant_id"],
            "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
            "row_limit": 200,
            "single_random_sample": True,
        },
        components=[BasePopulation(), _SimulantID(), MicrodataObserver()],
        population_size=250,
    )

    sim.step()  # FIRST_EVENT_TIME -> observed
    sim.step()  # SECOND_EVENT_TIME -> observed
    result = sim.get_results()["microdata_observer"]

    recorded = result.groupby("event_time")["simulant_id"].apply(set)
    assert len(recorded.iloc[0]) == 100  # row_limit // n_observed_timesteps
    # The same simulants both steps - a fixed closed cohort, not a fresh sample each step.
    assert recorded.iloc[0] == recorded.iloc[1]


def test_microdata_observer_single_random_sample_drops_members_leaving_filter(
    base_config, base_plugins
) -> None:
    """A cohort member that leaves the filter is dropped and never refilled (upper bound)."""
    sim = _build_microdata_sim(
        base_config,
        base_plugins,
        {
            "columns": ["simulant_id"],
            "filter": ["eligible == True"],
            "timesteps": [FIRST_EVENT_TIME, SECOND_EVENT_TIME],
            "row_limit": 200,  # cohort size 100
            "single_random_sample": True,
        },
        components=[BasePopulation(), _SimulantID(), _Disqualifier(), MicrodataObserver()],
        population_size=250,
    )

    sim.step()  # FIRST_EVENT_TIME -> full cohort still eligible
    sim.step()  # _Disqualifier flips half ineligible, then SECOND_EVENT_TIME observes
    result = sim.get_results()["microdata_observer"]

    recorded = result.groupby("event_time")["simulant_id"].apply(set)
    assert len(recorded.iloc[0]) == 100  # full cohort recorded the first step
    # No new simulants enter the cohort (no refill)...
    assert recorded.iloc[1].issubset(recorded.iloc[0])
    # ...and the disqualified members really did drop out.
    assert len(recorded.iloc[1]) < len(recorded.iloc[0])
