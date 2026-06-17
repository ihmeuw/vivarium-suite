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

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from vivarium.config_tree.main import ConfigTree

from vivarium.engine import Component
from vivarium.engine.framework.results.exceptions import ResultsConfigurationError

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder


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

    A black-box observer: at each observed timestep it records the columns named in the model spec
    for every simulant, concatenated across timesteps, so results scientists can compute derived
    quantities downstream. The columns to record are configured under the ``columns`` key of this
    observer's configuration block.
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
                f"The '{self.name}' observer requires a non-empty 'columns' list."
            )
        builder.results.register_microdata_observation(name=self.name, columns=columns)
