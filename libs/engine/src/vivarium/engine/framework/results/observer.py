"""
=========
Observers
=========

An observer is a component that is responsible for registering
:class:`observations <vivarium.engine.framework.results.observation.Observation>`
to the simulation.

The provided :class:`Observer` class is an abstract base class that should be subclassed
by concrete observers. Each concrete observer is required to implement a
`register_observations` method that registers all required observations.

"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pandas as pd
from vivarium.config_tree.main import ConfigTree

from vivarium.engine import Component
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder
    from vivarium.engine.framework.event import Event


class Observer(Component, ABC):
    """An abstract base class intended to be subclassed by observer components.

    Notes
    -----
        A `register_observation` method must be defined in the subclass.

    """

    def __init__(self) -> None:
        super().__init__()
        self.results_dir = None

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        return {
            "stratification": {
                self.get_configuration_name(): {
                    "exclude": [],
                    "include": [],
                },
            },
        }

    def get_configuration_name(self) -> str:
        """Returns the name of a concrete observer for use in the configuration"""
        return self.name.split("_observer")[0]

    def get_configuration(self, builder: Builder) -> ConfigTree:
        return builder.configuration.get_tree(
            ["stratification", self.get_configuration_name()]
        )

    @abstractmethod
    def register_observations(self, builder: Builder) -> None:
        """Registers observations with within each observer."""
        pass

    def setup_component(self, builder: Builder) -> None:
        """Sets up the observer component."""
        with builder.components._tracking_setup(self):
            super().setup_component(builder)
            self.register_observations(builder)
            self.set_results_dir(builder)

    def set_results_dir(self, builder: Builder) -> None:
        """Defines the results directory from the configuration."""
        self.results_dir = (
            builder.configuration.to_dict()
            .get("output_data", {})
            .get("results_directory", None)
        )


class MicrodataObserver(Observer):
    """Observer that records a configured set of columns for each simulant.

    At each observed timestep it records the configured columns for every (optionally filtered)
    simulant, concatenated across timesteps, so results scientists can compute derived quantities
    downstream.

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

    def register_observations(self, builder: Builder) -> None:
        config = builder.configuration[self.name]
        columns = list(config.columns)
        if not columns:
            raise ResultsConfigurationError(
                f"The '{self.name}' observer requires a non-empty 'columns' list."
            )
        timesteps = list(config.timesteps)
        max_rows_per_timestep = None
        sampler = None
        if config.row_limit is not None:
            n_observed_timesteps = self._count_observed_timesteps(builder, timesteps)
            if config.row_limit < n_observed_timesteps:
                raise ResultsConfigurationError(
                    f"The '{self.name}' observer's row_limit ({config.row_limit}) is smaller than "
                    f"the number of observed timesteps ({n_observed_timesteps}), which would record "
                    "zero rows per timestep. Increase row_limit or reduce 'timesteps'."
                )
            max_rows_per_timestep = config.row_limit // n_observed_timesteps
            sampler = builder.randomness.get_stream(self.name).get_draw
        builder.results.register_microdata_observation(
            name=self.name,
            columns=columns,
            pop_filter=self._build_pop_filter(list(config.filter)),
            max_rows_per_timestep=max_rows_per_timestep,
            sampler=sampler,
            to_observe=self._build_to_observe(timesteps),
        )

    def _build_pop_filter(self, filters: list[str]) -> str:
        """Combine row filters into a single AND-joined pop_filter query."""
        return " and ".join(f"({condition})" for condition in filters)

    def _build_to_observe(self, timesteps: list[str]) -> Callable[[Event], bool]:
        """Build a predicate that observes only on the configured timesteps."""
        if not timesteps:
            return lambda event: True
        target_times = {pd.Timestamp(timestep).normalize() for timestep in timesteps}
        return lambda event: pd.Timestamp(event.time).normalize() in target_times

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
