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

import numpy as np
import pandas as pd

from vivarium.engine.framework.engine import SimulationContext
from vivarium.engine.interface.utilities import log_progress, run_from_ipython

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path
    from typing import Any

    from vivarium.config_tree.main import ConfigTree

    from vivarium.engine import Component
    from vivarium.engine.framework.event import Event
    from vivarium.engine.framework.values import AttributePipeline, Pipeline
    from vivarium.engine.types import ClockStepSize, ClockTime


class InteractiveContext(SimulationContext):
    """A simulation context with helper methods for running simulations interactively.

    Does not observe results by default; pass ``observe=True`` to include them.
    """

    def __init__(
        self,
        model_specification: str | Path | ConfigTree | None = None,
        components: list[Component] | dict[str, Any] | ConfigTree | None = None,
        configuration: dict[str, Any] | ConfigTree | None = None,
        plugin_configuration: dict[str, Any] | ConfigTree | None = None,
        sim_name: str | None = None,
        logging_verbosity: int = 0,
        *,
        observe: bool = False,
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
            Components to include in addition to the specification's. A dict or
            ``ConfigTree`` merges into the specification's ``components`` block
            instead, replacing the keys it names. A value of None will use the
            specification's components without change.
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
        observe
            Whether to observe results. A value of False (the default) prevents
            observations from being recorded and results generated.
        setup
            Whether to set the simulation up on construction. A value of True
            (the default) freezes the configuration; pass False to change
            configuration before setting up.
        """
        super().__init__(
            model_specification=model_specification,
            components=components,
            configuration=configuration,
            plugin_configuration=plugin_configuration,
            sim_name=sim_name,
            logging_verbosity=logging_verbosity,
        )

        self._results.set_to_observe(observe)

        if setup:
            self.setup()

    def get_results(self) -> dict[str, pd.DataFrame]:
        """Get the formatted results, saying why there are none if gathering is off."""
        if not self._results.to_observe:
            self._logger.warning(
                "No results to return. An InteractiveContext does not observe "
                "results by default; pass observe=True to include them."
            )
        return super().get_results()

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
            Whether to show a progress bar. Only works in an IPython environment.
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
            Whether to show a progress bar. Only works in an IPython environment.
        """
        if isinstance(duration, str):
            duration = pd.Timedelta(duration)
        self.run_until(self._clock.time + duration, with_logging=with_logging)  # type: ignore [operator]

    def run_until(
        self,
        target: ClockTime | Callable[[InteractiveContext], bool],
        with_logging: bool = True,
        *,
        max_steps: int | None = None,
    ) -> bool:
        """Run the simulation until a time is reached or a condition becomes true.

        A time target steps until the clock is at or past it, rounding up to the
        next step boundary; a time at or before the current time takes no steps.
        A condition is checked before the first step and after every step, and
        the run stops on the first step where it is true. Either way, how many
        steps were taken is logged at the INFO level.

        Parameters
        ----------
        target
            The time to run until, compatible with the simulation clock (usually
            a pandas.Timestamp), or a condition that takes this context and
            returns a bool.
        with_logging
            Whether to show a progress bar. Only works in an IPython environment.
        max_steps
            The most steps a condition run may take, which may go past the
            configured end time. A value of None (the default) allows the steps
            remaining until the configured end time. Only valid with a condition.

        Returns
        -------
            Bool for whether the target was reached. A time target is always reached.

        Raises
        ------
        ValueError
            If ``max_steps`` is given with a time, or the time is not compatible
            with the simulation clock.
        TypeError
            If a condition returns something other than a bool.
        """
        if callable(target):
            reached = self._run_until_condition(target, max_steps, with_logging)
        else:
            if max_steps is not None:
                raise ValueError("max_steps only applies when the target is a condition.")
            self._run_until_time(target, with_logging)
            reached = True
        return reached

    def _run_until_time(self, end_time: ClockTime, with_logging: bool) -> None:
        """Step until the clock is at or past end_time, taking no steps if it already is."""
        if not (
            isinstance(end_time, type(self._clock.time))
            or isinstance(self._clock.time, type(end_time))
        ):
            raise ValueError(
                f"Provided time must be compatible with {type(self._clock.time)}"
            )

        iterations = max(0, int(ceil((end_time - self._clock.time) / self._clock.step_size)))  # type: ignore [operator, arg-type]
        self.take_steps(number_of_steps=iterations, with_logging=with_logging)
        self._logger.info(f"Simulation complete after {iterations} iterations")

    def _run_until_condition(
        self,
        condition: Callable[[InteractiveContext], bool],
        max_steps: int | None,
        with_logging: bool,
    ) -> bool:
        """Step until the condition is true or max_steps runs out, and return whether it was met."""
        if max_steps is None:
            max_steps = max(0, self._clock.time_steps_remaining)

        if self._check_condition(condition):
            steps_taken, met = 0, True
        else:
            steps_taken, met = self._step_loop(
                max_steps,
                step_size=None,
                with_logging=with_logging,
                stop_when=lambda: self._check_condition(condition),
            )

        if met:
            self._logger.info(f"Condition met after {steps_taken} iterations")
        else:
            self._logger.info(
                f"Condition not met after {steps_taken} iterations (reached max_steps)"
            )
        return met

    def _check_condition(self, condition: Callable[[InteractiveContext], bool]) -> bool:
        """Evaluate a run_until condition, requiring that it return a bool."""
        result = condition(self)
        if isinstance(result, (bool, np.bool_)):
            return bool(result)
        if isinstance(result, (pd.Series, pd.DataFrame, np.ndarray)):
            raise TypeError(
                f"A run_until condition must return a bool, but it returned a value of "
                f"type {type(result).__name__}. Reduce it with .any() or .all()."
            )
        raise TypeError(
            f"A run_until condition must return a bool, but it returned a value of "
            f"type {type(result).__name__}."
        )

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
            Whether to show a progress bar. Only works in an IPython environment.
        """
        if not isinstance(number_of_steps, int):
            raise ValueError("Number of steps must be an integer.")
        self._step_loop(number_of_steps, step_size, with_logging)

    def _step_loop(
        self,
        number_of_steps: int,
        step_size: ClockStepSize | None,
        with_logging: bool,
        stop_when: Callable[[], bool] | None = None,
    ) -> tuple[int, bool]:
        """Take up to number_of_steps steps, stopping after any step where stop_when is true.

        Return the number of steps taken and whether stop_when ended the loop.
        """
        steps: Iterable[int] = range(number_of_steps)
        if run_from_ipython() and with_logging:
            steps = log_progress(range(number_of_steps), name="Step")

        steps_taken = 0
        for _ in steps:
            self.step(step_size)
            steps_taken += 1
            if stop_when is not None and stop_when():
                return steps_taken, True
        return steps_taken, False

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
                "Are you looking for an attribute pipeline? Try get_attribute()."
            )
        return self._values.get_value(value_pipeline_name)

    def get_attribute(self, attribute_pipeline_name: str) -> AttributePipeline:
        """Get the attribute pipeline associated with the given name.

        Printing the returned pipeline, or echoing it in a notebook cell, reports
        its source, modifiers, combiner, and post-processors.

        Parameters
        ----------
        attribute_pipeline_name
            Name of the attribute pipeline to return. Available names are given
            by :meth:`get_attribute_names`.

        Returns
        -------
            The requested attribute pipeline.

        Raises
        ------
        ValueError
            If no attribute pipeline of that name is registered.
        """
        # Guarded because the values manager creates a pipeline for an unknown
        # name, which would silently pollute the simulation with an empty one.
        if attribute_pipeline_name not in self.get_attribute_names():
            raise ValueError(
                f"No attribute pipeline '{attribute_pipeline_name}' registered. "
                "Are you looking for a value pipeline? Try get_value()."
            )
        return self._values.get_attribute(attribute_pipeline_name)

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
