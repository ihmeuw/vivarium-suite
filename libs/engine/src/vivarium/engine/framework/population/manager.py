"""
==================
Population Manager
==================

"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, overload

import pandas as pd

import vivarium.engine.framework.population.utilities as pop_utils
from vivarium.engine.component import Component
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.lifecycle import lifecycle_states
from vivarium.engine.framework.population.exceptions import PopulationError
from vivarium.engine.framework.population.population_view import PopulationView
from vivarium.engine.framework.resource import Resource
from vivarium.engine.manager import Manager

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder
    from vivarium.engine.types import ClockStepSize, ClockTime

from collections import defaultdict


@dataclass
class SimulantData:
    """Data to help components initialize simulants.

    Any time simulants are added to the simulation, each initializer is called
    with this structure containing information relevant to their initialization.

    """

    index: pd.Index[int]
    """The index representing the new simulants being added to the simulation."""
    user_data: dict[str, Any]
    """A dictionary of extra data passed in by the component creating the population."""
    creation_time: ClockTime
    """The time when the simulants enter the simulation."""
    creation_window: ClockStepSize
    """The span of time over which the simulants are created. Useful for, e.g., distributing 
    ages over the window."""


class PopulationManager(Manager):
    """Manages the population state table."""

    # TODO: Move the configuration for initial population creation to
    #  user components.
    CONFIGURATION_DEFAULTS = {
        "population": {
            "population_size": 100,
        },
    }

    @property
    def name(self) -> str:
        """The name of this component."""
        return "population_manager"

    @property
    def private_columns(self) -> pd.DataFrame:
        """The dataframe of all population private columns.

        Notes
        -----
        Critically, the private columns dataframe not only contains all private
        columns created for the simulation, but also serves as the simulant
        index for the entire population. Even if no private columns are created,
        this dataframe will exist and all simulants will be represented by its index.
        """
        if self._private_columns is None:
            raise PopulationError("Population has not been initialized.")
        return self._private_columns

    @property
    def staged_simulants(self) -> pd.DataFrame:
        """The dataframe of the simulants currently being initialized.

        Initializers write here rather than into the population, so the population is
        never widened with null rows for simulants whose values have not been computed
        yet. Appended to the private columns once every initializer has run.
        """
        if self._staged_simulants is None:
            raise PopulationError("No simulants are being added.")
        return self._staged_simulants

    @property
    def adding_simulants(self) -> bool:
        """Whether simulants are currently being initialized."""
        return self._staged_simulants is not None

    ############################
    # Normal Component Methods #
    ############################

    def __init__(self) -> None:
        self._private_columns: pd.DataFrame | None = None
        self._staged_simulants: pd.DataFrame | None = None
        self._private_column_metadata: defaultdict[str, list[str]] = defaultdict(list)
        self._registered_initializers: list[Callable[[SimulantData], None]] = []
        self._last_id = -1
        self.tracked_queries: list[str] = []
        self.pipeline_evaluation_depth: int = 0

    def setup(self, builder: Builder) -> None:
        """Registers the population manager with other vivarium systems."""
        super().setup(builder)
        self.logger = builder.logging.get_logger(self.name)
        self.clock = builder.time.clock()
        self.step_size = builder.time.step_size()
        self.resources = builder.resources
        self._add_constraint = builder.lifecycle.add_constraint
        self._get_attribute_pipelines = builder.value.get_attribute_pipelines()
        self._register_attribute_producer = builder.value.register_attribute_producer
        self._get_current_component_or_manager = (
            builder.components.get_current_component_or_manager
        )
        self.get_current_state = builder.lifecycle.current_state()

        builder.lifecycle.add_constraint(
            self.get_view,
            allow_during=[
                lifecycle_states.SETUP,
                lifecycle_states.POST_SETUP,
                lifecycle_states.POPULATION_CREATION,
                lifecycle_states.SIMULATION_END,
                lifecycle_states.REPORT,
            ],
        )
        builder.lifecycle.add_constraint(
            self.get_simulant_creator, allow_during=[lifecycle_states.SETUP]
        )
        builder.lifecycle.add_constraint(
            self.register_initializer, allow_during=[lifecycle_states.SETUP]
        )
        self._add_constraint(
            self.get_population,
            restrict_during=[
                lifecycle_states.SETUP,
                lifecycle_states.POST_SETUP,
            ],
        )

        builder.event.register_listener(lifecycle_states.POST_SETUP, self.on_post_setup)

    def on_post_setup(self, event: Event) -> None:
        # All pipelines are registered during setup and so exist at this point.
        self._attribute_pipelines = self._get_attribute_pipelines()

    def __repr__(self) -> str:
        return "PopulationManager()"

    ###########################
    # Builder API and helpers #
    ###########################

    def register_tracked_query(self, query: str) -> None:
        """Updates list of registered tracked queries with the provided query.

        Parameters
        ----------
        query
            The new query to add to the running list of tracked queries.

        Notes
        -----
        While we log a warning if the same query is registered multiple times,
        we make no attempt to de-duplicate functionally-equivalent queries that
        are syntactically different, e.g. "x > 5" and "5 < x". In such cases,
        duplicate queries will be applied which is not optimal but will not
        affect correctness.
        """
        if query in self.tracked_queries:
            self.logger.warning(
                f"The tracked query '{query}' has already been registered. "
                "Duplicate registrations are ignored."
            )
            return
        self.tracked_queries.append(query)

    def get_private_column_names(self, component_name: str) -> list[str]:
        """Gets the names of private columns created by a given component.

        Parameters
        ----------
        component_name
            The name of the component whose private column names are to be retrieved.

        Returns
        -------
            The list of private column names created by the specified component.
            If the component has not created any private columns, an empty list is returned.
        """
        return self._private_column_metadata[component_name]

    @overload
    def get_private_columns(
        self,
        component: Component | Manager,
        index: pd.Index[int] | None = None,
        columns: str = ...,
    ) -> pd.Series[Any]:
        ...

    @overload
    def get_private_columns(
        self,
        component: Component | Manager,
        index: pd.Index[int] | None = None,
        columns: list[str] | tuple[str, ...] = ...,
    ) -> pd.DataFrame:
        ...

    @overload
    def get_private_columns(
        self,
        component: Component | Manager,
        index: pd.Index[int] | None = None,
        columns: None = None,
    ) -> pd.Series[Any] | pd.DataFrame:
        ...

    def get_private_columns(
        self,
        component: Component | Manager,
        index: pd.Index[int] | None = None,
        columns: str | list[str] | tuple[str, ...] | None = None,
    ) -> pd.DataFrame | pd.Series[Any]:
        """Gets the private columns for a given component.

        While the ``private_columns`` property provides a dataframe of all private
        columns in population, this method returns only the private columns created
        by the specified component. If no component is specified, then no columns
        are returned.

        Parameters
        ----------
        component
            The component whose private columns are to be retrieved. If None,
            no columns are returned.
        index
            The index of simulants to include in the returned dataframe. If None,
            all simulants are included.
        columns
            The specific column(s) to include. If None, all columns created by the
            component are included.

        Raises
        ------
        PopulationError
            If ``columns`` are requested during initial population creation
            (when no columns yet exist) or if the provided ``component`` does not
            create one or more of them.

        Returns
        -------
            The private column(s) created by the specified component. Will return
            a Series if a single column is requested or a Dataframe otherwise.
        """

        all_private_columns = self._private_column_metadata.get(component.name, [])
        if columns is None:
            returned_cols = all_private_columns
            squeeze = True
        else:
            if isinstance(columns, str):
                columns = [columns]
                squeeze = True
            else:
                columns = list(columns)
                squeeze = False
            missing_cols = set(columns).difference(set(all_private_columns))
            if missing_cols:
                raise PopulationError(
                    f"Component {component.name} is requesting the following "
                    f"private columns to which it does not have access: {missing_cols}."
                )
            returned_cols = columns
        private_columns = self._read(index, returned_cols)
        if squeeze:
            private_columns = private_columns.squeeze(axis=1)
        return private_columns

    def _read(self, index: pd.Index[int] | None, columns: list[str]) -> pd.DataFrame:
        """Read ``columns`` for ``index`` from whichever frame holds those simulants.

        During a creation pass the new simulants live in their own frame, so a read of
        them is served from there.

        Raises
        ------
        PopulationError
            If ``index`` names both the simulants being added and simulants already in
            the population. A simulant being added has no value yet for any column
            whose initializer has not run, so serving it alongside a settled one would
            quietly mix real values with nulls.
        PopulationError
            If no initializer has created one of ``columns`` yet.
        PopulationError
            If a column exists but the frame serving the read has no value for it.
        """
        frame = self.private_columns
        created = set(frame.columns)
        if self.adding_simulants:
            staged = self.staged_simulants
            created.update(staged.columns)
            if index is not None:
                is_staged = index.isin(staged.index)
                if is_staged.all():
                    frame = staged
                elif is_staged.any():
                    raise PopulationError(
                        "A read cannot cover both the simulants being added and those "
                        "already in the population, because a simulant being added has "
                        "no value yet for any column whose initializer has not run. "
                        "Read them separately."
                    )

        missing = [column for column in columns if column not in created]
        if missing:
            raise PopulationError(
                f"The following private columns have not been created: {missing}. An "
                "initializer that reads a private column must require it, so that it "
                "runs after the initializer that creates it."
            )
        unset = [column for column in columns if column not in frame.columns]
        if unset:
            raise PopulationError(
                f"The following private columns have no value for the simulants being "
                f"read: {unset}. Their initializer has not run for these simulants, so "
                "an initializer that reads them must require them."
            )
        return frame[columns] if index is None else frame.loc[index, columns]

    @property
    def staged_index(self) -> pd.Index[int]:
        """The index of the simulants currently being initialized."""
        return self.staged_simulants.index

    def get_population_index(self) -> pd.Index[int]:
        """Gets the index of the current population."""
        return self.private_columns.index

    def get_view(self, component: Component | None = None) -> PopulationView:
        """Gets a time-varying view of the population state table.

        The requested population view can be used to view the current state or
        to update the state with new values.

        Parameters
        ----------
        component
            The component requesting this view. If None, the view will provide
            read-only access.

        Returns
        -------
            A view of the requested private columns of the population state table.

        """
        view = self._get_view(component)
        self._add_constraint(
            view.get,
            restrict_during=[
                lifecycle_states.INITIALIZATION,
                lifecycle_states.SETUP,
                lifecycle_states.POST_SETUP,
            ],
        )
        self._add_constraint(
            view.update,
            restrict_during=[
                lifecycle_states.INITIALIZATION,
                lifecycle_states.SETUP,
                lifecycle_states.POST_SETUP,
                lifecycle_states.SIMULATION_END,
                lifecycle_states.REPORT,
            ],
        )
        return view

    def _get_view(self, component: Component | None) -> PopulationView:
        self._last_id += 1
        view = PopulationView(self, component, self._last_id)
        return view

    def get_simulant_creator(self) -> Callable[[int, dict[str, Any] | None], pd.Index[int]]:
        """Gets a function that can generate new simulants.

        The creator function takes the number of simulants to be created as its
        first argument and a population configuration dict that will be available
        to simulant initializers as its second argument. It generates the new rows
        in the population state table and then calls each initializer registered
        with the population system with a data object containing the state table
        index of the new simulants, the configuration info passed to the creator,
        the current simulation time, and the size of the next time step. It returns
        the index of the whole population, including the simulants just created.

        Returns
        -------
           The simulant creator function.
        """
        return self._create_simulants

    def _create_simulants(
        self, count: int, population_configuration: dict[str, Any] | None = None
    ) -> pd.Index[int]:
        population_configuration = (
            population_configuration if population_configuration else {}
        )
        initial_pass = self._private_columns is None
        if initial_pass:
            self._private_columns = pd.DataFrame()

        first_simulant = len(self.private_columns)
        new_index = pd.RangeIndex(first_simulant, first_simulant + count)

        if new_index.empty and not initial_pass:
            # Nothing to initialize. The initial pass still runs its initializers,
            # because they are what create the population's columns.
            return self.private_columns.index

        self._staged_simulants = pd.DataFrame(index=new_index)
        for initializer in self.resources.get_population_initializers():
            initializer(
                SimulantData(
                    new_index, population_configuration, self.clock(), self.step_size()
                )
            )
        staged = self.staged_simulants
        committed = self.private_columns
        # A rowless population contributes nothing but its dtypes, and those are
        # unreliable: an initializer that builds values by comprehension yields an
        # empty list for an empty index, which pandas types object or float64
        # whatever the values would have been. Concatenating would resolve to that
        # dtype and lose the real one, so the staged frame stands alone. Note
        # len() rather than .empty, which is also true of a frame that has rows
        # but no columns.
        self._private_columns = (
            staged if len(committed) == 0 else pd.concat([committed, staged])
        )
        self._staged_simulants = None

        missing = {}
        for component, cols_created in self._private_column_metadata.items():
            missing_cols = [col for col in cols_created if col not in self._private_columns]
            if missing_cols:
                missing[component] = missing_cols
        if missing:
            raise PopulationError(
                "The following components registered initializers to create columns "
                f"that were not actually created: {missing}."
            )

        return self.private_columns.index

    def register_initializer(
        self,
        initializer: Callable[[SimulantData], None],
        columns: str | Sequence[str] | None,
        required_resources: Sequence[str | Resource] = (),
    ) -> None:
        """Registers a component's initializers and any (private) columns created by them.

        This does three primary things:
        1. Registers each private column's corresponding attribute producer.
        2. Records metadata about which component created which private columns.
        3. Registers the initializer as a resource.

        A `columns` value of None indicates that no private columns are being registered.
        This is useful when a component or manager needs to register an initializer
        that does not create any private columns.

        Parameters
        ----------
        initializer
            A function that will be called to initialize the state of new simulants.
        columns
            The private columns that the given initializer provides the initial state
            information for.
        required_resources
            The resources that the initializer requires to run. Strings are interpreted
            as attributes.

        Raises
        ------
        PopulationError
            If this initializer has already been registered or if the columns being
            created by this initializer overlap with columns created by another initializer.
        """

        if initializer in self._registered_initializers:
            raise PopulationError(
                f"The initializer '{initializer.__qualname__}' has already been registered. "
                "Each initializer may only be registered once."
            )

        component = self._get_current_component_or_manager()

        if columns is None:
            columns = []
        elif isinstance(columns, str):
            columns = [columns]
        for column_name in columns:
            # Check for duplicate registration
            for component_name, columns_list in self._private_column_metadata.items():
                if column_name in columns_list:
                    raise PopulationError(
                        f"Component '{component.name}' is attempting to register "
                        f"private column '{column_name}' but it is already registered "
                        f"by component '{component_name}'."
                    )
            # Register each private column's attribute producer
            self._register_attribute_producer(
                column_name,
                source=[column_name],
                source_is_private_column=True,
            )

        # Register private column metadata
        self._private_column_metadata[component.name].extend(columns)

        # Track the initializer to prevent duplicate registration
        self._registered_initializers.append(initializer)

        # Register the initializer as a resource
        self.resources.add_private_columns(
            initializer=initializer,
            columns=columns,
            required_resources=required_resources,
        )

    ###############
    # Context API #
    ###############

    def get_all_attribute_names(self) -> list[str]:
        """Gets the names of all attributes in the population.

        Returns
        -------
            A list of all attribute names in the population.
        """
        return list(self._attribute_pipelines.keys())

    @overload
    def get_population(
        self,
        attributes: list[str] | tuple[str, ...] | Literal["all"],
        index: pd.Index[int] | None = None,
        query: str = "",
        squeeze: Literal[True] = True,
        mode: Literal["default"] = "default",
    ) -> pd.Series[Any] | pd.DataFrame:
        ...

    @overload
    def get_population(
        self,
        attributes: list[str] | tuple[str, ...] | Literal["all"],
        index: pd.Index[int] | None = None,
        query: str = "",
        squeeze: Literal[False] = ...,
        mode: Literal["default"] = "default",
    ) -> pd.DataFrame:
        ...

    @overload
    def get_population(
        self,
        attributes: list[str] | tuple[str, ...] | Literal["all"],
        index: pd.Index[int] | None = None,
        query: str = "",
        squeeze: Literal[True, False] = True,
        mode: Literal["source", "no-post-processors"] = ...,
    ) -> Any:
        ...

    def get_population(
        self,
        attributes: list[str] | tuple[str, ...] | Literal["all"],
        index: pd.Index[int] | None = None,
        query: str = "",
        squeeze: Literal[True, False] = True,
        mode: Literal["default", "source", "no-post-processors"] = "default",
    ) -> Any:
        """Provides a copy of the population state table.

        Parameters
        ----------
        attributes
            The attributes to include as the state table. If "all", all attributes are included.
        index
            The index of simulants to include in the returned population. If None,
            all simulants are included.
        query
            Additional conditions used to filter the index.
        squeeze
            Whether or not to attempt to squeeze a multi-level column into a single-level
            column and/or a single-column dataframe into a series.
        mode
            The mode for pipeline evaluation. One of "default", "source",
            or "no-post-processors".

        Notes
        -----
        If ``mode`` is not "default", the returned data will not be squeezed
        regardless of the ``squeeze`` argument passed.

        Returns
        -------
            A copy of the population state table.

        Raises
        ------
        TypeError
            If ``attributes`` is not a list or tuple of strings or "all".
        PopulationError
            - If any of the requested attributes do not exist in the state table.
            - If a required column for querying is missing from the state table.
            - If the population has not yet been initialized.
        ValueError
            If multiple attributes are requested when ``mode`` is not "default".
        """

        if self._private_columns is None:
            return pd.DataFrame()

        if isinstance(attributes, str) and attributes != "all":
            raise TypeError(
                f"Attributes must be a list of strings or 'all'; got '{attributes}'."
            )

        if attributes == "all":
            requested_attributes = self.get_all_attribute_names()
        else:
            attributes = list(attributes)
            # check for duplicate request
            if len(attributes) != len(set(attributes)):
                # deduplicate while preserving order
                requested_attributes = list(dict.fromkeys(attributes))
                self.logger.warning(
                    f"Duplicate attributes requested: {set(attributes) - set(requested_attributes)}\n"
                    "Only returning one instance of each of these duplicate requests."
                )
            else:
                requested_attributes = attributes

        non_existent_attributes = set(requested_attributes) - set(
            self._attribute_pipelines.keys()
        )
        if non_existent_attributes:
            raise PopulationError(
                f"Requested attribute(s) {non_existent_attributes} not in population state table. "
                "This is likely due to a failure to require some columns, randomness "
                "streams, or pipelines when registering a simulant initializer, an attribute "
                "producer, or an attribute modifier. NOTE: It is possible for a run to "
                "succeed even if resource requirements were not properly specified in "
                "the simulant initializers or pipeline creation/modification calls. This "
                "success depends on component initialization order which may change in "
                "different run settings."
            )

        idx = index if index is not None else self._private_columns.index

        # Filter the index based on the query
        columns_to_get = set(requested_attributes)
        if query:
            query_columns = pop_utils.extract_columns_from_query(query)
            # We can remove these query columns from requested columns (and will fetch later)
            columns_to_get = columns_to_get.difference(query_columns)
            missing_query_columns = query_columns.difference(set(self._attribute_pipelines))
            if missing_query_columns:
                raise PopulationError(
                    "Columns used for querying missing from population state table:\n"
                    f"Missing columns: {missing_query_columns}\n"
                    f"Query: {query}"
                )
            query_df = self._get_attributes(idx, list(query_columns))
            query_df = query_df.query(query)
            idx = query_df.index

        _use_single_attr_path = mode in ("source", "no-post-processors")
        data = self._get_attributes(
            idx,
            requested_attributes if _use_single_attr_path else list(columns_to_get),
            mode=mode,
        )
        if _use_single_attr_path:
            # NOTE: This correctly returns the requested attribute even when it
            # overlaps with query columns because we pass `requested_attributes`
            # (not `columns_to_get`) above when `mode` is "source" or "no-post-processors".
            return data

        # Add on any query columns that are actually requested to be returned
        requested_query_columns = (
            query_columns.intersection(set(requested_attributes)) if query else set()
        )
        if requested_query_columns:
            requested_query_df = query_df[list(requested_query_columns)]
            if isinstance(data.columns, pd.MultiIndex):
                # Make the query df multi-index to prevent converting columns from
                # multi-index to single index w/ tuples for column names
                requested_query_df.columns = pd.MultiIndex.from_product(
                    [requested_query_df.columns, [""]]
                )
            data = pd.concat([data, requested_query_df], axis=1)

        # Maintain column ordering
        data = data[requested_attributes]

        if squeeze:
            if (
                isinstance(data.columns, pd.MultiIndex)
                and len(set(data.columns.get_level_values(0))) == 1
            ):
                # If multi-index columns with a single outer level, drop the outer level
                data = data.droplevel(0, axis=1)
            if len(data.columns) == 1:
                # If single column df, squeeze to series
                data = data.squeeze(axis=1)

        return data

    def get_tracked_query(self) -> str:
        """Gets the combined tracked query for the population.

        Returns
        -------
            A query string combining all registered tracked queries with "and" operators.
        """
        return " and ".join(self.tracked_queries)

    @overload
    def _get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["default"] = "default",
    ) -> pd.DataFrame:
        ...

    @overload
    def _get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["source", "no-post-processors"] = ...,
    ) -> Any:
        ...

    def _get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["default", "source", "no-post-processors"] = "default",
    ) -> Any:
        """Get the population for a given index and requested attributes.

        While evaluating attribute pipelines, we increment ``pipeline_evaluation_depth``
        so that nested calls to ``PopulationView.get`` (which may be
        triggered by pipeline sources or mutators) do not automatically re-apply
        tracked queries. The index passed to each pipeline has already been filtered
        appropriately by the enclosing ``get_population`` call.

        Note that only tracked queries are suppressed. Explicit ``query`` arguments
        passed by the pipeline source/mutator are supported.
        """

        self.pipeline_evaluation_depth += 1
        try:
            return self.__get_attributes(idx, requested_attributes, mode=mode)
        finally:
            self.pipeline_evaluation_depth -= 1

    @overload
    def __get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["default"] = "default",
    ) -> pd.DataFrame:
        ...

    @overload
    def __get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["source", "no-post-processors"] = ...,
    ) -> Any:
        ...

    def __get_attributes(
        self,
        idx: pd.Index[int],
        requested_attributes: Sequence[str],
        mode: Literal["default", "source", "no-post-processors"] = "default",
    ) -> Any:
        """Core implementation of ``_get_attributes``."""

        if mode in ("source", "no-post-processors"):
            if len(requested_attributes) != 1:
                raise ValueError(
                    f"When mode is '{mode}', a single attribute must "
                    f"be requested. You requested {requested_attributes}."
                )
            return self._attribute_pipelines[requested_attributes[0]](idx, mode=mode)

        attributes_list: list[pd.Series[Any] | pd.DataFrame] = []

        # batch simple attributes and directly leverage private column backing dataframe
        simple_attributes = [
            name
            for name, pipeline in self._attribute_pipelines.items()
            if name in requested_attributes and pipeline.is_simple
        ]
        if simple_attributes:
            attributes_list.append(self._read(idx, simple_attributes))

        # handle remaining non-simple attributes one by one
        remaining_attributes = [
            attribute
            for attribute in requested_attributes
            if attribute not in simple_attributes
        ]
        contains_column_multi_index = False
        for name in remaining_attributes:
            values = self._attribute_pipelines[name](idx)

            # Handle column names
            if isinstance(values, pd.Series):
                if values.name is not None and values.name != name:
                    self.logger.warning(
                        f"The '{name}' attribute pipeline returned a pd.Series with a "
                        f"different name '{values.name}'. For the column being added to the "
                        f"population state table, we will use '{name}'."
                    )
                values.name = name
            else:
                # Must be a dataframe. Coerce the columns to multi-index and set the
                # attribute name as the outer level.
                if isinstance(values.columns, pd.MultiIndex):
                    # FIXME [MIC-6645]
                    raise NotImplementedError(
                        f"The '{name}' attribute pipeline returned a DataFrame with multi-level "
                        f"columns (nlevels={values.columns.nlevels}). Multi-level columns in "
                        "attribute pipeline outputs are not supported."
                    )
                values.columns = pd.MultiIndex.from_product([[name], values.columns])
                contains_column_multi_index = True
            attributes_list.append(values)

        # Make sure all items of the list have consistent column levels
        if contains_column_multi_index:
            for i, item in enumerate(attributes_list):
                if isinstance(item, pd.Series):
                    item_df = item.to_frame()
                    item_df.columns = pd.MultiIndex.from_tuples([(item.name, "")])
                    attributes_list[i] = item_df
                if isinstance(item, pd.DataFrame) and item.columns.nlevels == 1:
                    item.columns = pd.MultiIndex.from_product([item.columns, [""]])
        df = (
            pd.concat(attributes_list, axis=1) if attributes_list else pd.DataFrame(index=idx)
        )

        return df

    def update(self, update: pd.DataFrame) -> None:
        # Writes during a creation pass belong to the simulants being added.
        frame = self.staged_simulants if self.adding_simulants else self.private_columns
        frame[update.columns] = update
