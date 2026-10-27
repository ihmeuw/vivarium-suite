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

import inspect
import re
import warnings
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, NamedTuple, overload

import numpy as np
import pandas as pd

from vivarium.engine.component import NUM_EVENT_PRIORITIES
from vivarium.engine.exceptions import ObservationError
from vivarium.engine.framework.engine import BaseSimulationContext
from vivarium.engine.framework.lifecycle import lifecycle_states
from vivarium.engine.framework.lookup.table import LookupTable
from vivarium.engine.framework.randomness.stream import RandomnessStream
from vivarium.engine.framework.resource.resource import Column, Initializer, Resource
from vivarium.engine.framework.values import AttributePipeline, Pipeline, ValueModifier
from vivarium.engine.interface.utilities import log_progress, run_from_ipython

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path
    from typing import Any, Literal

    import networkx as nx
    from vivarium.config_tree.main import ConfigTree

    from vivarium.engine import Component
    from vivarium.engine.types import ClockStepSize, ClockTime

_IGNORED_RESOURCE_TYPES = frozenset(
    {Column.RESOURCE_TYPE, RandomnessStream.RESOURCE_TYPE, Initializer.RESOURCE_TYPE}
)
"""Resource types to ignore.

These resource types are not very usable/debuggable in an interactive context, so
:meth:`InteractiveContext.find_resources` and :meth:`InteractiveContext.get_dependencies`
do not report them.
"""

_RESOURCE_TYPE_ORDER = (
    AttributePipeline.RESOURCE_TYPE,
    Pipeline.RESOURCE_TYPE,
    ValueModifier.RESOURCE_TYPE,
    LookupTable.RESOURCE_TYPE,
)
"""Resource types in the order :meth:`InteractiveContext.find_resources` ranks them."""

_RESOURCE_TYPE_RANK = {
    resource_type: index for index, resource_type in enumerate(_RESOURCE_TYPE_ORDER)
}

_WORD_SEPARATORS = frozenset({".", "_"})
"""Characters :meth:`InteractiveContext.find_resources` treats as word boundaries."""

_HELP_SECTIONS = (
    (
        "Run the simulation",
        ("setup", "step", "take_steps", "run_for", "run_until", "run", "current_time"),
    ),
    ("Inspect the population", ("get_population", "list_attributes", "get_attribute")),
    (
        "Find and inspect resources",
        (
            "find_resources",
            "list_values",
            "get_value",
            "get_lookup_table",
            "get_dependencies",
            "print_dependencies",
        ),
    ),
    (
        "Inspect components and scheduling",
        (
            "list_components",
            "get_component",
            "get_schedule",
            "print_lifecycle_order",
            "print_initializer_order",
        ),
    ),
    (
        "Observe and collect results",
        ("register_observation", "pause_observation", "get_results"),
    ),
)
"""Headings and the :class:`InteractiveContext` members that :meth:`InteractiveContext.help` lists under each."""


_DEPENDENCY_DIRECTIONS = ("uses", "used-by")
"""Directions :meth:`InteractiveContext.print_dependencies` can walk."""

_DEPENDENCY_TYPE_ORDER = (
    AttributePipeline.RESOURCE_TYPE,
    Pipeline.RESOURCE_TYPE,
    LookupTable.RESOURCE_TYPE,
    Column.RESOURCE_TYPE,
    RandomnessStream.RESOURCE_TYPE,
    Initializer.RESOURCE_TYPE,
    ValueModifier.RESOURCE_TYPE,
)
"""Resource types in the order :meth:`InteractiveContext.print_dependencies` draws them.

This is structural rather than the relevance ranking :data:`_RESOURCE_TYPE_ORDER`
applies: what feeds a resource comes first, then the storage and randomness it is
built out of, and last the modifiers layered over it.
"""

_DEPENDENCY_TYPE_RANK = {
    resource_type: index for index, resource_type in enumerate(_DEPENDENCY_TYPE_ORDER)
}


class InteractiveContext(BaseSimulationContext):
    """A simulation context with helper methods for running simulations interactively.

    Does not gather the model's results by default; pass ``observe=True`` to
    include them.

    Call :meth:`help` to list the available methods by task.
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
            Whether to gather the results of the observations the model's
            components register. A value of False (the default) prevents them
            from being gathered. Observations registered with
            :meth:`register_observation` are evaluated either way.
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
        self._interactive_observations: dict[str, _InteractiveObservation] = {}

        if setup:
            self.setup()

    def __repr__(self) -> str:
        return "InteractiveContext()"

    def help(self) -> None:
        """Print the methods available on this context, grouped by task."""
        width = max(len(name) for _, names in _HELP_SECTIONS for name in names) + 2
        sections = []
        for heading, names in _HELP_SECTIONS:
            lines = [heading]
            for name in names:
                member = getattr(type(self), name)
                label = name if isinstance(member, property) else f"{name}()"
                summary = (inspect.getdoc(member) or "").partition("\n")[0]
                lines.append(f"    {label:<{width}}  {summary}")
            sections.append("\n".join(lines))
        print("\n\n".join(sections))

    ######################
    # Run the simulation #
    ######################

    def setup(self) -> None:
        """Set up the simulation and create the initial population."""
        super().setup()
        self.initialize_simulants()
        self._population_view = self._builder.population.get_view()
        self._assert_no_duplicate_names(self._interactive_observations)
        self._observe()

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
        ObservationError
            If an observation registered with :meth:`register_observation` raises
            after the step. The clock stays on the completed step.
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
        self._observe()

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

    def run(self, with_logging: bool = True) -> None:
        """Run the simulation for the duration specified in the configuration.

        Parameters
        ----------
        with_logging
            Whether to show a progress bar. Only works in an IPython environment.
        """
        self.run_until(self._clock.stop_time, with_logging=with_logging)

    ##########################
    # Inspect the population #
    ##########################

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

    def list_attributes(self) -> list[str]:
        """List all attributes in the population state table."""
        return self._population.get_all_attribute_names()

    def get_attribute_names(self) -> list[str]:
        """Deprecated alias for :meth:`list_attributes`.

        .. deprecated:: 5.19.0
            Use :meth:`list_attributes` instead. This alias will be removed.
        """
        warnings.warn(
            "InteractiveContext.get_attribute_names is deprecated and will be removed; "
            "use InteractiveContext.list_attributes instead.",
            FutureWarning,
            stacklevel=2,
        )
        return self.list_attributes()

    def get_attribute(self, attribute_pipeline_name: str) -> AttributePipeline:
        """Get the attribute pipeline associated with the given name.

        Printing the returned pipeline, or echoing it in a notebook cell, reports
        its source, modifiers, combiner, and post-processors.

        Parameters
        ----------
        attribute_pipeline_name
            Name of the attribute pipeline to return. Available names are given
            by :meth:`list_attributes`.

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
        if attribute_pipeline_name not in self.list_attributes():
            raise ValueError(
                f"No attribute pipeline '{attribute_pipeline_name}' registered. "
                "Are you looking for a value pipeline? Try get_value()."
            )
        return self._values.get_attribute(attribute_pipeline_name)

    ##############################
    # Find and inspect resources #
    ##############################

    def find_resources(self, pattern: str = "", *, regex: bool = False) -> pd.DataFrame:
        """Find simulation resources whose name or component matches a pattern.

        Use this to locate a value when you know roughly what it is called but not
        what kind of thing holds it. Each match reports its kind, which is what
        says how to reach it.

        Parameters
        ----------
        pattern
            The text to look for, matched case-insensitively against both a
            resource's name and the name of the component that registered it. It
            may appear anywhere in either name. An empty pattern, the default,
            matches everything.
        regex
            Whether to treat the pattern as a regular expression. It is matched
            literally by default, so a name copied out of an earlier result always
            finds itself even when it contains characters a regular expression
            would read as syntax.

        Returns
        -------
            A frame of matching resources with columns ``name``, ``resource_type``
            and ``component``, one row per resource, most relevant first.

            Relevance is how squarely the pattern hit the name: the whole name,
            then the start of it, then whole dot-separated segments from the
            middle or the end, then whole underscore-separated words, then part
            of a word, and last the resources that matched only through their
            component. Ties break by resource type - attributes, values,
            modifiers, lookup tables - and then by name.

        Raises
        ------
        ValueError
            If ``regex`` is True and the pattern is not a valid regular expression.

        Notes
        -----
        Columns, randomness streams, and initializers are excluded.

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

        rows: list[_ResourceRow] = []
        for resource in self._resource.get_graph().nodes:
            if resource.RESOURCE_TYPE in _IGNORED_RESOURCE_TYPES:
                continue
            resource_name = str(resource.name)
            component_name = resource.component.name
            if matcher.search(resource_name) or matcher.search(component_name):
                rank = _match_rank(matcher, resource_name)
                rows.append(
                    _ResourceRow(rank, resource_name, resource.RESOURCE_TYPE, component_name)
                )

        rows.sort(
            key=lambda row: (
                row.rank,
                # A type absent from _RESOURCE_TYPE_ORDER sorts last rather than
                # being dropped, so a type added later still reports its real name.
                _RESOURCE_TYPE_RANK.get(row.resource_type, len(_RESOURCE_TYPE_RANK)),
                row.name,
            )
        )
        return pd.DataFrame(
            [(row.name, row.resource_type, row.component) for row in rows],
            columns=["name", "resource_type", "component"],
        )

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

    def get_lookup_table(
        self, name: str
    ) -> LookupTable[pd.Series[Any]] | LookupTable[pd.DataFrame]:
        """Get the lookup table associated with the given name.

        Calling the returned table with a population index returns the data it
        holds for those simulants.

        Parameters
        ----------
        name
            Name of the lookup table to return. Available names are the
            ``lookup_table`` rows of :meth:`find_resources`.

        Returns
        -------
            The requested lookup table.

        Raises
        ------
        ValueError
            If no lookup table of that name is registered.
        """
        if name not in self._tables.tables:
            raise ValueError(
                f"No lookup table '{name}' registered. A table is usually named "
                "for the component that built it, so its name may be longer than "
                "the quantity it holds; try find_resources()."
            )
        return self._tables.tables[name]

    def get_dependencies(
        self,
        resource: str | Resource,
        direction: Literal["uses", "used-by"],
    ) -> list[Resource]:
        """Get the direct resources a resource uses or the resources that use it.

        Use this to work with the dependencies themselves. :meth:`InteractiveContext.print_dependencies`
        walks the same graph but draws it as a tree to read.

        Parameters
        ----------
        resource
            The resource to start from, given as a ``Resource``, as a full
            resource id such as ``"attribute.age"``, or as a bare name such as
            ``"age"``. A bare name has to name exactly one resource.
        direction
            Which way to walk. ``"uses"`` reports the resources this one reads,
            and ``"used-by"`` reports the resources that read it.

        Returns
        -------
            The resources one level away, grouped by resource type in the order
            :meth:`InteractiveContext.print_dependencies` draws them and sorted
            by name within a type.

        Raises
        ------
        ValueError
            If ``direction`` is neither ``"uses"`` nor ``"used-by"``, or if
            ``resource`` does not name exactly one resource in the graph.

        Notes
        -----
        Columns, randomness streams, and initializers are left out (as they are
        from :meth:`InteractiveContext.find_resources`).

        Results report the dependencies a component explicitly declared. An undeclared
        dependency is missing here with no signal.
        """
        _validate_direction(direction)
        graph = self._resource.get_graph()
        root = _resolve_resource(graph, resource)
        return [
            dependency
            for dependency in _dependencies(graph, root, direction)
            if dependency.RESOURCE_TYPE not in _IGNORED_RESOURCE_TYPES
        ]

    def print_dependencies(
        self,
        resource: str | Resource,
        direction: Literal["uses", "used-by"],
        depth: int | None = 1,
    ) -> None:
        """Print what a resource uses or what uses it as a tree.

        Parameters
        ----------
        resource
            The resource to start from, given as a ``Resource``, as a full
            resource id such as ``"attribute.age"``, or as a bare name such as
            ``"age"``. A bare name has to name exactly one resource.
        direction
            Which way to walk. ``"uses"`` reports the resources this one reads,
            and ``"used-by"`` reports the resources that read it.
        depth
            How many levels to print. A value of None walks the whole chain.

        Raises
        ------
        ValueError
            If ``direction`` is neither ``"uses"`` nor ``"used-by"``, if
            ``depth`` is neither None nor at least 1, or if ``resource`` does
            not name exactly one resource in the graph.

        Notes
        -----
        A branch cut off by ``depth`` is marked ``(N direct branches hidden)``,
        where ``N`` counts its direct dependencies and not every resource below
        them.

        The resource graph is a directed acyclic graph rather than a tree, so a
        resource can be reached along several paths. Every parent shows it, but
        only the first arrival that expands it unrolls its dependencies. The
        rest are marked ``(expanded above)``. A resource first expanded deep in
        the tree, where ``depth`` cut off its own dependencies, keeps that mark
        when it is reached again higher up. The branch it points at can then
        hide more than the depth allows at the new position. Raise ``depth`` to
        see the rest.

        Every resource type is reported, including the ``column`` and ``initializer``
        nodes that :meth:`InteractiveContext.find_resources` leaves out. Those sit
        between an attribute and whatever its initializer reads, so a chain runs
        through them.

        Dependencies of one resource are grouped by type in the order defined by
        ``_DEPENDENCY_TYPE_ORDER`` (and then sorted by name); what feeds a resource
        comes first and the modifiers layered over it come last. This is a different
        order from the one :meth:`InteractiveContext.find_resources` ranks by, which
        sorts by how likely a match is to be the resource you are looking for.
        """
        _validate_direction(direction)
        if depth is not None and depth < 1:
            raise ValueError(
                f"A depth of {depth} is not valid. Use a depth of at least 1, or"
                " None to walk the whole chain."
            )

        graph = self._resource.get_graph()
        root = _resolve_resource(graph, resource)
        print("\n".join(_render_dependency_tree(graph, root, direction, depth)))

    #####################################
    # Inspect components and scheduling #
    #####################################

    def list_components(self) -> dict[str, Any]:
        """Get a mapping of component names to components currently in the simulation.

        Returns
        -------
            A dictionary mapping component names to components.
        """
        return self._component_manager.list_components()

    def get_component(self, name: str) -> Any:
        """Get the component in the simulation with the given name, if present.

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

    def get_schedule(self) -> pd.DataFrame:
        """Get what runs in the simulation, in the order they currently run.

        Use this to find out when a component's actions and observations run
        relative to the rest of the model. An "action" is a method, such as
        ``on_time_step``, that the simulation calls in a lifecycle state.

        Returns
        -------
            A frame with one row per action or observation, indexed by
            ``phase``, ``state`` and ``priority``, with columns
            ``component``, ``kind``, ``name`` and ``active``. ``kind`` is
            ``"action"``, ``"observation"`` or ``"observation (interactive)"``.
            ``name`` is the action's method name or the observation's name.
            ``active`` says whether the row runs in this context.

        Notes
        -----
        Phases and their lifecycle states run in the order shown, and within a
        state lower priorities run first. The states of the ``main_loop`` phase
        repeat every time step. Rows that share a state and priority have no
        guaranteed order. States with nothing in them are left out.

        Observations are listed even when results are not observed, which is the
        default for an interactive context. Their ``active`` value is then False.

        The results manager's own actions are left out. Its work is shown as the
        observations it makes, so the frame has the same rows whether or not
        results are observed.

        An active observation is checked every step but can still decline to run,
        through its to_observe condition or because its population filter leaves
        no simulants.

        Observations registered with :meth:`register_observation` are listed at
        the end of the ``main_loop`` phase with no state, priority or component.
        They run once when registered, or at the end of setup if registered
        before it, and then after every step. A paused one has ``active`` set to
        False.

        """
        # A row is phase, state, priority, component name, kind, action/observation name, active.
        rows: list[tuple[str, str | None, int | None, str | None, str, str, bool]] = []
        for phase in self._lifecycle.get_phase_names():
            for state in self._lifecycle.get_state_names(phase):
                # get_listeners() would create a channel for a state with none.
                listeners = self._events.get_listeners(state) if state in self._events else {}
                for priority in range(NUM_EVENT_PRIORITIES):
                    for (
                        observation,
                        component,
                    ) in self._results.get_scheduled_observations(state, priority):
                        rows.append(
                            (
                                phase,
                                state,
                                priority,
                                component.name,
                                "observation",
                                observation.name,
                                self._results.to_observe,
                            )
                        )
                    for listener in listeners.get(priority, []):
                        owner = getattr(listener, "__self__", None)
                        if owner is self._results:
                            # The results manager's work is listed as the
                            # observations above.
                            continue
                        rows.append(
                            (
                                phase,
                                state,
                                priority,
                                getattr(owner, "name", None),
                                "action",
                                getattr(listener, "__name__", type(listener).__name__),
                                True,
                            )
                        )
            # Add interactive observations to end of main loop phase
            if phase == "main_loop":
                for name, interactive_observation in self._interactive_observations.items():
                    rows.append(
                        (
                            phase,
                            None,
                            None,
                            None,
                            "observation (interactive)",
                            name,
                            interactive_observation.active,
                        )
                    )
        index = ["phase", "state", "priority"]
        return (
            pd.DataFrame(rows, columns=index + ["component", "kind", "name", "active"])
            .astype({"priority": "Int64"})
            .set_index(index)
        )

    def print_lifecycle_order(self) -> None:
        """Print the lifecycle phases and the states in each, in the order they run."""
        print(self._lifecycle)

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

    ###############################
    # Observe and collect results #
    ###############################

    def register_observation(
        self, *names: str, **observations: Callable[[InteractiveContext], Any]
    ) -> None:
        """Register named observations, or resume paused ones, to evaluate every step.

        Each observation is called with this context and its return value is
        stored under the current clock time, first as soon as the simulation is
        set up (immediately if it already is) and then after every step taken.
        Values are stored exactly as returned and are included in
        :meth:`get_results`. After setup, the call is all or nothing: if any
        observation fails, nothing is registered or resumed.

        Parameters
        ----------
        names
            Names of paused observations to resume. Their values are kept and
            new values are added from now on. A name that is still registered is
            left as it is.
        observations
            Callables keyed by the name to store their values under. Each takes
            this context and returns the value to store.

        Raises
        ------
        ValueError
            If a name to resume is not an observation, or a new name is already an
            observation on this context or in the model. For observations
            registered before setup, the check against the model's observations
            is made by ``setup``.
        ObservationError
            If an observation raises when evaluated.
        """
        unknown = [name for name in names if name not in self._interactive_observations]
        if unknown:
            raise ValueError(f"No observations named {unknown} to resume.")
        duplicates = [name for name in observations if name in self._interactive_observations]
        if duplicates:
            raise ValueError(
                f"Observation names must be unique; {duplicates} already exist. Use a "
                "different name, or pass an existing name without a callable to resume it."
            )
        resumed = {
            name: self._interactive_observations[name]
            for name in names
            if not self._interactive_observations[name].active
        }
        new_observations = {
            name: _InteractiveObservation(func) for name, func in observations.items()
        }
        # Before setup there is nothing to evaluate or check against yet; setup does both.
        if self._lifecycle.current_state != lifecycle_states.INITIALIZATION:
            self._assert_no_duplicate_names(new_observations)
            self._observe({**resumed, **new_observations})
        for observation in resumed.values():
            observation.active = True
        self._interactive_observations.update(new_observations)
        already_registered = [name for name in names if name not in resumed]
        if already_registered:
            self._logger.info(f"Observations {already_registered} are already registered.")

    def pause_observation(self, *names: str) -> None:
        """Stop evaluating the named observations, keeping their values.

        A paused observation's values stay in :meth:`get_results`, and it
        can be resumed with :meth:`register_observation` by passing just its name.

        Parameters
        ----------
        names
            The names of the observations to stop.

        Raises
        ------
        ValueError
            If any name is not an observation, in which case nothing is changed.
        """
        unknown = [name for name in names if name not in self._interactive_observations]
        if unknown:
            raise ValueError(f"No observations named {unknown}.")
        for name in names:
            self._interactive_observations[name].active = False

    def _assert_no_duplicate_names(self, names: Iterable[str]) -> None:
        """Raise if any name is already an observation registered by the model."""
        taken = sorted(set(names) & set(self._results.observation_names))
        if taken:
            raise ValueError(
                f"Observation names {taken} passed to register_observation are already "
                "registered by the model. Use a different name."
            )

    def _observe(
        self, observations: dict[str, _InteractiveObservation] | None = None
    ) -> None:
        """Evaluate the given observations, or all active ones, and store the values, or none if any fails."""
        if observations is None:
            # Fix the active set up front, since an observation may pause another.
            observations = {
                name: obs
                for name, obs in self._interactive_observations.items()
                if obs.active
            }
        time = self.current_time
        values: dict[str, Any] = {}
        for name, observation in observations.items():
            try:
                values[name] = observation.func(self)
            except Exception as error:
                raise ObservationError(
                    f"Observation '{name}' failed at time {time}: {error!r}"
                ) from error
        # Store only after every observation succeeds so a failure leaves no partial values.
        for name, value in values.items():
            observations[name].values[time] = value

    def get_results(self) -> dict[str, Any]:
        """Get the model's formatted results and the values of each registered observation.

        Each observation registered with :meth:`register_observation` is keyed by
        its name, and its value is a copy of the values it has observed, keyed by
        clock time.
        """
        if not self._results.to_observe and not self._interactive_observations:
            self._logger.warning(
                "No results to return. An InteractiveContext does not observe "
                "results by default; pass observe=True to include the model's "
                "observations, or use register_observation() to add your own."
            )
        results: dict[str, Any] = super().get_results()
        for name, observation in self._interactive_observations.items():
            results[name] = dict(observation.values)
        return results


class _ResourceRow(NamedTuple):
    """A candidate row, with the relevance rank that orders it but is not reported."""

    rank: int
    name: str
    resource_type: str
    component: str


def _match_rank(matcher: re.Pattern[str], name: str) -> int:
    """Rank a resource by how squarely the pattern hit its name, best first.

    Names are dot-delimited hierarchies of underscore-separated words, so a match
    filling whole segments means more than one filling whole words, which in turn
    means more than one landing mid-word. Searching "mortality_rate" ranks
    ``mortality.mortality_rate`` above ``excess_mortality_rate``, which merely
    contains those characters; searching "ever" ranks ``test_ever_eligible`` above
    ``never_treated``.
    """
    # A pattern can occur several times in one name, so take its best showing
    # rather than whichever happens to come first.
    rank = 5  # only the component matched
    for match in matcher.finditer(name):
        start, end = match.span()
        if (start, end) == (0, len(name)):
            # the entire name, e.g. "age" for "age"
            return 0
        if start == 0:
            # the start of the name, e.g. "age" in "agent_used.x.y"
            rank = min(rank, 1)
        elif name[start - 1] == "." and (end == len(name) or name[end] == "."):
            # whole segments from the middle or the end, e.g. "age" in "mortality.age.rate"
            rank = min(rank, 2)
        elif name[start - 1] in _WORD_SEPARATORS and (
            end == len(name) or name[end] in _WORD_SEPARATORS
        ):
            # whole words from the middle or the end, e.g. "age" in "mortality_age_rate"
            rank = min(rank, 3)
        else:
            # part of a word, e.g. "age" in "coverage"
            rank = min(rank, 4)
    return rank


def _validate_direction(direction: str) -> None:
    """Check the direction a dependency walk was asked to take."""
    if direction not in _DEPENDENCY_DIRECTIONS:
        raise ValueError(
            f"Unknown direction '{direction}'. Use 'uses' for what this resource"
            " uses or 'used-by' for what uses it."
        )


def _natural_key(name: str) -> tuple[tuple[int, int | str], ...]:
    """Split a name so that a run of digits in it compares as a number."""
    # Resource names carry unpadded indices, so "10" has to sort after "2".
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", name)
    )


def _dependency_sort_key(resource: Resource) -> tuple[int, tuple[tuple[int, int | str], ...]]:
    """Sort resources by type and then by name."""
    # A type absent from the order sorts last rather than being dropped.
    rank = _DEPENDENCY_TYPE_RANK.get(resource.RESOURCE_TYPE, len(_DEPENDENCY_TYPE_RANK))
    return rank, _natural_key(str(resource.resource_id))


def _dependencies(
    graph: nx.DiGraph[Resource], node: Resource, direction: str
) -> list[Resource]:
    """Get the resources that a node uses or is used by, sorted by type and name."""
    neighbors = graph.predecessors(node) if direction == "uses" else graph.successors(node)
    return sorted(neighbors, key=_dependency_sort_key)


def _resolve_resource(graph: nx.DiGraph[Resource], resource: str | Resource) -> Resource:
    """Find the graph node that a resource argument names."""
    nodes_by_id = {str(node.resource_id): node for node in graph.nodes}

    if isinstance(resource, Resource):
        node = nodes_by_id.get(str(resource.resource_id))
        if node is None:
            raise ValueError(
                f"Resource '{resource.resource_id}' is not in the resource graph."
            )
        return node

    if resource in nodes_by_id:
        return nodes_by_id[resource]

    # Handle a bare name by looking for a unique match among all resources
    matches = sorted(
        (node for node in graph.nodes if node.name == resource),
        key=lambda node: str(node.resource_id),
    )
    if not matches:
        raise ValueError(
            f"No resource named '{resource}'. Use find_resources to search for it."
        )
    if len(matches) > 1:
        candidates = ", ".join(repr(str(node.resource_id)) for node in matches)
        raise ValueError(
            f"'{resource}' matches more than one resource. Use one of: {candidates}."
        )
    return matches[0]


def _render_dependency_tree(
    graph: nx.DiGraph[Resource],
    root: Resource,
    direction: str,
    depth: int | None,
) -> list[str]:
    """Render the dependency tree rooted at a resource, one string per line."""
    lines = [str(root.resource_id)]
    # ``expanded`` holds the nodes actually recursed into rather than the nodes
    # printed. A node cut off by the depth limit was never expanded, so a later
    # arrival at it has to report its hidden dependencies too rather than claim
    # they were shown already.
    expanded = {root}

    def walk(node: Resource, prefix: str, level: int) -> None:
        children = _dependencies(graph, node, direction)
        for position, child in enumerate(children):
            last = position == len(children) - 1
            child_dependencies = _dependencies(graph, child, direction)
            if child in expanded:
                # This child has already been expanded in a previous branch
                suffix = "  (expanded above)"
            elif depth is not None and level + 1 == depth and child_dependencies:
                # This child has more dependencies but we are at the depth limit
                hidden = len(child_dependencies)
                suffix = f"  ({hidden} direct branch{'es' if hidden > 1 else ''} hidden)"
            else:
                suffix = ""
            branch = "└── " if last else "├── "
            lines.append(f"{prefix}{branch}{child.resource_id}{suffix}")
            if suffix or not child_dependencies:
                # Don't recurse into this child, either because it has no dependencies or
                # because it has more dependencies but we are at the depth limit
                continue
            # Mark the child before walking it, not after. A cycle leads back to
            # the child from inside its own subtree, where an unmarked child is
            # expanded again and the walk never ends.
            expanded.add(child)
            walk(node=child, prefix=prefix + ("    " if last else "│   "), level=level + 1)

    if _dependencies(graph, root, direction):
        walk(node=root, prefix="", level=0)
    else:
        lines.append("    (none)")
    return lines


@dataclass
class _InteractiveObservation:
    """An observation registered on an interactive context and the values it has observed."""

    func: Callable[[InteractiveContext], Any]
    values: dict[ClockTime, Any] = field(default_factory=dict)
    active: bool = True
