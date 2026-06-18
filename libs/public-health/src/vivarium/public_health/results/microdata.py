"""
==================
Microdata Observer
==================

An observer that records a configured set of population columns for each simulant at each observed
timestep, concatenated across timesteps. Unlike the stratified public health observers, it records
raw per-simulant rows so that derived quantities can be derived downstream.

"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

from vivarium.public_health.results.observer import PublicHealthObserver

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder


class MicrodataObserver(PublicHealthObserver):
    """Observer that records a configured set of columns for each simulant.

    At each observed timestep it records the configured columns (plus ``event_time``) for every
    simulant, concatenated across timesteps. It composes the framework's generic concatenating
    observation, so it records raw rows rather than the stratified measures of the other public
    health observers (it does not use ``PublicHealthObserver``'s measure/entity formatting).

    Configuration
    -------------
    Configured under this observer's name (e.g. ``microdata_observer``) in the model spec:

    columns
        The population columns/attributes to record. Required; an empty list is an error.
    """

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        config = super().configuration_defaults
        config[self.name] = {"columns": []}
        return config

    def register_observations(self, builder: Builder) -> None:
        columns = list(builder.configuration[self.name].columns)
        if not columns:
            raise ResultsConfigurationError(
                f"The '{self.name}' observer configuration requires a non-empty 'columns'"
            )
        builder.results.register_concatenating_observation(
            name=self.name, requires_attributes=columns
        )
