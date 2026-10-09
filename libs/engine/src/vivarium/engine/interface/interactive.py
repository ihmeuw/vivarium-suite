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

import re
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, overload

import numpy as np
import pandas as pd

from vivarium.engine.exceptions import WatchError
from vivarium.engine.framework.engine import SimulationContext
from vivarium.engine.framework.lifecycle import lifecycle_states
from vivarium.engine.framework.randomness.stream import RandomnessStream
from vivarium.engine.framework.resource.resource import Column
from vivarium.engine.interface.utilities import log_progress, run_from_ipython

_UNSEARCHABLE_RESOURCE_TYPES = frozenset(
    {Column.RESOURCE_TYPE, RandomnessStream.RESOURCE_TYPE}
)
"""Resource types :meth:`InteractiveContext.find_resources` does not report."""

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any

    from vivarium.config_tree.main import ConfigTree

    from vivarium.engine import Component
    from vivarium.engine.framework.event import Event
    from vivarium.engine.framework.values import AttributePipeline, Pipeline
    from vivarium.engine.types import ClockStepSize, ClockTime


@dataclass
class _Watch:
    """A registered watch expression and the values it has recorded."""

    func: Callable[[InteractiveContext], Any]
    values: dict[ClockTime, Any] = field(default_factory=dict)


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
        self._watches: dict[str, _Watch] = {}

        if setup:
            self.setup()

    @property
    def current_time(self) -> ClockTime:
        """Returns the current simulation time."""
        return self._clock.time

    @property
    def watches(self) -> dict[str, dict[ClockTime, Any]]:
        """Return a copy of the recorded watch values."""
        return {name: dict(watch.values) for name, watch in self._watches.items()}

    def get_results(self) -> dict[str, pd.DataFrame]:
        """Get the formatted results, saying why there are none if gathering is off."""
        if not self._results.to_observe:
            self._logger.warning(
                "No results to return. An InteractiveContext does not observe "
                "results by default; pass observe=True to include them."
            )
        return super().get_results()

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

        Raises
        ------
        ValueError
            If ``step_size`` is not compatible with the clock's step size.
        WatchError
            If a watch expression raises after the step. The clock stays on the
            completed step.
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
        if self._watches:
            self._record_watches()

    def watch(self, **watches: Callable[[InteractiveContext], Any]) -> None:
        """Register named expressions to evaluate now and after every step.

        Each expression is called with this context and its return value is
        recorded under the current clock time, first when it is registered and
        then after every step taken. Values are stored
        exactly as returned. Registration is all or nothing: if any expression in
        the call is rejected or fails, none of them is registered.

        Parameters
        ----------
        watches
            Callables keyed by the name to record them under. Each takes this
            context and returns the value to record.

        Raises
        ------
        ValueError
            If a name is already watched, or the context has not been set up.
        TypeError
            If a value is not callable.
        WatchError
            If an expression raises when first evaluated. The original exception
            is chained.
        """
        if self._lifecycle.current_state == lifecycle_states.INITIALIZATION:
            raise ValueError(
                "Cannot watch a simulation that is not set up. Call setup() first."
            )
        duplicates = [name for name in watches if name in self._watches]
        if duplicates:
            raise ValueError(f"Already watching {duplicates}. Unwatch them first.")
        non_callables = [name for name, func in watches.items() if not callable(func)]
        if non_callables:
            raise TypeError(f"Watches {non_callables} are not callable.")

        values = self._evaluate_watches(watches)
        for name, func in watches.items():
            self._watches[name] = _Watch(func)
        self._store_watch_values(values)

    def unwatch(self, *names: str) -> None:
        """Stop watching the named expressions and drop their recorded values.

        Parameters
        ----------
        names
            The names of the watches to remove.

        Raises
        ------
        ValueError
            If any name is not watched, in which case nothing is removed.
        """
        unknown = [name for name in names if name not in self._watches]
        if unknown:
            raise ValueError(f"Not watching {unknown}.")
        for name in names:
            # pop with a default so a name repeated in the call is not an error
            self._watches.pop(name, None)

    def _record_watches(self) -> None:
        """Evaluate every registered watch and record the values under the current time."""
        funcs = {name: watch.func for name, watch in self._watches.items()}
        self._store_watch_values(self._evaluate_watches(funcs))

    def _evaluate_watches(
        self, watches: dict[str, Callable[[InteractiveContext], Any]]
    ) -> dict[str, Any]:
        """Evaluate each watch at the current time, raising WatchError on the first failure."""
        time = self.current_time
        values: dict[str, Any] = {}
        for name, func in watches.items():
            try:
                values[name] = func(self)
            except Exception as error:
                raise WatchError(
                    f"Watch '{name}' failed at time {time}: {error!r}"
                ) from error
        return values

    def _store_watch_values(self, values: dict[str, Any]) -> None:
        """Record each watch's value under the current clock time."""
        time = self.current_time
        for name, value in values.items():
            # A watch expression may have unwatched another watch while they ran.
            if name in self._watches:
                self._watches[name].values[time] = value

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
        """Run the simulation until a time is reached or a callable evaluates to True.

        A time target is treated as a callable that is True once the clock is at
        or past it, bounded by the steps needed to get there, so a time at or
        before the current time takes no steps; a time before it also logs a
        warning. A callable is called before the
        first step and after every step, and the run stops on the first step
        where it returns True. Either way, how many steps were taken is logged at
        the INFO level.

        Parameters
        ----------
        target
            The time to run until, compatible with the simulation clock (usually
            a pandas.Timestamp), or a callable that takes this context and
            returns a bool.
        with_logging
            Whether to show a progress bar. Only works in an IPython environment.
        max_steps
            The most steps a callable run may take, which may go past the
            configured end time. A value of None (the default) allows the steps
            remaining until the configured end time. Only valid with a callable.

        Returns
        -------
            Bool for whether the target was reached. A time target is always reached.

        Raises
        ------
        ValueError
            If ``max_steps`` is given with a time or is negative, or the time is
            not compatible with the simulation clock.
        TypeError
            If a callable returns something other than a bool.
        """
        if callable(target):
            condition = target
            if max_steps is None:
                max_steps = max(0, self._clock.time_steps_remaining)
            elif max_steps < 0:
                raise ValueError(f"max_steps must be zero or greater, but got {max_steps}.")
        else:
            if max_steps is not None:
                raise ValueError("max_steps only applies when the target is a callable.")
            if not (
                isinstance(target, type(self._clock.time))
                or isinstance(self._clock.time, type(target))
            ):
                raise ValueError(
                    f"Provided time must be compatible with {type(self._clock.time)}"
                )
            if target < self._clock.time:  # type: ignore [operator]
                self._logger.warning(
                    f"Target time {target} is before the current time "
                    f"{self._clock.time}; not stepping."
                )
            end_time = target
            condition = lambda sim: sim.current_time >= end_time  # type: ignore [operator]
            max_steps = max(0, int(ceil((end_time - self._clock.time) / self._clock.step_size)))  # type: ignore [operator, arg-type]

        reached = self._run_until_condition(condition, max_steps, with_logging)
        return reached

    def _run_until_condition(
        self,
        condition: Callable[[InteractiveContext], bool],
        max_steps: int,
        with_logging: bool,
    ) -> bool:
        """Step until the condition is true or max_steps runs out, and return whether it was met."""
        steps_taken, met = self._step_loop(
            max_steps,
            step_size=None,
            with_logging=with_logging,
            stop_when=lambda: self._check_condition(condition),
        )

        if met:
            self._logger.info(f"Target reached after {steps_taken} iterations")
        else:
            self._logger.info(
                f"Target not reached after {steps_taken} iterations (reached max_steps)"
            )
        return met

    def _check_condition(self, condition: Callable[[InteractiveContext], bool]) -> bool:
        """Evaluate a run_until condition, requiring that it return a bool."""
        result = condition(self)
        if not isinstance(result, (bool, np.bool_)):
            raise TypeError(
                f"A run_until condition must return a bool, but it returned "
                f"{type(result).__name__}."
            )
        return bool(result)

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
        """Take up to number_of_steps steps, stopping as soon as stop_when is true.

        stop_when is checked before the first step and after each step. Return the
        number of steps taken and whether stop_when was true.
        """
        progress = None
        if run_from_ipython() and with_logging:
            progress = log_progress(range(number_of_steps), name="Step")

        stopper = stop_when if callable(stop_when) else lambda: False
        steps_taken = 0
        stopped = stopper()
        try:
            while not stopped and steps_taken < number_of_steps:
                self.step(step_size)
                steps_taken += 1
                # Advance the bar only after the step, so it shows steps taken.
                if progress is not None:
                    next(progress)
                stopped = stopper()
        except Exception as error:
            if progress is not None:
                # Lets log_progress mark the bar as failed; it re-raises the error.
                progress.throw(error)
            raise
        if progress is not None:
            progress.close()
        return steps_taken, stopped

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

    def find_resources(self, pattern: str, *, regex: bool = False) -> pd.DataFrame:
        """Find simulation resources whose name or component matches a pattern.

        Use this to locate a value when you know roughly what it is called but not
        what kind of thing holds it. Each match reports its kind, which is what
        says how to reach it.

        Parameters
        ----------
        pattern
            The text to look for, matched case-insensitively against both a
            resource's name and the name of the component that registered it. It
            may appear anywhere in either name. An empty pattern matches everything.
        regex
            Whether to treat the pattern as a regular expression. It is matched
            literally by default, so a name copied out of an earlier result always
            finds itself even when it contains characters a regular expression
            would read as syntax.

        Returns
        -------
            A frame of matching resources with columns ``name``, ``resource_type``
            and ``component``, one row per resource, sorted by name and then
            resource type.

        Raises
        ------
        ValueError
            If ``regex`` is True and the pattern is not a valid regular expression.

        Notes
        -----
        Columns and randomness streams are left out. A column is a component's
        private store whose public face is an attribute of the same name, so
        reporting it adds a near-duplicate row without adding information, and a
        stream is upstream of the values it randomizes rather than one of them.

        Modifiers of both attribute and value pipelines appear under the resource
        type ``value_modifier``. There is no accessor for one; read it by printing
        the pipeline it modifies.

        The resource graph holds no combiners or post-processors, so this cannot
        find those; printing a pipeline you have already found reports them.
        """
        try:
            matcher = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        except re.error as error:
            raise ValueError(
                f"Invalid regular expression '{pattern}': {error}. Drop regex=True to"
                " search for this text literally."
            ) from error

        rows = []
        for resource in self._resource.get_graph().nodes:
            if resource.RESOURCE_TYPE in _UNSEARCHABLE_RESOURCE_TYPES:
                continue
            resource_name = str(resource.name)
            component_name = resource.component.name
            if matcher.search(resource_name) or matcher.search(component_name):
                rows.append((resource_name, resource.RESOURCE_TYPE, component_name))

        return pd.DataFrame(rows, columns=["name", "resource_type", "component"]).sort_values(
            ["name", "resource_type"], ignore_index=True
        )

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
