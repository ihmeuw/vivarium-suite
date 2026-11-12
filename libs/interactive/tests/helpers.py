"""Mock components for the tests, copied from vivarium-engine's test helpers.

vivarium-engine does not ship its tests, so this package keeps its own copy of the
components that its tests use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from vivarium.engine import Component
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.population import SimulantData
from vivarium.engine.framework.results import VALUE_COLUMN
from vivarium.engine.framework.results.observer import Observer


class ColumnCreator(Component):
    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_test_columns,
            columns=["test_column_1", "test_column_2", "test_column_3"],
        )

    def initialize_test_columns(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(self.get_initial_state(pop_data.index))

    def get_initial_state(self, index: pd.Index[int]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                column: [i % 3 for i in index]
                for column in ["test_column_1", "test_column_2", "test_column_3"]
            },
            index=index,
        )


class SingleColumnCreator(ColumnCreator):
    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_test_columns, columns=["test_column_1"]
        )

    def get_initial_state(self, index: pd.Index[int]) -> pd.DataFrame:
        return pd.DataFrame(
            {"test_column_1": [i % 3 for i in index]},
            index=index,
        )


class MultiLevelSingleColumnCreator(Component):
    def setup(self, builder: Builder) -> None:
        builder.value.register_attribute_producer(
            "some_attribute",
            lambda idx: pd.DataFrame({"some_column": [i % 3 for i in idx]}, index=idx),
        )


class MultiLevelMultiColumnCreator(Component):
    def setup(self, builder: Builder) -> None:
        builder.value.register_attribute_producer(
            "some_attribute",
            lambda idx: pd.DataFrame(
                {"column_1": [i % 3 for i in idx], "column_2": [i % 3 for i in idx]},
                index=idx,
            ),
        )
        builder.value.register_attribute_producer(
            "some_other_attribute",
            lambda idx: pd.DataFrame({"column_3": [i % 3 for i in idx]}, index=idx),
        )


class AttributePipelineCreator(Component):
    """A helper class to register different types of attribute pipelines.

    It does NOT create any private columns; use the ColumnCreator class for that.

    """

    def setup(self, builder: Builder) -> None:

        # Simple attributes
        builder.value.register_attribute_producer(
            "attribute_generating_columns_4_5",
            lambda idx: pd.DataFrame(
                {
                    "test_column_4": [i % 3 for i in idx],
                    "test_column_5": [i % 3 for i in idx],
                },
                index=idx,
            ),
        )
        builder.value.register_attribute_producer(
            "attribute_generating_column_8",
            lambda idx: pd.DataFrame({"test_column_8": [i % 3 for i in idx]}, index=idx),
        )

        # Non-simple attributes
        # For this test, we make them non-simple by registering a modifer that doesn't actually modify anything
        builder.value.register_attribute_producer(
            "test_attribute",
            lambda idx: pd.Series([i % 3 for i in idx], index=idx),
        )
        builder.value.register_attribute_producer(
            "attribute_generating_columns_6_7",
            lambda idx: pd.DataFrame(
                {
                    "test_column_6": [i % 3 for i in idx],
                    "test_column_7": [i % 3 for i in idx],
                },
                index=idx,
            ),
        )
        builder.value.register_attribute_modifier(
            "test_attribute",
            lambda index, series: series,
        )
        builder.value.register_attribute_modifier(
            "attribute_generating_columns_6_7",
            lambda index, df: df,
        )


class DiamondAttributeCreator(Component):
    """A helper class to register attributes whose dependencies reconverge.

    ``diamond_top`` requires ``diamond_left`` and ``diamond_right``, both of
    which require ``diamond_base``, which in turn requires ``diamond_root``. So
    ``diamond_base`` is reached along two paths and still has a dependency of
    its own below it.

    """

    def setup(self, builder: Builder) -> None:
        for name, required_resources in [
            ("diamond_root", []),
            ("diamond_base", ["diamond_root"]),
            ("diamond_left", ["diamond_base"]),
            ("diamond_right", ["diamond_base"]),
            ("diamond_top", ["diamond_left", "diamond_right"]),
        ]:
            builder.value.register_attribute_producer(
                name, self.source, required_resources=required_resources
            )

    def source(self, index: pd.Index[int]) -> pd.Series[Any]:
        return pd.Series([i % 3 for i in index], index=index)


class OrderedDependencyCreator(Component):
    """A helper class whose attribute depends on one resource of several types.

    ``ordered`` reads an attribute, a value pipeline, and a lookup table, and
    carries a modifier of its own. Those four types are the ones whose rendered
    order differs from a plain alphabetical sort of their resource ids.

    """

    CONFIGURATION_DEFAULTS = {
        "ordered_dependency_creator": {"data_sources": {"ordered_table": 0.4}}
    }

    def setup(self, builder: Builder) -> None:
        table = self.build_lookup_table(builder, "ordered_table", value_columns="scalar")
        value = builder.value.register_value_producer("ordered_value", self.source)
        builder.value.register_attribute_producer("ordered_attribute", self.source)
        builder.value.register_attribute_producer(
            "ordered", self.source, required_resources=[table, value, "ordered_attribute"]
        )
        builder.value.register_attribute_modifier("ordered", self.modifier)

    def source(self, index: pd.Index[int]) -> pd.Series[Any]:
        return pd.Series([i % 3 for i in index], index=index)

    def modifier(self, index: pd.Index[int], values: pd.Series[Any]) -> pd.Series[Any]:
        return values


class ReversedNameCreator(Component):
    """A helper class whose attribute reads two attributes in reverse name order.

    ``reversed_root`` requires ``zulu`` and then ``alpha``, so the order the
    dependencies were registered in is the reverse of the order their names sort
    in.

    """

    def setup(self, builder: Builder) -> None:
        for name in ["zulu", "alpha"]:
            builder.value.register_attribute_producer(name, self.source)
        builder.value.register_attribute_producer(
            "reversed_root", self.source, required_resources=["zulu", "alpha"]
        )

    def source(self, index: pd.Index[int]) -> pd.Series[Any]:
        return pd.Series([i % 3 for i in index], index=index)


class ManyModifierCreator(Component):
    """A helper class that puts more than nine modifiers on one value pipeline.

    The number Vivarium builds into a modifier's name is the order it is applied
    in. Past the ninth that number is two digits wide, so reading the name as
    text puts the tenth modifier ahead of the second.

    """

    MODIFIER_COUNT = 12

    def setup(self, builder: Builder) -> None:
        builder.value.register_value_producer("many_modifiers", self.source)
        for number in range(self.MODIFIER_COUNT):
            builder.value.register_value_modifier(
                "many_modifiers", modifier=self._modifier(number)
            )

    def source(self, index: pd.Index[int]) -> pd.Series[Any]:
        return pd.Series(1.0, index=index)

    def _modifier(self, number: int) -> Callable[..., Any]:
        def modifier(index: pd.Index[int], values: pd.Series[Any]) -> pd.Series[Any]:
            return values

        modifier.__name__ = f"modifier_{number:02d}"
        return modifier


class NestedAttributeCreator(Component):
    """A helper class to register nested pipelines."""

    def setup(self, builder: Builder) -> None:

        builder.value.register_attribute_producer(
            "outer",
            self.outer_source,
        )

        builder.population.register_initializer(
            initializer=self.initialize_inner,
            columns="inner",
        )

    def initialize_inner(self, pop_data: SimulantData) -> None:
        inner = pd.Series([i % 3 for i in pop_data.index], index=pop_data.index, name="inner")
        self.population_view.initialize(inner)

    def outer_source(self, idx: pd.Index[int]) -> pd.DataFrame:
        """Calls another pipeline to generate a dataframe"""
        inner = self.population_view.get_frame(idx, "inner")
        outer = pd.DataFrame(inner)
        outer.rename(columns={"inner": "outer"}, inplace=True)
        return pd.concat([inner, outer], axis=1)


class NestedLookupCaller(NestedAttributeCreator):
    """Like NestedAttributeCreator but outer_source calls a lookup table keyed on 'inner'."""

    def setup(self, builder: Builder) -> None:
        super().setup(builder)
        self.inner_lookup = self.build_lookup_table(
            builder,
            "inner_lookup",
            data_source=pd.DataFrame({"inner": [0, 1, 2], "value": [10, 20, 30]}),
        )

    def outer_source(self, idx: pd.Index[int]) -> pd.DataFrame:
        """Call a lookup table inside a nested pipeline evaluation."""
        looked_up = self.inner_lookup(idx)
        return pd.DataFrame({"lookup_value": looked_up})


class ColumnCreatorAndRequirer(Component):
    def setup(self, builder: Builder) -> None:
        self.pipeline = builder.value.get_value("pipeline_1")
        self.randomness = builder.randomness.get_stream("stream_1")
        builder.population.register_initializer(
            initializer=self.initialize_test_column_4,
            columns="test_column_4",
            required_resources=["test_column_2", self.pipeline, self.randomness],
        )

    def initialize_test_column_4(self, pop_data: SimulantData) -> None:
        initialization_data = pd.DataFrame({"test_column_4": 8}, index=pop_data.index)
        self.population_view.initialize(initialization_data)


STUDENT_HOUSES = pd.Series(["gryffindor", "slytherin", "ravenclaw"])

FAMILIARS = ["owl", "cat", "gecko", "banana_slug", "unladen_swallow"]

POWER_LEVELS = [20, 40, 60, 80]

POWER_LEVEL_BIN_EDGES = [0, 25, 50, 75, 100]

POWER_LEVEL_GROUP_LABELS = ["low", "medium", "high", "very high"]

HARRY_POTTER_CONFIG = {
    "time": {
        "start": {"year": 2024, "month": 4, "day": 22},
        "end": {"year": 2029, "month": 4, "day": 22},
        "step_size": 365,  # Days
    },
    "stratification": {
        "default": ["student_house", "power_level_group"],
    },
}

_RNG_SEED = 42


class Hogwarts(Component):
    def setup(self, builder: Builder) -> None:
        self.rng = np.random.default_rng(_RNG_SEED)
        builder.value.register_attribute_producer(
            "grade",
            source=lambda index: self.population_view.get(index, "exam_score").map(
                lambda x: x // 10
            ),
            required_resources=["exam_score"],
        )
        builder.value.register_attribute_producer(
            "double_power",
            source=lambda index: self.population_view.get(index, "power_level") * 2,
            required_resources=["power_level"],
        )
        builder.population.register_initializer(
            initializer=self.initialize_hogwarts,
            columns=[
                "student_id",
                "student_house",
                "familiar",
                "power_level",
                "house_points",
                "quidditch_wins",
                "exam_score",
                "spell_power",
                "potion_power",
            ],
        )

    def initialize_hogwarts(self, pop_data: SimulantData) -> None:
        size = len(pop_data.index)
        initialization_data = pd.DataFrame(
            {
                "student_id": list(range(size)),
                "student_house": self.rng.choice(STUDENT_HOUSES, size=size),
                "familiar": self.rng.choice(FAMILIARS, size=size),
                "power_level": self.rng.choice([lvl for lvl in POWER_LEVELS], size=size),
                "house_points": 0,
                "quidditch_wins": 0,
                "exam_score": 0.0,
            },
            index=pop_data.index,
        )
        # Assume power level components are evenly split
        initialization_data["spell_power"] = initialization_data["power_level"] / 2
        initialization_data["potion_power"] = initialization_data["power_level"] / 2
        self.population_view.initialize(initialization_data)

    def on_time_step(self, pop_data: Event) -> None:
        def _apply_updates(df: pd.DataFrame) -> pd.DataFrame:
            attrs = self.population_view.get(
                pop_data.index,
                ["student_house", "power_level", "familiar"],
                include_untracked=True,
            )
            result = df.loc[pop_data.index]
            result["house_points"] = 0
            result["quidditch_wins"] = 0
            # House points are stratified by 'student_house' and 'power_level_group'.
            # Let's have each wizard of gryffindor and of power level 20 or 80
            # gain a point on each time step.
            result.loc[
                (attrs["student_house"] == "gryffindor")
                & (attrs["power_level"].isin([20, 80])),
                "house_points",
            ] = 1
            # Quidditch wins are stratified by 'familiar'.
            # Let's have each wizard with a banana slug familiar gain a point
            # on each time step.
            result.loc[attrs["familiar"] == "banana_slug", "quidditch_wins"] = 1
            # Update everyones test score to increase by 10 points per time step
            result["exam_score"] += 10.0
            return result

        self.population_view.update(
            ["house_points", "quidditch_wins", "exam_score"],
            _apply_updates,
        )


class HousePointsObserver(Observer):
    """Observer that is stratified by multiple columns (the defaults,
    'student_house' and 'power_level_group')
    """

    def register_observations(self, builder: Builder) -> None:
        builder.results.register_adding_observation(
            name="house_points",
            aggregator_sources=["house_points"],
            aggregator=lambda df: df.sum(),
            requires_attributes=["house_points"],
            results_formatter=results_formatter,
        )


class HogwartsResultsStratifier(Component):
    def setup(self, builder: Builder) -> None:
        builder.results.register_stratification(
            name="student_house",
            categories=list(STUDENT_HOUSES),
            requires_attributes=["student_house"],
        )
        builder.results.register_stratification(
            name="familiar", categories=FAMILIARS, requires_attributes=["familiar"]
        )
        builder.results.register_binned_stratification(
            "power_level",
            "power_level_group",
            POWER_LEVEL_BIN_EDGES,
            POWER_LEVEL_GROUP_LABELS,
        )


def results_formatter(
    measure: str,
    results: pd.DataFrame,
) -> pd.DataFrame:
    """An test use case of an observer's report method that writes a DataFrame to a CSV file."""
    # Add extra cols
    results["measure"] = measure
    # Sort the columns such that the stratifications (index) are first
    # and VALUE_COLUMN is last and sort the rows by the stratifications.
    other_cols = [c for c in results.columns if c != VALUE_COLUMN]
    return results[other_cols + [VALUE_COLUMN]].sort_index().reset_index()


def assert_squeezing_multi_level_single_outer_multi_inner(
    unsqueezed: pd.Series[Any] | pd.DataFrame, squeezed: pd.Series[Any] | pd.DataFrame
) -> None:
    assert isinstance(unsqueezed, pd.DataFrame)
    assert isinstance(unsqueezed.columns, pd.MultiIndex)
    assert isinstance(squeezed, pd.DataFrame)
    assert not isinstance(squeezed.columns, pd.MultiIndex)
    assert squeezed.equals(unsqueezed.droplevel(0, axis=1))


def assert_squeezing_multi_level_single_outer_single_inner(
    unsqueezed: pd.Series[Any] | pd.DataFrame,
    squeezed: pd.Series[Any] | pd.DataFrame,
    column: tuple[str, str] = ("attribute_generating_column_8", "test_column_8"),
) -> None:
    assert isinstance(unsqueezed, pd.DataFrame)
    assert isinstance(unsqueezed.columns, pd.MultiIndex)
    assert isinstance(squeezed, pd.Series)
    assert unsqueezed[column].equals(squeezed)


def assert_squeezing_single_level_single_col(
    unsqueezed: pd.Series[Any] | pd.DataFrame,
    squeezed: pd.Series[Any] | pd.DataFrame,
    column: str = "test_column_1",
) -> None:
    assert isinstance(unsqueezed, pd.DataFrame)
    assert not isinstance(unsqueezed.columns, pd.MultiIndex)
    assert isinstance(squeezed, pd.Series)
    assert unsqueezed[column].equals(squeezed)
