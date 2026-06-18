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
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

from vivarium.public_health.results.observer import PublicHealthObserver

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder
    from vivarium.engine.framework.event import Event


class MicrodataObserver(PublicHealthObserver):
    """Observer that records a configured set of columns for each simulant.

    At each observed timestep it records the configured columns (plus ``event_time``) for every
    (optionally filtered) simulant, concatenated across timesteps, so results scientists can compute
    derived quantities downstream. It composes the framework's generic concatenating observation, so
    it records raw rows rather than the stratified measures of the other public health observers (it
    does not use ``PublicHealthObserver``'s measure/entity formatting).

    Configuration
    -------------
    Configured under this observer's name (e.g. ``microdata_observer``) in the model spec:

    columns
        The population columns/attributes to record. Required; an empty list is an error.
    filter
        A list of Pandas query strings, AND-combined, restricting which simulants are recorded.
        Empty (the default) records all simulants.
    timesteps
        A list of dates; only timesteps whose event time matches one of them are recorded. Empty
        (the default) records every timestep.
    row_limit
        The *total* maximum number of rows across all observed timesteps. The per-timestep cap is
        ``row_limit // <number of observed timesteps>`` (floored), and each observed timestep records
        a fresh random sample of up to that many simulants. None (the default) applies no cap.
    """

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        config = super().configuration_defaults
        config[self.name] = {
            "columns": [],
            "filter": [],
            "timesteps": [],
            "row_limit": None,
        }
        return config

    def setup(self, builder: Builder) -> None:
        self.randomness = builder.randomness.get_stream(self.name)

    def register_observations(self, builder: Builder) -> None:
        config = builder.configuration[self.name]
        columns = list(config.columns)
        if not columns:
            raise ResultsConfigurationError(
                f"The '{self.name}' observer configuration requires a non-empty 'columns'"
            )
        timesteps = list(config.timesteps)
        results_gatherer = None
        if config.row_limit is not None:
            n_observed_timesteps = self._count_observed_timesteps(builder, timesteps)
            if config.row_limit < n_observed_timesteps:
                raise ResultsConfigurationError(
                    f"The '{self.name}' observer's row_limit ({config.row_limit}) is smaller than "
                    f"the number of observed timesteps ({n_observed_timesteps}), which would record "
                    "zero rows per timestep. Increase row_limit or reduce 'timesteps'."
                )
            self.max_rows_per_timestep = config.row_limit // n_observed_timesteps
            results_gatherer = self._sample_rows
        builder.results.register_concatenating_observation(
            name=self.name,
            requires_attributes=columns,
            pop_filter=" and ".join(f"({condition})" for condition in list(config.filter)),
            to_observe=self._build_to_observe(timesteps),
            results_gatherer=results_gatherer,
        )

    def _build_to_observe(self, timesteps: list[str]) -> Callable[[Event], bool]:
        """Build a predicate that observes only on the configured timesteps."""
        if not timesteps:
            return lambda event: True
        target_times = {pd.Timestamp(timestep).normalize() for timestep in timesteps}
        return lambda event: pd.Timestamp(event.time).normalize() in target_times

    def _sample_rows(self, pop: pd.DataFrame) -> pd.DataFrame:
        """Record a fresh random sample of at most ``max_rows_per_timestep`` simulants."""
        if len(pop) <= self.max_rows_per_timestep:
            return pop
        # Give each simulant a random draw (clock-keyed, so reproducible yet re-drawn each
        # timestep) and keep the max_rows_per_timestep simulants with the largest draws.
        draws = self.randomness.get_draw(pop.index)
        return pop.loc[draws.nlargest(self.max_rows_per_timestep).index]

    def _count_observed_timesteps(self, builder: Builder, timesteps: list[str]) -> int:
        """Count the timesteps this observer records on.

        When ``timesteps`` is configured this is exact; otherwise the observer records every
        step and the count is estimated from the simulation's time configuration (the
        ``row_limit`` is an upper bound, so an estimate is acceptable here).
        """
        if timesteps:
            return len(timesteps)
        time = builder.configuration.time
        start = pd.Timestamp(**time.start.to_dict())
        end = pd.Timestamp(**time.end.to_dict())
        step_size = pd.Timedelta(builder.time.step_size()())
        return max(1, math.ceil((end - start) / step_size))
