"""
==========================
Vivarium Interactive Tools
==========================

This module provides an interface for interactive simulation usage. The main
part is the :class:`InteractiveContext`, a sub-class of the main simulation
object in ``vivarium`` that has been extended to include convenience
methods for running and exploring the simulation in an interactive setting.

See the associated tutorials for :ref:`running <interactive_tutorial>` and
:ref:`exploring <exploration_tutorial>` for more information.

"""
from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, overload

import pandas as pd

from vivarium.engine.framework.engine import SimulationContext
from vivarium.engine.framework.results.observer import Observer
from vivarium.engine.interface.utilities import log_progress, run_from_ipython

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any

    from vivarium.config_tree.main import ConfigTree

    from vivarium.engine import Component
    from vivarium.engine.framework.event import Event
    from vivarium.engine.framework.values import Pipeline
    from vivarium.engine.types import ClockStepSize, ClockTime


class InteractiveContext(SimulationContext):
    """A simulation context with helper methods for running simulations interactively."""

    def __init__(
        self,
        model_specification: str | Path | ConfigTree | None = None,
        components: list[Component] | dict[str, Any] | ConfigTree | None = None,
        configuration: dict[str, Any] | ConfigTree | None = None,
        plugin_configuration: dict[str, Any] | ConfigTree | None = None,
        sim_name: str | None = None,
        logging_verbosity: int = 0,
        *,
        include_observers: bool = False,
        setup: bool = True,
    ) -> None:
        """Create an interactive simulation context.

        Parameters
        ----------
        model_specification
            Path to a model specification yaml, or an already-parsed ``ConfigTree``.
            A path must exist, end in ``.yaml`` or ``.yml``, and use only the
            top-level keys ``plugins``, ``components``, and ``configuration``.
            A value of None will build the simulation from the other arguments alone.
        components
            Components to include in this simulation. A list is appended to the
            components the specification declares while a dict or ``ConfigTree``
            overrides the specification's ``components`` block. A value of None
            will use the specification's components without change.
        configuration
            Values overriding the specification's ``configuration`` block. A value
            of None will use the specification's configuration without change.
        plugin_configuration
            Managers overriding the specification's ``plugins`` block. A value of
            None will use the specification's plugin configuration without change.
        sim_name
            Name for this context, used to label its log records. Must be unique
            within the process. A value of None names the context ``simulation_<n>``
            by how many have been created so far.
        logging_verbosity
            How much to log. A value of 0 (the default) logs warnings and errors, 1
            logs INFO-level messages, and 2+ logs DEBUG-level messages.
            Note that only the first context built in a process configures logging
            and subsequent contexts will inherit that configuration.
        include_observers
            Whether to include the observers the specification declares. A value
            of False (the default) leaves them out and does not gather results.
        setup
            Whether to set the simulation up on construction. A value of True
            (the default) freezes the configuration; pass False to change
            configuration before setting up.
        """
        # NOTE: Must assign before super().__init__() because it calls add_components()
        # which is overridden below and reads this.
        self._include_observers = include_observers

        super().__init__(
            model_specification=model_specification,
            components=components,
            configuration=configuration,
            plugin_configuration=plugin_configuration,
            sim_name=sim_name,
            logging_verbosity=logging_verbosity,
        )

        if setup:
            self.setup()

    def add_components(self, component_list: list[Component]) -> None:
        """Add components, dropping observers unless they were asked for.

        Excluding by type here rather than filtering ``component_list`` catches an
        observer declared as another component's subcomponent, which is only
        visible once the manager flattens them.
        """
        exclude_types = () if self._include_observers else (Observer,)
        self._component_manager.add_components(component_list, exclude_types)

    def get_results(self) -> dict[str, pd.DataFrame]:
        """Get the formatted results, warning if observers were excluded.

        Empty results and a simulation where nothing happened look the same to a
        caller, so say which one this is.
        """
        results = super().get_results()
        if not results and not self._include_observers:
            self._logger.warning(
                "No results to return. Observers are excluded from an"
                " InteractiveContext by default; pass include_observers=True to"
                " keep them."
            )
        return results

    @property
    def current_time(self) -> ClockTime:
        """Returns the current simulation time."""
        return self._clock.time

    def setup(self) -> None:
        super().setup()
        self.initialize_simulants()
        self._population_view = self._builder.population.get_view()

    def step(self, step_size: ClockStepSize | None = None) -> None:
        """Advance the simulation one step.

        Parameters
        ----------
        step_size
            An optional size of step to take. Must be compatible with the
            simulation clock's step size (usually a pandas.Timedelta).
        """
        old_step_size = self._clock._clock_step_size
        if step_size is not None:
            if not (
                isinstance(step_size, type(self._clock.step_size))
                or isinstance(self._clock.step_size, type(step_size))
            ):
                raise ValueError(
                    f"Provided time must be compatible with {type(self._clock.step_size)}"
                )
            self._clock._clock_step_size = step_size
        super().step()
        self._clock._clock_step_size = old_step_size

    def run(self, with_logging: bool = True) -> None:  # type: ignore [override]
        """Run the simulation for the duration specified in the configuration.

        Parameters
        ----------
        with_logging
            Whether or not to log the simulation steps. Only works in an ipython
            environment.

        Returns
        -------
            The number of steps the simulation took.
        """
        self.run_until(self._clock.stop_time, with_logging=with_logging)

    def run_for(self, duration: ClockStepSize | str, with_logging: bool = True) -> None:
        """Run the simulation for the given time duration.

        Parameters
        ----------
        duration
            The length of time to run the simulation for. Should be compatible
            with the simulation clock's step size (usually a pandas
            Timedelta). If a string is provided, it will be passed to
            `pandas.Timedelta` to be converted.
        with_logging
            Whether or not to log the simulation steps. Only works in an ipython
            environment.

        Returns
        -------
            The number of steps the simulation took.
        """
        if isinstance(duration, str):
            duration = pd.Timedelta(duration)
        self.run_until(self._clock.time + duration, with_logging=with_logging)  # type: ignore [operator]

    def run_until(self, end_time: ClockTime, with_logging: bool = True) -> None:
        """Run the simulation until the provided end time.

        Parameters
        ----------
        end_time
            The time to run the simulation until. The simulation will run until
            its clock is greater than or equal to the provided end time. Must be
            compatible with the simulation clock's step size (usually a pandas.Timestamp)

        with_logging
            Whether or not to log the simulation steps. Only works in an ipython
            environment.

        Returns
        -------
            The number of steps the simulation took.
        """
        if not (
            isinstance(end_time, type(self._clock.time))
            or isinstance(self._clock.time, type(end_time))
        ):
            raise ValueError(
                f"Provided time must be compatible with {type(self._clock.time)}"
            )

        iterations = int(ceil((end_time - self._clock.time) / self._clock.step_size))  # type: ignore [operator, arg-type]
        self.take_steps(number_of_steps=iterations, with_logging=with_logging)
        assert self._clock.time - self._clock.step_size < end_time <= self._clock.time  # type: ignore [operator]
        print("Simulation complete after", iterations, "iterations")

    def take_steps(
        self,
        number_of_steps: int = 1,
        step_size: ClockStepSize | None = None,
        with_logging: bool = True,
    ) -> None:
        """Run the simulation for the given number of steps.

        Parameters
        ----------
        number_of_steps
            The number of steps to take.
        step_size
            An optional size of step to take. Must be compatible with the
            simulation clock's step size (usually a pandas.Timedelta).
        with_logging
            Whether or not to log the simulation steps. Only works in an ipython
            environment.
        """
        if not isinstance(number_of_steps, int):
            raise ValueError("Number of steps must be an integer.")

        if run_from_ipython() and with_logging:
            for _ in log_progress(range(number_of_steps), name="Step"):
                self.step(step_size)
        else:
            for _ in range(number_of_steps):
                self.step(step_size)

    @overload
    def get_population(
        self,
        attributes: str,
        query: str = "",
        include_untracked: bool = False,
    ) -> pd.Series[Any] | pd.DataFrame:
        ...

    @overload
    def get_population(
        self,
        attributes: list[str] | tuple[str, ...],
        query: str = "",
        include_untracked: bool = False,
    ) -> pd.DataFrame:
        ...

    def get_population(
        self,
        attributes: str | list[str] | tuple[str, ...],
        query: str = "",
        include_untracked: bool = False,
    ) -> pd.Series[Any] | pd.DataFrame:
        """Get a copy of the population state table.

        Parameters
        ----------
        attributes
            The attribute pipelines to include in the returned table.
        query
            Additional conditions used to filter the index.
        include_untracked
            Whether to include untracked simulants.

        Returns
        -------
            The current state of requested population attributes.
        """
        index = self.get_population_index()
        if isinstance(attributes, str):
            try:
                return self._population_view.get(
                    index, attributes, query, include_untracked=include_untracked
                )
            except ValueError as e:
                if "call `get_frame()` instead" in str(e):
                    return self._population_view.get_frame(
                        index, attributes, query, include_untracked=include_untracked
                    )
                raise
        return self._population_view.get(
            index, attributes, include_untracked=include_untracked
        )

    def get_attribute_names(self) -> list[str]:
        """List all attributes in the population state table."""
        return self._population.get_all_attribute_names()

    def list_values(self) -> list[str]:
        """List the names of all value pipelines in the simulation."""
        return list(self._values.get_value_pipelines().keys())

    def get_value(self, value_pipeline_name: str) -> Pipeline:
        """Get the value pipeline associated with the given name."""
        if value_pipeline_name not in self.list_values():
            raise ValueError(
                f"No value pipeline '{value_pipeline_name}' registered. "
                "Are you looking for an attribute pipeline?"
            )
        return self._values.get_value(value_pipeline_name)

    def list_events(self) -> list[str]:
        """List all event types registered with the simulation."""
        return self._events.list_events()

    def get_listeners(self, event_name: str) -> dict[int, list[Callable[[Event], None]]]:
        """Get all listeners of a particular type of event.

        Available events can be found by calling
        :func:`InteractiveContext.list_events`.

        Parameters
        ----------
        event_name
            The name of the event to grab the listeners for.

        Returns
        -------
            A dictionary that maps each priority level of the named event's
            listeners to a list of listeners at that level.
        """
        if event_name not in self._events:
            raise ValueError(f"No event {event_name} in system.")
        return self._events.get_listeners(event_name)

    def get_emitter(
        self, event_name: str
    ) -> Callable[[pd.Index[int], dict[str, Any] | None], None]:
        """Get the callable that emits the given type of events.

        Available events can be found by calling
        :func:`InteractiveContext.list_events`.

        Parameters
        ----------
        event_name
            The name of the event to grab the listeners for.

        Returns
        -------
            The callable that emits the named event.
        """
        if event_name not in self._events:
            raise ValueError(f"No event {event_name} in system.")
        return self._events.get_emitter(event_name)

    def list_components(self) -> dict[str, Any]:
        """Get a mapping of component names to components currently in the simulation.

        Returns
        -------
            A dictionary mapping component names to components.
        """
        return self._component_manager.list_components()

    def get_component(self, name: str) -> Any:
        """Get the component in the simulation that has ``name``, if present.
        Names are guaranteed to be unique.

        Parameters
        ----------
        name
            A component name.

        Returns
        -------
            A component that has the name ``name`` else None.
        """
        return self._component_manager.get_component(name)

    def print_initializer_order(self) -> None:
        """Print the order in which population initializers are called."""
        initializers = []
        for r in self._resource.get_population_initializers():
            name = r.__name__
            if hasattr(r, "__self__"):
                obj = r.__self__
                initializers.append(f"{obj.__class__.__name__}({obj.name}).{name}")
            else:
                initializers.append(f"Unbound function {name}")
        print("\n".join(initializers))

    def print_lifecycle_order(self) -> None:
        """Print the order of lifecycle events (including user event handlers)."""
        print(self._lifecycle)

    def __repr__(self) -> str:
        return "InteractiveContext()"
