"""
==================
Microdata Observer
==================

An observer that records a configured set of population columns for each simulant at each observed
timestep, concatenated across timesteps. Unlike the stratified public health observers, it records
raw per-simulant rows so that derived quantities can be derived downstream.

"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger
from vivarium.engine.framework.results import Observer
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder
    from vivarium.engine.framework.event import Event
    from vivarium.engine.framework.population import SimulantData


class MicrodataObserver(Observer):
    """Observer that records a configured set of columns for each simulant.

    At each observed timestep it records the configured columns (plus ``event_time``) for every
    (optionally filtered) simulant, concatenated across timesteps, so users can compute
    derived quantities downstream. It subclasses the framework's ``Observer`` directly and composes
    its generic concatenating observation, so it records raw per-simulant rows rather than the
    stratified measures the other public health observers produce.

    Configuration
    -------------
    Configured under this observer's name (e.g. ``microdata_observer``) in the model spec:

    columns
        The population columns/attributes to record. Required; an empty list is an error.
    filter
        A list of Pandas query strings, AND-combined, restricting which simulants are recorded.
        Empty (the default) records all simulants. NOTE: These filters could be applied in such 
        a way with a combination of ``single_random_sample`` such that a simulant is not tracked 
        for their entire existence in the tracked population.
    timesteps
        A list of dates; only timesteps whose event time matches one of them are recorded. Empty
        (the default) records every timestep.
    row_limit
        The *total* maximum number of rows across all observed timesteps. The per-timestep cap is
        ``row_limit // <number of observed timesteps>`` (floored), and each observed timestep records
        a fresh random sample of up to that many simulants. None (the default) applies no cap.
    single_random_sample
        If True, sample a fixed *closed cohort* (of the per-timestep-cap size) once from the initial
        population and record only those simulants each observed timestep, instead of resampling.
        Requires ``row_limit``. Members are never added, and are dropped without replacement when
        they leave the filter or simulation, so ``row_limit`` stays an upper bound. False by default.
    """

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        config = super().configuration_defaults
        config[self.name] = {
            "columns": [],
            "filter": [],
            "timesteps": [],
            "row_limit": None,
            "single_random_sample": False,
        }
        return config

    def setup(self, builder: Builder) -> None:
        self.randomness = builder.randomness.get_stream(self.name)
        self.cohort: pd.Index[int] | None = None

    def register_observations(self, builder: Builder) -> None:
        config = builder.configuration[self.name]
        columns = list(config.columns)
        if not columns:
            raise ResultsConfigurationError(
                f"The '{self.name}' observer requires a non-empty 'columns' list in its config."
            )
        observed_dates = [pd.Timestamp(timestep).normalize() for timestep in config.timesteps]
        unique_dates = list(dict.fromkeys(observed_dates))
        if len(unique_dates) != len(observed_dates):
            duplicates = sorted(
                {
                    date.strftime("%Y-%m-%d")
                    for date in observed_dates
                    if observed_dates.count(date) > 1
                }
            )
            logger.warning(
                f"The '{self.name}' observer's 'timesteps' contains duplicate dates "
                f"{duplicates}; each is recorded only once."
            )
        observed_dates = unique_dates
        if config.single_random_sample and config.row_limit is None:
            raise ResultsConfigurationError(
                f"The '{self.name}' observer's 'single_random_sample' requires a 'row_limit' "
                "to define the closed cohort's size; set 'row_limit' or disable "
                "'single_random_sample'."
            )
        gatherer_kwargs: dict[str, Any] = {}
        if config.row_limit is not None:
            n_observed_timesteps = self._count_observed_timesteps(builder, observed_dates)
            if config.row_limit < n_observed_timesteps:
                if observed_dates:
                    detail = (
                        f"the number of configured timesteps ({n_observed_timesteps}); "
                        "list fewer 'timesteps' or increase row_limit."
                    )
                else:
                    detail = (
                        f"the number of timesteps the simulation runs ({n_observed_timesteps}); "
                        "increase row_limit, or set 'timesteps' to fewer specific dates."
                    )
                raise ResultsConfigurationError(
                    f"The '{self.name}' observer's row_limit ({config.row_limit}) would record "
                    f"zero rows per timestep: it is smaller than {detail}"
                )
            self.max_rows_per_timestep = config.row_limit // n_observed_timesteps
            if config.single_random_sample:
                builder.population.register_initializer(self._sample_cohort, columns=None)
                gatherer_kwargs["results_gatherer"] = self._cohort_rows
            else:
                gatherer_kwargs["results_gatherer"] = self._sample_rows
        builder.results.register_concatenating_observation(
            name=self.name,
            requires_attributes=columns,
            pop_filter=" and ".join(f"({condition})" for condition in list(config.filter)),
            to_observe=self._build_to_observe(observed_dates),
            **gatherer_kwargs,
        )

    def _build_to_observe(
        self, observed_dates: list[pd.Timestamp]
    ) -> Callable[[Event], bool]:
        """Build a predicate that observes only on the configured timesteps."""
        if not observed_dates:
            return lambda event: True
        target_times = set(observed_dates)
        return lambda event: pd.Timestamp(event.time).normalize() in target_times

    def _sample_index(
        self, index: pd.Index[int], additional_key: str | None = None
    ) -> pd.Index[int]:
        """Randomly draw up to ``max_rows_per_timestep`` simulants from ``index``."""
        draws = self.randomness.get_draw(index, additional_key=additional_key)
        return draws.nlargest(self.max_rows_per_timestep).index

    def _sample_rows(self, pop: pd.DataFrame) -> pd.DataFrame:
        """Record a fresh random sample of at most ``max_rows_per_timestep`` simulants."""
        if len(pop) <= self.max_rows_per_timestep:
            return pop
        return pop.loc[self._sample_index(pop.index)]

    def _sample_cohort(self, pop_data: SimulantData) -> None:
        """Sample the fixed closed cohort once, from the initial population."""
        if self.cohort is None:
            self.cohort = self._sample_index(
                pop_data.index, additional_key="cohort_selection"
            )

    def _cohort_rows(self, pop: pd.DataFrame) -> pd.DataFrame:
        """Record the once-sampled closed cohort still present in the (filtered) population."""
        if self.cohort is None:
            raise RuntimeError(
                f"The '{self.name}' observer's closed cohort was never sampled; its "
                "population initializer did not run before results were gathered."
            )
        return pop.loc[pop.index.intersection(self.cohort)]

    def _count_observed_timesteps(
        self, builder: Builder, observed_dates: list[pd.Timestamp]
    ) -> int:
        """Count the timesteps this observer records on."""
        if observed_dates:
            return len(observed_dates)
        time = builder.configuration.time
        start = pd.Timestamp(**time.start.to_dict())
        end = pd.Timestamp(**time.end.to_dict())
        step_size = pd.Timedelta(builder.time.step_size()())
        return max(1, math.ceil((end - start) / step_size))
