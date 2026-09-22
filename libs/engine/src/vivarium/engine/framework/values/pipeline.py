from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import pandas as pd

from vivarium.engine import Component
from vivarium.engine.framework.resource import Resource
from vivarium.engine.framework.values.exceptions import DynamicValueError
from vivarium.engine.manager import Manager

if TYPE_CHECKING:
    from vivarium.engine.framework.population import PopulationView
    from vivarium.engine.framework.values import (
        AttributePostProcessor,
        PostProcessor,
        ValueCombiner,
        ValuesManager,
    )

T = TypeVar("T")

_UNSET_COMPONENT = "<unset>"

_LABEL_WIDTH = 16
"""Column the values in a pipeline description are aligned to."""
_ENTRY_INDENT = 18
"""Indent for a modifier or post-processor entry."""
_DETAIL_INDENT = 21
"""Indent for the continuation line under an entry."""

_MUTATORS_DEPRECATION_MESSAGE = (
    "Pipeline.mutators is deprecated and will be removed; use Pipeline.modifiers instead."
)


class RequiresPrettyHook:
    """Reject subclasses that would shadow the inherited IPython display hook.

    IPython walks the mro and uses whichever of ``_repr_pretty_`` or ``__repr__``
    it finds first in a class's own ``__dict__``, so a subclass defining only
    ``__repr__`` silently disables the rich display it would have inherited.

    That same rule is why ``_repr_pretty_`` is repeated on each class below rather
    than inherited from here: a class defining ``__repr__`` stops the walk before
    an inherited hook is reached, so a hook defined here would never run. Only the
    check is shared; the repetition of the hook itself is load-bearing.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "__repr__" in vars(cls) and "_repr_pretty_" not in vars(cls):
            raise TypeError(
                f"{cls.__name__} defines __repr__ without _repr_pretty_, which shadows the "
                "inherited IPython display hook. Define both, or neither."
            )


def _callable_display_name(callable_: Callable[..., Any]) -> str:
    """Return the most readable name available for a callable.

    Prefers ``__qualname__`` so bound methods carry their class, which is what
    makes a modifier identifiable in a pipeline description. This deliberately
    differs from :meth:`~vivarium.engine.framework.resource.Resource.get_callable_name`,
    which prefers the bare ``__name__``: that one builds resource ids, where the
    component name already supplies the owner and a dotted class name would make
    the id harder to read.
    """
    for attribute in ("__qualname__", "__name__"):
        name = getattr(callable_, attribute, None)
        if name is not None:
            return str(name)
    # A callable class instance has neither, and its repr is an address; name it
    # by its class so post-processors registered that way stay identifiable.
    return type(callable_).__qualname__


class NamedCallable(RequiresPrettyHook):
    """Wrap a plain callable so it prints like the other parts of a pipeline."""

    def __init__(self, callable_: Callable[..., Any]) -> None:
        # Unwrap rather than nest. Wrapping a wrapper would leave the display
        # reporting this class's name in place of the original callable's, and
        # the type annotations let an already-wrapped list be registered again.
        self._callable: Callable[..., Any] = (
            callable_._callable if isinstance(callable_, NamedCallable) else callable_
        )
        # Let inspect see through to the wrapped callable, so signature() and
        # getdoc() describe it rather than this wrapper's forwarding __call__.
        # Only forward a docstring that exists: most callables passed here have
        # none, and this class's own description is more useful than None.
        self.__wrapped__ = self._callable
        if self._callable.__doc__ is not None:
            self.__doc__ = self._callable.__doc__

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._callable(*args, **kwargs)

    def __str__(self) -> str:
        return _callable_display_name(self._callable)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {str(self)!r}>"

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """Show this object's description when it is echoed bare in an IPython cell.

        IPython's display machinery prefers this hook over ``__repr__``, so the
        description shows up in a notebook while ``__repr__`` stays short enough
        for tracebacks and collections.
        """
        printer.text(str(self))

    def __eq__(self, other: object) -> bool:
        # Equal to the callable it wraps, so a comparison against the bare
        # function keeps working after wrapping.
        if isinstance(other, NamedCallable):
            return bool(self._callable == other._callable)
        return bool(self._callable == other)

    def __hash__(self) -> int:
        return hash(self._callable)


class NamedCombiner(NamedCallable):
    """A pipeline's combiner, wrapped for display."""


class NamedPostProcessor(NamedCallable):
    """One of a pipeline's post-processors, wrapped for display."""


class ValueSource(RequiresPrettyHook):
    """A wrapper for the source of a value pipeline."""

    def __init__(self, pipeline: Pipeline, source: Callable[..., Any]) -> None:
        self._pipeline = pipeline
        self._source = source

    def __bool__(self) -> bool:
        return True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._source(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self._identifier} ({self._kind})"

    def __repr__(self) -> str:
        # Defined only here: a subclass __repr__ would shadow _repr_pretty_ in
        # IPython, which walks the mro and takes the first of the two it finds.
        identifier = self._identifier
        if identifier is None:
            return f"<{type(self).__name__}>"
        return f"<{type(self).__name__} {identifier!r}>"

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """Show this object's description when it is echoed bare in an IPython cell.

        IPython's display machinery prefers this hook over ``__repr__``, so the
        description shows up in a notebook while ``__repr__`` stays short enough
        for tracebacks and collections.
        """
        printer.text(str(self))

    @property
    def _identifier(self) -> Any:
        """What this source reads from. None when there is nothing to name."""
        if isinstance(self._source, Resource):
            return self._source.name
        return _callable_display_name(self._source)

    @property
    def _kind(self) -> str:
        """The label for what kind of source this is.

        A source that is itself a resource names its own kind, so the label does
        not have to be inferred.
        """
        if isinstance(self._source, Resource):
            return str(self._source.RESOURCE_TYPE)
        return "callable"


class MissingValueSource(ValueSource):
    """A placeholder value source representing a pipeline with no source.

    This is used when a modifier of a pipeline is registered before the pipeline itself.
    The source of the pipeline must be set before the pipeline is called, but this allows
    for more flexible ordering of component setup.
    """

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        # Not composed from _identifier and _kind like its siblings: there is
        # nothing to name, and "None (missing)" reads worse than this.
        return "<no source>"

    @property
    def _identifier(self) -> None:
        return None

    def _source(self, *args: Any, **kwargs: Any) -> Any:
        raise DynamicValueError(
            f"The pipeline for {self._pipeline.name} has no source. This likely means you are"
            " attempting to modify a value that hasn't been created."
        )


class PrivateColumnValueSource(ValueSource):
    """A value source representing a private column source of a value pipeline."""

    def __init__(
        self, pipeline: Pipeline, source: str, population_view: PopulationView
    ) -> None:
        self._pipeline = pipeline
        self.column_name = source
        """The name of the private column that is the source of this pipeline."""
        self._population_view = population_view
        """A population view that can be used to access the private column source of this pipeline."""

    @property
    def _identifier(self) -> str:
        return self.column_name

    @property
    def _kind(self) -> str:
        return "private_column"

    def _source(self, index: pd.Index[int]) -> pd.Series[Any]:
        return self._population_view._manager.get_private_columns(
            component=self._pipeline.component, index=index, columns=self.column_name
        )


class AttributesValueSource(ValueSource):
    """A value source representing the list of attributes source of an attribute pipeline."""

    def __init__(
        self, pipeline: Pipeline, source: list[str], population_view: PopulationView
    ) -> None:
        self._pipeline = pipeline
        self.attributes = source[0] if len(source) == 1 else source
        """The name or list of names of the attributes that are the source of this pipeline."""
        self._population_view = population_view
        """A population view that can be used to access the attribute source of this pipeline."""

    @property
    def _identifier(self) -> str | list[str]:
        return self.attributes

    @property
    def _kind(self) -> str:
        return "attributes"

    def _source(self, index: pd.Index[int]) -> pd.Series[Any] | pd.DataFrame:
        return self._population_view.get(index=index, attributes=self.attributes)


class ValueModifier(Resource, RequiresPrettyHook):
    """A resource representing a modifier of a value pipeline."""

    RESOURCE_TYPE = "value_modifier"

    def __init__(
        self,
        pipeline: Pipeline,
        modifier: Callable[..., Any],
        component: Component | Manager,
        required_resources: Iterable[str | Resource] = (),
        description: str | None = None,
    ) -> None:
        modifier_name = self.get_callable_name(modifier)
        modifier_index = len(pipeline.modifiers) + 1
        name = f"{pipeline.name}.{modifier_index}.{component.name}.{modifier_name}"
        super().__init__(name, component, required_resources)

        self._pipeline = pipeline
        self._source = modifier
        self.description = description
        """A description of what this modifier does to the value."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._source(*args, **kwargs)

    def __str__(self) -> str:
        callable_name, component_name = self._describe()
        entry = f"{callable_name} from {component_name}"
        if self.description:
            entry = f"{entry}\n{self.description}"
        return entry

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """Show this object's description when it is echoed bare in an IPython cell.

        IPython's display machinery prefers this hook over ``__repr__``, so the
        description shows up in a notebook while ``__repr__`` stays short enough
        for tracebacks and collections.
        """
        printer.text(str(self))

    def _describe(self) -> tuple[str, str]:
        """Return this modifier's callable name and the name of its component."""
        component_name = (
            self._component.name if self._component is not None else _UNSET_COMPONENT
        )
        return _callable_display_name(self._source), component_name


class Pipeline(Resource, RequiresPrettyHook):
    """A tool for building up values across several components.

    Pipelines are lazily initialized so that we don't have to put constraints
    on the order in which components are created and set up. The values manager
    will configure a pipeline (set all of its attributes) when the pipeline
    source is created.

    As long as a pipeline is not actually called in a simulation, it does not
    need a source or to be configured. This might occur when writing
    generic components that create a set of pipeline modifiers for
    values that won't be used in the particular simulation.

    Notes
    -----
    Pipelines are highy generic and can be used to calculate values of any type
    through a simulation. *Most* pipelines are intended to calculate simulant
    attributes; for those, use :class:`~vivarium.engine.framework.values.pipeline.AttributePipeline`.
    """

    RESOURCE_TYPE = "value"
    """The type of the resource."""

    def __init__(self, name: str, component: Component | None = None) -> None:
        super().__init__(name, component=component)

        self.description: str | None = None
        """A description of the value this pipeline represents."""
        self.source: ValueSource = MissingValueSource(self)
        """The callable source of the value represented by the pipeline."""
        self.modifiers: list[ValueModifier] = []
        """A list of callables that directly modify the pipeline source or
        contribute portions of the value."""
        self._combiner: NamedCombiner | None = None
        self.post_processor: list[NamedPostProcessor] = []
        """A list of the transformations to perform in order on the combined output of
        the source and modifiers."""
        self._manager: ValuesManager | None = None

    def _get_attr_error(self, attribute: str) -> str:
        return (
            f"The pipeline for {self.name} has no {attribute}. This likely means "
            f"you are attempting to modify a value that hasn't been created."
        )

    def _set_attr_error(self, attribute: str, new_value: Any) -> str:
        current_value = getattr(self, f"_{attribute}")
        return (
            f"A second component is attempting to set the {attribute} for pipeline {self.name} "
            f"with {new_value}, but it already has a {attribute}: {current_value}."
        )

    def _get_property(self, property: T | None, property_name: str) -> T:
        if property is None:
            raise DynamicValueError(self._get_attr_error(property_name))
        return property

    @property
    def combiner(self) -> ValueCombiner:
        """A strategy for combining the source and modifier values into the
        final value represented by the pipeline."""
        return self._get_property(self._combiner, "combiner")

    @property
    def manager(self) -> ValuesManager:
        """A reference to the simulation values manager."""
        return self._get_property(self._manager, "manager")

    @property
    def mutators(self) -> list[ValueModifier]:
        """Deprecated alias for :attr:`modifiers`.

        .. deprecated:: 5.11.0
            Use :attr:`modifiers` instead. This alias is read-only and will be
            removed.
        """
        warnings.warn(_MUTATORS_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        return self.modifiers

    def __call__(
        self,
        *args: Any,
        mode: Literal["default", "source", "no-post-processors"] = "default",
        **kwargs: Any,
    ) -> Any:
        """Generates the value represented by this pipeline.

        Arguments
        ---------
        mode
            The mode for pipeline evaluation. One of "default", "source",
            or "no-post-processors".
        args, kwargs
            Pipeline arguments. These should be the arguments to the
            callable source of the pipeline.

        Returns
        -------
            The value represented by the pipeline.

        Raises
        ------
        DynamicValueError
            If the pipeline is invoked without a source set.
        """
        return self._call(*args, mode=mode, **kwargs)

    def _call(
        self,
        *args: Any,
        mode: Literal["default", "source", "no-post-processors"] = "default",
        **kwargs: Any,
    ) -> Any:
        if not self.source:
            raise DynamicValueError(
                f"The dynamic value pipeline for {self.name} has no source. This likely means "
                f"you are attempting to modify a value that hasn't been created."
            )
        value = self.source(*args, **kwargs)
        if mode != "source":
            for modifier in self.modifiers:
                value = self.combiner(value, modifier, *args, **kwargs)
        if mode == "default":
            for processor in self.post_processor:
                value = processor(value, self.manager)
        if isinstance(value, pd.Series):
            value.name = self.name

        return value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """Show the full description when the pipeline is echoed in an IPython cell.

        IPython's display machinery prefers this hook over ``__repr__``, so the
        description shows up in a notebook while ``__repr__`` stays short enough
        for tracebacks and collections.
        """
        printer.text(str(self))

    def __str__(self) -> str:
        """Render everything registered on this pipeline, in application order.

        The modifier order shown is the order they are applied in, which for an
        order-dependent combiner such as ``replace_combiner`` determines the
        result. It reflects the current registration rather than a guarantee:
        adding or reordering components changes it, so it is not a contract to
        write code against.
        """
        component_name = (
            self._component.name if self._component is not None else _UNSET_COMPONENT
        )
        combiner = str(self._combiner) if self._combiner is not None else "<none>"

        def labelled(label: str, value: str) -> str:
            return f"{label:<{_LABEL_WIDTH}}{value}"

        lines = [
            f"{self.name}  [{self.RESOURCE_TYPE} pipeline]",
            labelled("registered by", component_name),
        ]
        if self.description:
            lines.append(labelled("description", self.description))
        lines += [
            "",
            labelled("source", str(self.source)),
            labelled("combiner", combiner),
        ]

        if self.modifiers:
            lines.append(
                labelled("modifiers", f"{len(self.modifiers)} (order not guaranteed)")
            )
            for modifier in self.modifiers:
                callable_name, modifier_component = modifier._describe()
                lines.append(f"{'':<{_ENTRY_INDENT}}- {callable_name}")
                lines.append(f"{'':<{_DETAIL_INDENT}}from {modifier_component}")
                if modifier.description:
                    lines.append(f"{'':<{_DETAIL_INDENT}}{modifier.description}")
        else:
            lines.append(labelled("modifiers", "none"))

        if self.post_processor:
            lines.append(labelled("post-processors", str(len(self.post_processor))))
            for order, post_processor in enumerate(self.post_processor, start=1):
                lines.append(f"{'':<{_ENTRY_INDENT}}{order}. {post_processor}")
        else:
            lines.append(labelled("post-processors", "none"))

        return "\n".join(lines)

    def __hash__(self) -> int:
        return hash(self.name)

    def get_value_modifier(
        self,
        modifier: Callable[..., Any],
        component: Component | Manager,
        required_resources: Iterable[str | Resource],
        description: str | None = None,
    ) -> ValueModifier:
        """Adds a value modifier to the pipeline and returns it.

        Parameters
        ----------
        modifier
            The value modifier callable for the ValueModifier.
        component
            The component that creates the value modifier.
        required_resources
            A list of resources required by the modifier. A string represents a population attribute.
        description
            An optional description of what the modifier does to the value.
        """
        value_modifier = ValueModifier(
            self, modifier, component, required_resources, description=description
        )
        self.modifiers.append(value_modifier)
        self._required_resources = [*self._required_resources, value_modifier]
        return value_modifier

    def set_attributes(
        self,
        component: Component | Manager,
        source: ValueSource,
        combiner: ValueCombiner,
        post_processor: list[PostProcessor],
        required_resources: Iterable[str | Resource],
        manager: ValuesManager,
        description: str | None = None,
    ) -> None:
        """
        Adds a source, combiner, post-processor, and manager to a pipeline.

        Parameters
        ----------
        component
            The component that creates the pipeline.
        source
            The source for the dynamic attribute pipeline. This can be a callable
            or a list of column names. If a list of column names is provided,
            the component that is registering this attribute producer must be the
            one that creates those columns.
        combiner
            A strategy for combining the source and modifier values into the
            final value represented by the pipeline.
        post_processor
            An optional final transformation to perform on the combined output
            of the source and modifiers.
        required_resources
            A list of resources required by the pipeline source, combiner, and
            post-processor. A string represents a population attribute.
        manager
            The simulation values manager.
        description
            An optional description of the value this pipeline represents.

        Raises
        ------
        DynamicValueError
            If a second component attempts to set the source for a pipeline that
            already has a source.
        """
        if self.source:
            raise DynamicValueError(
                f"A second component is attempting to set the source for pipeline {self.name} "
                f"with {source}, but it already has a source: {self.source}."
            )

        self._component = component
        self.source = source
        self._combiner = NamedCombiner(combiner)
        self.post_processor = [NamedPostProcessor(p) for p in post_processor]
        self._required_resources = [*self._required_resources, *required_resources]
        self._manager = manager
        self.description = description


class AttributePipeline(Pipeline):
    """A type of value pipeline for calculating simulant attributes.

    An attribute pipeline is a specific type of :class:`~vivarium.engine.framework.values.pipeline.Pipeline`
    where the source and callable must take a pd.Index of integers and return a pd.Series
    or pd.DataFrame that has that same index.

    """

    RESOURCE_TYPE = "attribute"
    """The type of the resource."""

    @property
    def is_simple(self) -> bool:
        """Whether or not this ``AttributePipeline`` is simple, i.e. it has a list
        of columns as its source and no modifiers or postprocessors."""
        return (
            isinstance(self.source, PrivateColumnValueSource)
            and not self.modifiers
            and not self.post_processor
        )

    def __init__(self, name: str, component: Component | None = None) -> None:
        super().__init__(name, component=component)
        # Re-define the post-processor type to be more specific
        self.post_processor: list[AttributePostProcessor] = []  # type: ignore[assignment]
        """A list of the transformations to perform in order on the combined output of
        the source and modifiers."""

    def __call__(  # type: ignore[override]
        self,
        index: pd.Index[int],
        mode: Literal["default", "source", "no-post-processors"] = "default",
    ) -> pd.Series[Any] | pd.DataFrame:
        """Generates the attributes represented by this pipeline.

        Arguments
        ---------
        index
            A pd.Index of integers representing the simulants for which we
            want to calculate the attribute.
        mode
            The mode for pipeline evaluation. One of "default", "source",
            or "no-post-processors".

        Returns
        -------
            A pd.Series or pd.DataFrame of attributes for the simulants in `index`.

        Raises
        ------
        DynamicValueError
            If the pipeline is invoked without a source set.
        """
        # NOTE: must pass index in as arg (NOT kwarg!) to match signature of parent Pipeline._call()
        # Always skip post-processor at _call level; AttributePipeline handles it here.
        # Pass "source" mode through so _call also skips modifiers when needed.
        _call_mode: Literal["source", "no-post-processors"] = (
            "source" if mode == "source" else "no-post-processors"
        )
        attribute = self._call(index, mode=_call_mode)
        if mode == "default":
            for processor in self.post_processor:
                attribute = processor(index, attribute, self.manager)
        if not isinstance(attribute, (pd.Series, pd.DataFrame)):
            raise DynamicValueError(
                f"The dynamic attribute pipeline for {self.name} returned a {type(attribute)} "
                "but pd.Series' or pd.DataFrames are expected for attribute pipelines."
            )
        if not attribute.index.equals(index):
            raise DynamicValueError(
                f"The dynamic attribute pipeline for {self.name} returned a series "
                "or dataframe with a different index than was passed in. "
                f"\nReturned index: {attribute.index}"
                f"\nExpected index: {index}"
            )
        return attribute
