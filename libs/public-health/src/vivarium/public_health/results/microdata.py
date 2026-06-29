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


class MicrodataObserver(Observer):
    """Observer that records a configured set of columns for each simulant.

    At each observed timestep it records the configured columns (plus ``event_time``) for every
    (optionally filtered) simulant, concatenated across timesteps, so users can compute
    derived quantities downstream. It directly composes the framework's generic 
    concatenating observation, so it records raw per-simulant rows rather than the
    stratified measures the other public health observers produce.

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
        self.max_rows_per_timestep: int | None = None
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
        query = " and ".join(f"({condition})" for condition in list(config.filter))
        builder.results.register_concatenating_observation(
            name=self.name,
            requires_attributes=columns,
            pop_filter=(query, self._sample_index),
            to_observe=self._build_to_observe(observed_dates),
        )

    def _build_to_observe(
        self, observed_dates: list[pd.Timestamp]
    ) -> Callable[[Event], bool]:
        """Build a predicate that observes only on the configured timesteps."""
        if not observed_dates:
            return lambda event: True
        target_times = set(observed_dates)
        return lambda event: pd.Timestamp(event.time).normalize() in target_times

    def _sample_index(self, index: pd.Index) -> pd.Index:
        """Keep a fresh random sample of at most ``max_rows_per_timestep`` simulants (all if no limit)."""
        if self.max_rows_per_timestep is None or len(index) <= self.max_rows_per_timestep:
            return index
        draws = self.randomness.get_draw(index)
        return draws.nlargest(self.max_rows_per_timestep).index

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
