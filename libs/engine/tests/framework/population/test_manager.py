from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
import pytest
from pytest_mock import MockerFixture

from tests.framework.population.conftest import CUBE_COL_NAMES, PIE_COL_NAMES, PIE_RECORDS
from tests.framework.population.helpers import (
    assert_squeezing_multi_level_multi_outer,
    assert_squeezing_multi_level_single_outer_multi_inner,
    assert_squeezing_multi_level_single_outer_single_inner,
    assert_squeezing_single_level_multi_col,
    assert_squeezing_single_level_single_col,
)
from tests.helpers import (
    AttributePipelineCreator,
    ColumnCreator,
    ColumnCreatorAndRequirer,
    MultiLevelMultiColumnCreator,
    MultiLevelSingleColumnCreator,
    SingleColumnCreator,
)
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.population.exceptions import PopulationError
from vivarium.engine.framework.population.manager import PopulationManager, SimulantData

INITIAL_SIZE = 6
ADDED = 4
NEW_INDEX = pd.RangeIndex(INITIAL_SIZE, INITIAL_SIZE + ADDED)


class InitializingComponent(Component):
    @property
    def name(self) -> str:
        return self._name

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def initializer(self, simulant_data: SimulantData) -> None:
        pass

    def other_initializer(self, simulant_data: SimulantData) -> None:
        pass


@pytest.mark.parametrize("private_columns", [[], ["age", "sex"]])
def test_setting_columns_with_get_view(
    private_columns: list[str], mocker: MockerFixture
) -> None:
    manager = PopulationManager()
    component = mocker.Mock()
    component.name = "test_component"
    manager._private_column_metadata["test_component"] = private_columns
    view = manager._get_view(component=component)
    assert view.private_columns == private_columns


@pytest.mark.parametrize("attributes", ("all", PIE_COL_NAMES, ["pie", "cube"]))
@pytest.mark.parametrize("index", [None, pd.RangeIndex(0, len(PIE_RECORDS) // 2)])
@pytest.mark.parametrize("query", [None, "pie == 'apple'"])
def test_get_population(
    attributes: Literal["all"] | list[str],
    index: pd.Index[int] | None,
    query: str,
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    kwargs: dict[str, Any] = {"attributes": attributes}
    if index is not None:
        kwargs["index"] = index
    if query is not None:
        kwargs["query"] = query
    assert attributes == "all" or isinstance(attributes, list)
    pop = pies_and_cubes_pop_mgr.get_population(**kwargs)
    assert (
        set(pop.columns) == set(PIE_COL_NAMES + CUBE_COL_NAMES)
        if attributes == "all"
        else set(attributes)
    )
    if query is not None:
        assert (pop["pie"] == "apple").all()


def test_get_population_different_attribute_types() -> None:
    """Test that get_population works with simple attributes, non-simple attributes,
    and attribute pipelines that return dataframes instead of series'."""
    component1 = ColumnCreator()
    component2 = AttributePipelineCreator()
    sim = InteractiveContext(components=[component1, component2], setup=True)
    pop = sim._population.get_population("all")
    # We have columnar multi-index due to AttributePipelines that return dataframes
    assert isinstance(pop.columns, pd.MultiIndex)
    assert set(pop.columns) == {
        ("test_column_1", ""),
        ("test_column_2", ""),
        ("test_column_3", ""),
        ("attribute_generating_columns_4_5", "test_column_4"),
        ("attribute_generating_columns_4_5", "test_column_5"),
        ("attribute_generating_column_8", "test_column_8"),
        ("test_attribute", ""),
        ("attribute_generating_columns_6_7", "test_column_6"),
        ("attribute_generating_columns_6_7", "test_column_7"),
    }
    value_cols = [col for col in pop.columns if col != ("simulant_step_size", "")]
    expected = pd.Series([idx % 3 for idx in pop.index])
    for col in value_cols:
        pd.testing.assert_series_equal(pop[col], expected, check_names=False)


class TestGetPopulationSqueezing:
    """Tests for squeeze behavior on get_population with specific columns."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(components=[ColumnCreator(), AttributePipelineCreator()])

    def assert_squeezing(
        self,
        sim: InteractiveContext,
        columns: list[str] | Literal["all"],
        assert_fn: Any,
        *assert_args: Any,
    ) -> None:
        unsqueezed = sim._population.get_population(columns, squeeze=False)
        squeezed = sim._population.get_population(columns, squeeze=True)
        assert_fn(unsqueezed, squeezed, *assert_args)

    def test_single_level_single_column_returns_series(self, sim: InteractiveContext) -> None:
        self.assert_squeezing(
            sim, ["test_column_1"], assert_squeezing_single_level_single_col
        )

    def test_single_level_multi_column_returns_dataframe(
        self, sim: InteractiveContext
    ) -> None:
        self.assert_squeezing(
            sim, ["test_column_1", "test_column_2"], assert_squeezing_single_level_multi_col
        )

    def test_multi_level_single_outer_single_inner_returns_series(
        self, sim: InteractiveContext
    ) -> None:
        self.assert_squeezing(
            sim,
            ["attribute_generating_column_8"],
            assert_squeezing_multi_level_single_outer_single_inner,
        )

    def test_multi_level_single_outer_multi_inner_returns_inner_dataframe(
        self, sim: InteractiveContext
    ) -> None:
        self.assert_squeezing(
            sim,
            ["attribute_generating_columns_4_5"],
            assert_squeezing_multi_level_single_outer_multi_inner,
        )

    def test_multi_level_multi_outer_returns_full_dataframe(
        self, sim: InteractiveContext
    ) -> None:
        self.assert_squeezing(
            sim,
            ["test_column_1", "attribute_generating_columns_6_7"],
            assert_squeezing_multi_level_multi_outer,
        )

    def test_all_columns_single_level_single_column_returns_series(self) -> None:
        sim = InteractiveContext(components=[SingleColumnCreator()])
        self.assert_squeezing(
            sim, "all", assert_squeezing_single_level_single_col, "test_column_1"
        )

    def test_all_columns_single_level_multi_column_returns_dataframe(self) -> None:
        sim = InteractiveContext(components=[ColumnCreator()])
        self.assert_squeezing(sim, "all", assert_squeezing_single_level_multi_col)

    def test_all_columns_multi_level_single_outer_single_inner_returns_series(self) -> None:
        sim = InteractiveContext(components=[MultiLevelSingleColumnCreator()])
        self.assert_squeezing(
            sim,
            "all",
            assert_squeezing_multi_level_single_outer_single_inner,
            ("some_attribute", "some_column"),
        )

    def test_all_columns_multi_level_single_outer_multi_inner_returns_inner_dataframe(
        self,
    ) -> None:
        sim = InteractiveContext(components=[MultiLevelMultiColumnCreator()])
        sim._population._attribute_pipelines.pop("some_other_attribute")
        self.assert_squeezing(
            sim, "all", assert_squeezing_multi_level_single_outer_multi_inner
        )

    def test_all_columns_multi_level_multi_outer_returns_full_dataframe(self) -> None:
        sim = InteractiveContext(components=[ColumnCreator(), AttributePipelineCreator()])
        self.assert_squeezing(sim, "all", assert_squeezing_multi_level_multi_outer)


@pytest.mark.parametrize("include_duplicates", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        None,  # default
        "test_column_1 < 2",  # query on a requested column
        "test_column_2 < 2",  # query on a non-requested column
    ],
)
def test_get_population_column_ordering(include_duplicates: bool, query: str | None) -> None:
    def _extract_ordered_list(cols: list[str]) -> list[tuple[str, str]]:
        col_mapping = {
            "test_column_1": ("test_column_1", ""),
            "attribute_generating_columns_4_5": [
                ("attribute_generating_columns_4_5", "test_column_4"),
                ("attribute_generating_columns_4_5", "test_column_5"),
            ],
            "test_attribute": ("test_attribute", ""),
        }
        expected_cols = []
        for col in cols:
            col_tuple = col_mapping[col]
            if isinstance(col_tuple, list):
                for item in col_tuple:
                    if item not in expected_cols:
                        expected_cols.append(item)
            else:
                if col_tuple not in expected_cols:
                    expected_cols.append(col_tuple)
        return expected_cols

    def _check_col_ordering(
        sim: InteractiveContext, kwargs: dict[str, str | list[str]]
    ) -> None:
        pop = sim._population.get_population(**kwargs)  # type: ignore[call-overload]
        expected_cols = _extract_ordered_list(cols)
        assert isinstance(pop.columns, pd.MultiIndex)
        returned_cols = pop.columns.tolist()
        assert returned_cols == expected_cols

    component1 = ColumnCreator()
    component2 = AttributePipelineCreator()
    sim = InteractiveContext(components=[component1, component2], setup=True)

    cols = ["test_column_1", "attribute_generating_columns_4_5", "test_attribute"]
    if include_duplicates:
        cols.extend(cols)  # duplicate the list
    kwargs: dict[str, str | list[str]] = {}
    kwargs["attributes"] = cols
    if query is not None:
        kwargs["query"] = query
    _check_col_ordering(sim, kwargs)
    # Now try reversing the order
    # NOTE: we specifically do not parametrize this test to ensure that the two
    # 'get_population' calls are happening on exactly the same population manager
    cols.reverse()
    _check_col_ordering(sim, kwargs)


@pytest.mark.parametrize(
    "attributes",
    (
        ["age", "sex"],
        PIE_COL_NAMES + ["age", "sex"],
        ["age", "sex"],
        ["color", "count", "age"],
    ),
)
def test_get_population_raises_missing_attributes(
    attributes: list[str], pies_and_cubes_pop_mgr: PopulationManager
) -> None:
    with pytest.raises(PopulationError, match="not in population state table"):
        pies_and_cubes_pop_mgr.get_population(attributes)


def test_get_population_raises_bad_string(pies_and_cubes_pop_mgr: PopulationManager) -> None:
    with pytest.raises(TypeError, match="Attributes must be a list of strings or 'all'"):
        pies_and_cubes_pop_mgr.get_population("invalid_string")  # type: ignore[call-overload]


def test__get_attributes_three_or_more_levels_not_implemented() -> None:
    class BadAttributeCreator(Component):
        def setup(self, builder: Builder) -> None:
            builder.value.register_attribute_producer(
                "animals",
                lambda idx: pd.DataFrame(
                    {
                        ("cat", "size"): "teeny-tiny",
                        ("cat", "color"): "tuxedo",
                        ("dog", "size"): "huge",
                        ("dog", "color"): "spotted",
                    },
                    index=idx,
                ),
            )

    sim = InteractiveContext(components=[BadAttributeCreator()], setup=True)
    with pytest.raises(
        NotImplementedError,
        match="Multi-level columns in attribute pipeline outputs are not supported.",
    ):
        sim._population.get_population(["animals"])


def test_get_population_deduplicates_requested_columns(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    pop = pies_and_cubes_pop_mgr.get_population(["pie", "pie", "pie"], squeeze=False)
    assert set(pop.columns) == {"pie"}


def test_register_initializer(mocker: MockerFixture) -> None:
    class ColumnCreator2(ColumnCreator):
        @property
        def name(self) -> str:
            return "column_creator_2"

    class ColumnCreator3(ColumnCreator):
        @property
        def name(self) -> str:
            return "column_creator_3"

    # The metadata for the manager should be empty because the fixture does not
    # actually go through setup.
    mgr = PopulationManager()
    mock_register_attr = mocker.Mock()
    mocker.patch.object(mgr, "_register_attribute_producer", mock_register_attr, create=True)
    mock_resources = mocker.Mock()
    mocker.patch.object(mgr, "resources", mock_resources, create=True)
    mock_add_private_cols = mocker.Mock()
    mocker.patch.object(
        mgr.resources, "add_private_columns", mock_add_private_cols, create=True
    )

    assert mgr._private_column_metadata == {}

    component1 = ColumnCreator()
    mocker.patch.object(
        mgr, "_get_current_component_or_manager", return_value=component1, create=True
    )
    mgr.register_initializer(
        initializer=component1.initialize_test_columns,
        columns=["foo", "bar"],
        required_resources=["dep1", "dep2"],
    )

    component2 = ColumnCreator2()
    mocker.patch.object(
        mgr, "_get_current_component_or_manager", return_value=component2, create=True
    )
    mgr.register_initializer(
        initializer=component2.initialize_test_columns,
        columns=None,
        required_resources=["dep3", "dep4"],
    )

    component3 = ColumnCreator3()
    mocker.patch.object(
        mgr, "_get_current_component_or_manager", return_value=component3, create=True
    )
    mgr.register_initializer(
        initializer=component3.initialize_test_columns, columns="qux", required_resources=[]
    )

    # Check that register_attribute_producer was called appropriately
    assert mock_register_attr.call_count == 3
    for column in ["foo", "bar", "qux"]:
        mock_register_attr.assert_any_call(
            column, source=[column], source_is_private_column=True
        )

    # Check the private column metadata
    assert mgr._private_column_metadata == {
        component1.name: ["foo", "bar"],
        component2.name: [],
        component3.name: ["qux"],
    }

    # Check that resources.add_private_columns was called appropriately
    assert mock_add_private_cols.call_count == 3
    mock_add_private_cols.assert_any_call(
        columns=["foo", "bar"],
        required_resources=["dep1", "dep2"],
        initializer=component1.initialize_test_columns,
    )
    mock_add_private_cols.assert_any_call(
        columns=[],
        required_resources=["dep3", "dep4"],
        initializer=component2.initialize_test_columns,
    )
    mock_add_private_cols.assert_any_call(
        columns=["qux"], required_resources=[], initializer=component3.initialize_test_columns
    )


def test_register_initializer_duplicate_raises(mocker: MockerFixture) -> None:
    component = ColumnCreator()
    mgr = PopulationManager()
    mocker.patch.object(
        mgr, "_get_current_component_or_manager", return_value=component, create=True
    )
    mocker.patch.object(mgr, "_register_attribute_producer", mocker.Mock(), create=True)
    mock_resources = mocker.Mock()
    mocker.patch.object(mgr, "resources", mock_resources, create=True)

    # First registration should succeed
    mgr.register_initializer(initializer=component.initialize_test_columns, columns=["col_a"])

    # Registering the same initializer again should raise
    with pytest.raises(PopulationError, match="has already been registered"):
        mgr.register_initializer(
            initializer=component.initialize_test_columns, columns=["col_b"]
        )


@pytest.mark.parametrize(
    "components, index, columns",
    [
        ([ColumnCreator(), ColumnCreatorAndRequirer()], None, None),
        ([ColumnCreator()], pd.Index([4, 8, 15, 16, 23, 42]), None),
        ([ColumnCreator()], None, ["test_column_2"]),
        (
            [ColumnCreator()],
            pd.Index([4, 8, 15, 16, 23, 42]),
            ["test_column_1", "test_column_3"],
        ),
    ],
)
def test_get_private_columns(
    components: list[Component], index: pd.Index[int] | None, columns: list[str] | None
) -> None:
    sim = InteractiveContext(components=components)
    kwargs: dict[str, pd.Index[int] | list[str]] = {}
    if index is not None:
        kwargs["index"] = index
    if columns is not None:
        kwargs["columns"] = columns
    for component in components:
        private_columns = pd.DataFrame(sim._population.get_private_columns(component, **kwargs))  # type: ignore[arg-type]
        if index is not None:
            assert private_columns.index.equals(index)
        else:
            assert private_columns.index.equals(sim._population.get_population_index())
        if columns is not None:
            assert list(private_columns.columns) == columns
        else:
            assert list(private_columns.columns) == component.private_columns


def test_get_private_columns_squeezing() -> None:

    # Single-level, single-column -> series
    single_col_creator = SingleColumnCreator()
    sim = InteractiveContext(components=[single_col_creator], setup=True)
    unsqueezed = sim._population.get_private_columns(
        single_col_creator, columns=["test_column_1"]
    )
    squeezed = sim._population.get_private_columns(
        single_col_creator, columns="test_column_1"
    )
    assert_squeezing_single_level_single_col(unsqueezed, squeezed)
    default = sim._population.get_private_columns(single_col_creator)
    assert isinstance(default, pd.Series) and isinstance(squeezed, pd.Series)
    assert default.equals(squeezed)

    # Single-level, multiple-column -> dataframe
    col_creator = ColumnCreator()
    sim = InteractiveContext(components=[col_creator], setup=True)
    # There's no way to squeeze here.
    df = sim._population.get_private_columns(
        col_creator, columns=["test_column_1", "test_column_2", "test_column_3"]
    )
    assert isinstance(df, pd.DataFrame)
    assert not isinstance(df.columns, pd.MultiIndex)
    default = sim._population.get_private_columns(col_creator)
    assert isinstance(default, pd.DataFrame)
    assert default.equals(df)


def test_get_private_columns_raises_for_a_column_not_yet_created() -> None:
    """A column the component owns but no initializer has created yet is an error.

    Distinct from the access error below: here the request is legitimate and the
    column simply does not exist yet, which is the state during initial creation.
    """
    mgr = PopulationManager()
    component = ColumnCreator()
    mgr._private_column_metadata[component.name] = ["test_column_1"]
    mgr._private_columns = pd.DataFrame()

    with pytest.raises(PopulationError, match="have not been created"):
        mgr.get_private_columns(component, columns=["test_column_1"])


def test_get_private_columns_raises_bad_column_request() -> None:
    mgr = PopulationManager()
    with pytest.raises(
        PopulationError,
        match="is requesting the following private columns to which it does not have access",
    ):
        mgr.get_private_columns(ColumnCreator(), columns=["foo"])


def test_get_population_index() -> None:
    component = AttributePipelineCreator()
    sim = InteractiveContext(components=[component], setup=False)
    with pytest.raises(PopulationError, match="Population has not been initialized."):
        sim._population.get_population_index()
    sim.setup()
    private_cols = pd.DataFrame(sim._population._private_columns)
    private_cols.index.equals(sim._population.get_population_index())


def test_forget_to_create_columns() -> None:
    class ColumnForgetter(ColumnCreator):
        def initialize_test_columns(self, pop_data: SimulantData) -> None:
            pass

    with pytest.raises(PopulationError, match="not actually created"):
        InteractiveContext(components=[ColumnForgetter()])


def test_create_already_existing_columns_fails() -> None:
    class SameColumnCreator(ColumnCreator):
        ...

    with pytest.raises(
        PopulationError,
        match="Component 'same_column_creator' is attempting to register private column 'test_column_1' but it is already registered by component 'column_creator'.",
    ):
        InteractiveContext(components=[ColumnCreator(), SameColumnCreator()])


def test_register_tracked_query(mocker: MockerFixture) -> None:
    mgr = PopulationManager()
    assert mgr.tracked_queries == []
    mgr.register_tracked_query("foo == 'bar'")
    assert mgr.tracked_queries == ["foo == 'bar'"]
    mgr.register_tracked_query("cat != dog")
    assert mgr.tracked_queries == ["foo == 'bar'", "cat != dog"]
    # Check duplicates are ignored
    mocker.patch.object(mgr, "logger", mocker.Mock(), create=True)
    mgr.register_tracked_query("foo == 'bar'")
    mgr.logger.warning.assert_called_once()  # type: ignore[attr-defined]
    assert mgr.tracked_queries == ["foo == 'bar'", "cat != dog"]


############################
# PopulationManager.update #
############################


def test_update_full_index_writes_every_row(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """An update covering the whole population writes every row of those columns."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    update = pd.DataFrame({"pi": original["pi"] * 2, "cube": original["cube"] + 1})
    assert update.index.equals(original.index)

    pies_and_cubes_pop_mgr.update(update)

    updated = pies_and_cubes_pop_mgr.private_columns
    pd.testing.assert_frame_equal(updated[["pi", "cube"]], update)
    pd.testing.assert_frame_equal(
        updated[["pie", "cube_string"]], original[["pie", "cube_string"]]
    )


def test_create_columns_uses_the_initializers_dtype(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """A created column arrives at the dtype its initializer produced."""
    committed = pies_and_cubes_pop_mgr.private_columns.index
    staged_index = pd.RangeIndex(len(committed), len(committed) + 3)
    pies_and_cubes_pop_mgr._staged_simulants = pd.DataFrame(index=staged_index)
    data = pd.DataFrame({"a_bool": True, "an_int": 7, "a_string": "spam"}, index=staged_index)

    pies_and_cubes_pop_mgr.create_columns(data)

    staged = pies_and_cubes_pop_mgr.staged_simulants
    pd.testing.assert_frame_equal(staged, data)
    # Writing into rows could not have created these, let alone at these dtypes.
    assert staged["a_bool"].dtype == np.dtype("bool")
    assert staged["an_int"].dtype == np.dtype("int64")


def test_update_partial_index_writes_only_those_rows(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """An update covering some simulants leaves every other row untouched."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    index: pd.Index[int] = original.index[::2]
    omitted = original.index.difference(index)
    update = pd.DataFrame({"pi": original.loc[index, "pi"] * 2})

    pies_and_cubes_pop_mgr.update(update)

    updated = pies_and_cubes_pop_mgr.private_columns
    pd.testing.assert_series_equal(updated.loc[index, "pi"], original.loc[index, "pi"] * 2)
    pd.testing.assert_series_equal(updated.loc[omitted, "pi"], original.loc[omitted, "pi"])
    pd.testing.assert_frame_equal(updated.drop(columns=["pi"]), original.drop(columns=["pi"]))


def test_update_partial_index_does_not_null_omitted_rows(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """A partial update introduces no nulls in the rows it omits."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    assert not original.isna().to_numpy().any()
    index: pd.Index[int] = original.index[:3]
    omitted = original.index.difference(index)
    update = pd.DataFrame({"pie": "banana_cream", "pi": 0.0}, index=index)

    pies_and_cubes_pop_mgr.update(update)

    updated = pies_and_cubes_pop_mgr.private_columns
    assert not updated.loc[omitted, PIE_COL_NAMES].isna().to_numpy().any()
    assert not updated.isna().to_numpy().any()


@pytest.mark.parametrize("column", ["pie", "pi", "cube", "cube_string"])
def test_update_partial_index_preserves_dtype(
    pies_and_cubes_pop_mgr: PopulationManager, column: str
) -> None:
    """A partial update leaves the written column's dtype unchanged."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    index: pd.Index[int] = original.index[::3]
    # Relabelling the reversed rows changes most values without changing dtype.
    update = original.loc[index[::-1], [column]].set_axis(index, axis="index")
    expected = original[column].copy()
    expected.loc[index] = update[column]

    pies_and_cubes_pop_mgr.update(update)

    updated = pies_and_cubes_pop_mgr.private_columns
    assert updated[column].dtype == original[column].dtype
    pd.testing.assert_series_equal(updated[column], expected)


def test_update_partial_index_empty_is_noop(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """An update with an empty index changes nothing."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    update = original.loc[original.index[:0], PIE_COL_NAMES]

    pies_and_cubes_pop_mgr.update(update)

    pd.testing.assert_frame_equal(pies_and_cubes_pop_mgr.private_columns, original)


def test_update_partial_index_unordered(
    pies_and_cubes_pop_mgr: PopulationManager,
) -> None:
    """A partial update whose index is not in population order writes the right rows."""
    original = pies_and_cubes_pop_mgr.private_columns.copy()
    index = pd.Index([7, 2, 19, 5])
    omitted = original.index.difference(index)
    # Each row gets a distinct value, so writing them in population order would
    # land the wrong value on every simulant.
    update = pd.DataFrame(
        {"pi": [-1.0, -2.0, -3.0, -4.0], "cube": [-1, -2, -3, -4]}, index=index
    )

    pies_and_cubes_pop_mgr.update(update)

    updated = pies_and_cubes_pop_mgr.private_columns
    pd.testing.assert_frame_equal(updated.loc[index, ["pi", "cube"]], update)
    pd.testing.assert_frame_equal(
        updated.loc[omitted, ["pi", "cube"]], original.loc[omitted, ["pi", "cube"]]
    )


class ComprehensionColumnCreator(Component):
    """Build column values by comprehension rather than broadcasting a scalar."""

    @property
    def columns_created(self) -> list[str]:
        return ["by_comprehension"]

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_column, columns=self.columns_created
        )

    def initialize_column(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.Series(
                [int(i) for i in pop_data.index],
                index=pop_data.index,
                name="by_comprehension",
            )
        )


class SimulantAdder(Component):
    """Add simulants on the first time step, keeping what the creator returned."""

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.created_index: pd.Index[int] | None = None

    def setup(self, builder: Builder) -> None:
        self.simulant_creator = builder.population.get_simulant_creator()

    def on_time_step(self, event: Event) -> None:
        self.created_index = self.simulant_creator(self.count, {})


class TypedColumnCreator(Component):
    """Create one private column per dtype, so dtype handling can be checked."""

    COLUMNS: dict[str, Any] = {
        "a_bool": True,
        "an_int": 7,
        "a_float": 1.5,
        "a_string": "spam",
        "a_datetime": pd.Timestamp("2020-01-01"),
    }

    @property
    def columns_created(self) -> list[str]:
        return list(self.COLUMNS)

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_columns, columns=self.columns_created
        )

    def initialize_columns(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.DataFrame(self.COLUMNS, index=pop_data.index)[self.columns_created]
        )


def _grow(*components: Component) -> InteractiveContext:
    """Build a simulation that adds ``ADDED`` simulants on its first step."""
    return InteractiveContext(
        components=[*components, SimulantAdder(ADDED)],
        configuration={"population": {"population_size": INITIAL_SIZE}},
        setup=True,
    )


@pytest.fixture(scope="module")
def grown_population() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the private columns before and after a mid-simulation addition."""
    sim = _grow(ColumnCreator(), TypedColumnCreator())
    before = sim._population.private_columns.copy()
    sim.step()
    return before, sim._population.private_columns.copy()


def test_mid_sim_addition_leaves_committed_simulants_untouched(
    grown_population: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    """Growing the population does not disturb the simulants already in it.

    Deliberately a whole-frame comparison rather than a per-column one, so it also
    catches a dropped column, a reordered index, or null padding leaking in.
    """
    before, after = grown_population

    assert after.index.equals(pd.RangeIndex(0, INITIAL_SIZE + ADDED))
    assert not after.isna().to_numpy().any()
    pd.testing.assert_frame_equal(after.loc[before.index], before)


@pytest.mark.parametrize("column", list(TypedColumnCreator.COLUMNS))
def test_mid_sim_addition_preserves_dtype_and_values(
    column: str, grown_population: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    """Each column keeps its dtype across a mid-simulation addition.

    Compared against the committed dtype rather than a hard-coded one, so the test
    states the actual guarantee and does not need updating when pandas changes how
    it infers a dtype.
    """
    committed, grown = (frame[column] for frame in grown_population)

    assert len(grown) == INITIAL_SIZE + ADDED
    assert grown.dtype == committed.dtype
    assert (grown == TypedColumnCreator.COLUMNS[column]).all()


class DtypeShifter(Component):
    """Initialize a column with one value on the first pass and another after."""

    def __init__(self, first: Any, later: Any) -> None:
        super().__init__()
        self.first = first
        self.later = later
        self._passes = 0

    @property
    def columns_created(self) -> list[str]:
        return ["shifting"]

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_column, columns=self.columns_created
        )

    def initialize_column(self, pop_data: SimulantData) -> None:
        self._passes += 1
        value = self.first if self._passes == 1 else self.later
        self.population_view.initialize(
            pd.Series(value, index=pop_data.index, name="shifting")
        )


@pytest.mark.parametrize(
    "first, later",
    [
        pytest.param(1, 3.7, id="int_column_given_a_fractional_float"),
        pytest.param(1, 3.0, id="int_column_given_a_whole_float"),
        pytest.param(1, "spam", id="int_column_given_a_string"),
        pytest.param(True, 1, id="bool_column_given_an_int"),
        pytest.param(pd.Timestamp("2020-01-01"), "spam", id="datetime_column_given_a_string"),
    ],
)
def test_a_pass_may_not_retype_a_committed_column(first: Any, later: Any) -> None:
    """An initializer cannot change the dtype of a column the population already has.

    The whole-float case raises even though the values would coerce losslessly.
    Accepting it would make the check depend on the values a pass happened to
    produce, so the same initializer would pass on one seed and raise on another.
    """
    sim = _grow(DtypeShifter(first, later))

    with pytest.raises(PopulationError, match="would change the dtype"):
        sim.step()


def test_committed_float_column_survives_an_int_initializer() -> None:
    """An int-typed initializer no longer truncates a committed float column.

    The padded population let the whole column take the update's dtype, so existing
    simulants' values were rewritten as ints. The staged frame carries the int only
    for the new simulants, and the append promotes it back to float. This is the one
    direction the dtype check permits, so it also pins the check against overreach.
    """
    sim = _grow(DtypeShifter(1.5, 3))
    committed = sim._population.private_columns["shifting"]
    assert committed.dtype == np.dtype("float64")
    assert (committed == 1.5).all()

    sim.step()

    grown = sim._population.private_columns["shifting"]
    assert grown.dtype == np.dtype("float64")
    pd.testing.assert_series_equal(grown.loc[committed.index], committed)
    assert (grown.loc[NEW_INDEX] == 3.0).all()


def test_a_pass_must_initialize_every_registered_column() -> None:
    """An initializer cannot skip a column it registered, on any pass.

    Skipping it appends nulls for the new simulants. That retypes an int column but
    leaves a float one intact, so the dtype check alone would not catch it.
    """

    class SkipsLaterPasses(Component):
        """Create the column on the first pass, then never initialize it again."""

        def __init__(self) -> None:
            super().__init__()
            self._passes = 0

        @property
        def columns_created(self) -> list[str]:
            return ["skipped"]

        def setup(self, builder: Builder) -> None:
            builder.population.register_initializer(
                initializer=self.initialize_column, columns=self.columns_created
            )

        def initialize_column(self, pop_data: SimulantData) -> None:
            self._passes += 1
            if self._passes == 1:
                self.population_view.initialize(
                    pd.Series(1.5, index=pop_data.index, name="skipped")
                )

    sim = _grow(SkipsLaterPasses())

    with pytest.raises(PopulationError, match="not actually created"):
        sim.step()


class EarlyReader(Component):
    """Read a column that a later initializer creates, on chosen passes."""

    def __init__(self, read_on_passes: tuple[int, ...]) -> None:
        super().__init__()
        self.read_on_passes = read_on_passes
        self._passes = 0

    @property
    def columns_created(self) -> list[str]:
        return ["read_first"]

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_and_read, columns=self.columns_created
        )

    def initialize_and_read(self, pop_data: SimulantData) -> None:
        self._passes += 1
        if self._passes in self.read_on_passes:
            self.population_view.get(pop_data.index, "written_later")
        self.population_view.initialize(pd.Series(1, index=pop_data.index, name="read_first"))


class LateWriter(Component):
    """Create the column, ordered after EarlyReader by a resource requirement."""

    @property
    def columns_created(self) -> list[str]:
        return ["written_later"]

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_column,
            columns=self.columns_created,
            required_resources=["read_first"],
        )

    def initialize_column(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(
            pd.Series(2, index=pop_data.index, name="written_later")
        )


def test_column_awaiting_its_initializer_raises_mid_sim() -> None:
    """Reading a column whose initializer has not run yet this pass is an error.

    The column exists in the population, so this is distinct from never having been
    created: the staged frame simply carries no value for the new simulants until the
    initializer runs. Serving null would hide the missing resource requirement.
    """
    sim = _grow(EarlyReader(read_on_passes=(2,)), LateWriter())

    with pytest.raises(PopulationError, match="have no value for the simulants"):
        sim.step()


def test_never_created_column_raises_during_initial_creation() -> None:
    """A column that no initializer has created yet cannot be read."""
    with pytest.raises(PopulationError, match="have not been created"):
        _grow(EarlyReader(read_on_passes=(1,)), LateWriter())


def test_read_spanning_both_frames_raises() -> None:
    """A read cannot cover the simulants being added and the settled ones at once."""

    class SpanningReader(Component):
        def __init__(self) -> None:
            super().__init__()
            self._passes = 0

        @property
        def columns_created(self) -> list[str]:
            return ["spanning"]

        def setup(self, builder: Builder) -> None:
            builder.population.register_initializer(
                initializer=self.initialize_spanning,
                columns=self.columns_created,
                required_resources=["test_column_1"],
            )

        def initialize_spanning(self, pop_data: SimulantData) -> None:
            self._passes += 1
            index = pop_data.index
            if self._passes > 1:
                # Simulant 0 is committed by now; the rest are being added.
                index = pd.Index([0, *pop_data.index])
            self.population_view.get(index, "test_column_1")
            self.population_view.initialize(
                pd.Series(1, index=pop_data.index, name="spanning")
            )

    sim = _grow(ColumnCreator(), SpanningReader())

    with pytest.raises(PopulationError, match="cannot cover both the simulants being added"):
        sim.step()


def test_creator_returns_the_whole_population_index() -> None:
    """The creator hands back the whole population, not just the simulants it added."""
    adder = SimulantAdder(ADDED)
    sim = InteractiveContext(
        components=[ColumnCreator(), adder],
        configuration={"population": {"population_size": INITIAL_SIZE}},
        setup=True,
    )

    sim.step()

    assert adder.created_index is not None
    assert adder.created_index.equals(pd.RangeIndex(0, INITIAL_SIZE + ADDED))
    # The pass staged simulants and then handed them over, so it is over.
    assert not sim._population.adding_simulants


def test_zero_count_addition_changes_nothing() -> None:
    """Adding no simulants leaves the population exactly as it was.

    The creator still returns the whole population, so a caller cannot tell a
    no-op addition apart from one that added nothing new.
    """
    adder = SimulantAdder(0)
    sim = InteractiveContext(
        components=[ColumnCreator(), TypedColumnCreator(), adder],
        configuration={"population": {"population_size": INITIAL_SIZE}},
        setup=True,
    )
    before = sim._population.private_columns.copy()

    sim.step()

    pd.testing.assert_frame_equal(sim._population.private_columns, before)
    assert adder.created_index is not None
    assert adder.created_index.equals(before.index)


def test_staged_accessors_raise_outside_a_creation_pass() -> None:
    """The staged frame and its index are only reachable during a creation pass."""
    manager = _grow(ColumnCreator())._population
    assert not manager.adding_simulants

    with pytest.raises(PopulationError, match="No simulants are being added."):
        _ = manager.staged_simulants
    with pytest.raises(PopulationError, match="No simulants are being added."):
        _ = manager.staged_index


def test_partial_mid_sim_initialization_raises() -> None:
    """An initializer must cover every simulant being added, on every pass.

    Initial creation always required full coverage. Requiring it of a
    mid-simulation addition too is the behavior change: previously the simulants an
    initializer skipped were left null.
    """

    class PartialOnLaterPasses(Component):
        """Cover every simulant on the first pass, only half on later ones."""

        def __init__(self) -> None:
            super().__init__()
            self._passes = 0

        @property
        def columns_created(self) -> list[str]:
            return ["partial"]

        def setup(self, builder: Builder) -> None:
            builder.population.register_initializer(
                initializer=self.initialize_partial, columns=self.columns_created
            )

        def initialize_partial(self, pop_data: SimulantData) -> None:
            self._passes += 1
            covered = pop_data.index if self._passes == 1 else pop_data.index[::2]
            self.population_view.initialize(pd.Series(1, index=covered, name="partial"))

    component = PartialOnLaterPasses()
    sim = _grow(component)
    assert component._passes == 1

    with pytest.raises(PopulationError, match="is missing updates for"):
        sim.step()


def test_adding_to_a_rowless_population_keeps_the_new_dtypes() -> None:
    """A rowless population contributes no dtype to the simulants added after it.

    An initializer building its values by comprehension gets an empty list for an
    empty index, which pandas types without reference to what the values would have
    been. Concatenating that rowless column with the staged frame would resolve to
    the wrong dtype and lose the real one.
    """
    sim = InteractiveContext(
        components=[ComprehensionColumnCreator(), SimulantAdder(5)],
        configuration={"population": {"population_size": 0}},
        setup=True,
    )
    assert len(sim._population.private_columns) == 0

    sim.step()

    grown = sim._population.private_columns
    assert len(grown) == 5
    assert grown["by_comprehension"].dtype == np.dtype("int64")
    assert grown["by_comprehension"].tolist() == [0, 1, 2, 3, 4]
