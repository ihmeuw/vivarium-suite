from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
import pytest
from pytest_mock import MockerFixture

from tests.framework.population.conftest import (
    CUBE_COL_NAMES,
    PIE_COL_NAMES,
    PIE_RECORDS,
    CrossFrameReader,
    SimulantAdder,
    StagingRecorder,
    TypedColumnCreator,
)
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
from vivarium.engine.framework.population.exceptions import PopulationError
from vivarium.engine.framework.population.manager import PopulationManager, SimulantData


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


def test_get_private_columns_raises_on_initial_pop_creation() -> None:
    mgr = PopulationManager()
    mgr.creating_initial_population = True
    with pytest.raises(
        PopulationError,
        match="Cannot get private columns during initial population creation",
    ):
        mgr.get_private_columns(ColumnCreator(), columns=["test_column_1"])


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


##############################
# Staging new simulants      #
##############################

INITIAL_POP_SIZE = 6
ADDED_SIMULANTS = 4
NEW_INDEX = pd.RangeIndex(INITIAL_POP_SIZE, INITIAL_POP_SIZE + ADDED_SIMULANTS)
SENTINEL = -999

TYPED_COLUMN_CASES = [
    pytest.param("a_bool", True, np.dtype(bool), id="bool"),
    pytest.param("an_int", 7, np.dtype("int64"), id="int64"),
    pytest.param("a_float", 1.5, np.dtype("float64"), id="float64"),
    pytest.param("a_string", "spam", np.dtype(object), id="object"),
    # pandas reads second resolution out of a date-only Timestamp, so the unit is
    # pinned here to keep the dtype under test unambiguous.
    pytest.param(
        "a_datetime",
        pd.Timestamp("2020-01-01").as_unit("ns"),
        np.dtype("datetime64[ns]"),
        id="datetime64",
    ),
]


class OneTypedColumnCreator(TypedColumnCreator):
    """Create a single typed column, so one dtype at a time can be exercised."""

    def __init__(self, column: str, value: Any) -> None:
        super().__init__()
        self.COLUMNS = {column: value}


class FloatThenIntCreator(OneTypedColumnCreator):
    """Initialize a float column, then hand it int values on every later pass."""

    def __init__(self) -> None:
        super().__init__("a_float", 1.5)
        self._passes = 0

    def initialize_columns(self, pop_data: SimulantData) -> None:
        self._passes += 1
        value = 1.5 if self._passes == 1 else 3
        self.population_view.initialize(
            pd.Series(value, index=pop_data.index, name="a_float")
        )


class FailingInitializer(Component):
    """Stage a column and then raise, on one chosen creation pass.

    Which pass fails is selectable because a mid-simulation failure has to let the
    initial population be built first, while a first-pass failure must not.
    """

    ERROR_MESSAGE = "initializer failed on purpose"

    def __init__(self, fail_on_pass: int = 2) -> None:
        super().__init__()
        self.fail_on_pass = fail_on_pass
        self._passes = 0

    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.initialize_doomed, columns=["doomed"]
        )

    def initialize_doomed(self, pop_data: SimulantData) -> None:
        self._passes += 1
        # Stage values before raising so the failed pass has a partial frame to discard.
        self.population_view.initialize(
            pd.Series(list(pop_data.index), index=pop_data.index, name="doomed")
        )
        if self._passes == self.fail_on_pass:
            raise ValueError(self.ERROR_MESSAGE)


def _grow(*components: Component) -> InteractiveContext:
    """Build a simulation that adds ``ADDED_SIMULANTS`` simulants on its first step."""
    return InteractiveContext(
        components=[*components, SimulantAdder(count=ADDED_SIMULANTS)],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=True,
    )


def _population(sim: InteractiveContext) -> pd.DataFrame:
    """Read a simulation's whole population state table."""
    pop = sim._population.get_population("all")
    assert isinstance(pop, pd.DataFrame)
    return pop


def _column(sim: InteractiveContext, column: str) -> pd.Series[Any]:
    """Read one column of a simulation's population state table."""
    values = sim._population.get_population([column], squeeze=True)
    assert isinstance(values, pd.Series)
    return values


def _staged_values(index: pd.Index[int], column: str = "recorded") -> pd.Series[Any]:
    """Build the values a ``StagingRecorder`` writes for ``index``."""
    return pd.Series([i * 10 for i in index], index=index, name=column)


def test_create_simulants_appends_staged_simulants_once() -> None:
    """A creation pass adds every new row to the population in a single append."""
    first = StagingRecorder(name="first_recorder", column="recorded_first")
    second = StagingRecorder(
        name="second_recorder", column="recorded_second", requires=["recorded_first"]
    )
    sim = _grow(first, second)
    committed = sim._population.get_population_index()
    assert sim._population._staged_columns is None
    for recorder in (first, second):
        recorder.observations.clear()

    sim.step()

    # A partly grown population at either initializer would mean the new rows were
    # appended more than once.
    for recorder in (first, second):
        (observation,) = recorder.observations
        assert observation.committed_index.equals(committed)

    assert sim._population._staged_columns is None
    grown = pd.DataFrame(sim._population.private_columns)
    assert grown.index.equals(pd.RangeIndex(0, INITIAL_POP_SIZE + ADDED_SIMULANTS))
    assert not grown.index.has_duplicates
    assert not grown.isna().to_numpy().any()


def test_mid_sim_addition_leaves_committed_rows_untouched() -> None:
    """Adding simulants mid-simulation does not alter existing simulants' values."""
    sim = _grow(ColumnCreator(), TypedColumnCreator())
    before = _population(sim)

    sim.step()

    after = _population(sim)
    assert len(after) == len(before) + ADDED_SIMULANTS
    pd.testing.assert_frame_equal(after.loc[before.index], before)


@pytest.mark.parametrize("column, value, expected_dtype", TYPED_COLUMN_CASES)
def test_mid_sim_addition_preserves_committed_dtypes(
    column: str, value: Any, expected_dtype: np.dtype[Any]
) -> None:
    """A column's dtype survives a mid-simulation addition for every dtype we support."""
    sim = _grow(OneTypedColumnCreator(column, value))
    committed = _column(sim, column)
    assert committed.dtype == expected_dtype

    sim.step()

    grown = _column(sim, column)
    assert len(grown) == INITIAL_POP_SIZE + ADDED_SIMULANTS
    assert grown.dtype == expected_dtype
    assert (grown == value).all()


def test_committed_float_column_survives_int_initializer() -> None:
    """An int-typed initializer no longer truncates a committed float column."""
    sim = _grow(FloatThenIntCreator())
    committed = _column(sim, "a_float")
    assert committed.dtype == np.dtype("float64")
    assert (committed == 1.5).all()

    sim.step()

    grown = _column(sim, "a_float")
    assert grown.dtype == np.dtype("float64")
    pd.testing.assert_series_equal(grown.loc[committed.index], committed)
    assert (grown.loc[NEW_INDEX] == 3.0).all()


def test_get_population_index_includes_staged_simulants() -> None:
    """During initialization the population index covers committed and new simulants."""
    recorder = StagingRecorder()
    sim = _grow(recorder)
    committed = sim._population.get_population_index()
    recorder.observations.clear()

    sim.step()

    (observation,) = recorder.observations
    assert observation.committed_index.equals(committed)
    assert observation.population_index.equals(
        pd.RangeIndex(0, INITIAL_POP_SIZE + ADDED_SIMULANTS)
    )


def test_initializer_reads_column_created_earlier_in_the_same_pass() -> None:
    """An initializer sees the values a preceding initializer staged."""
    first = StagingRecorder(name="first_recorder", column="recorded_first")
    second = StagingRecorder(
        name="second_recorder",
        column="recorded_second",
        requires=["recorded_first"],
        reads=["recorded_first"],
    )
    sim = _grow(first, second)
    second.observations.clear()

    sim.step()

    (observation,) = second.observations
    assert observation.read is not None
    pd.testing.assert_series_equal(
        observation.read["recorded_first"],
        _staged_values(NEW_INDEX, "recorded_first"),
        check_index_type=False,
    )


def test_read_of_uninitialized_private_column_yields_null() -> None:
    """A column whose initializer has not run yet reads as null, not an error."""
    first = StagingRecorder(
        name="first_recorder", column="recorded_first", reads=["recorded_second"]
    )
    second = StagingRecorder(
        name="second_recorder", column="recorded_second", requires=["recorded_first"]
    )
    sim = _grow(first, second)

    sim.step()

    # Both the initial creation pass and the mid-simulation one.
    assert len(first.observations) == 2
    for observation in first.observations:
        read = observation.read
        assert read is not None and not read.empty
        assert read["recorded_second"].isna().all()


def test_update_writes_to_the_staged_frame_during_a_creation_pass() -> None:
    """A write while simulants are being added lands on the staged frame alone."""

    def write_sentinel(manager: PopulationManager, pop_data: SimulantData) -> None:
        manager.update(pd.DataFrame({"recorded": SENTINEL}, index=pop_data.index))

    sim = _grow(StagingRecorder(on_initialized=write_sentinel))
    committed = _column(sim, "recorded")

    sim.step()

    recorded = _column(sim, "recorded")
    assert (recorded.loc[NEW_INDEX] == SENTINEL).all()
    pd.testing.assert_series_equal(recorded.loc[committed.index], committed)


def test_initialize_rejects_an_already_committed_simulant() -> None:
    """initialize() writes new simulants only; naming an existing one is an error."""

    class WideningInitializer(Component):
        def setup(self, builder: Builder) -> None:
            self.population_manager: PopulationManager = builder.population._manager
            builder.population.register_initializer(
                initializer=self.initialize_widened, columns=["widened"]
            )

        def initialize_widened(self, pop_data: SimulantData) -> None:
            index = pop_data.index
            if not self.population_manager.creating_initial_population:
                index = pd.Index([0, *pop_data.index])
            self.population_view.initialize(pd.Series(1, index=index, name="widened"))

    sim = _grow(WideningInitializer())

    with pytest.raises(PopulationError, match="no matching index in the existing table"):
        sim.step()


def test_update_is_refused_while_simulants_are_being_added() -> None:
    """update() is for existing state; new simulants go through initialize()."""

    class UpdatingInitializer(Component):
        def setup(self, builder: Builder) -> None:
            builder.population.register_initializer(
                initializer=self.initialize_updated, columns=["updated"]
            )

        def initialize_updated(self, pop_data: SimulantData) -> None:
            self.population_view.initialize(
                pd.Series(1, index=pop_data.index, name="updated")
            )
            self.population_view.update("updated", lambda s: s + 1)

    with pytest.raises(
        PopulationError, match="cannot be called while simulants are being added"
    ):
        InteractiveContext(
            components=[UpdatingInitializer()],
            configuration={"population": {"population_size": INITIAL_POP_SIZE}},
            setup=True,
        )


def test_failed_mid_sim_addition_discards_the_staged_frame() -> None:
    """A raising initializer leaves the population exactly as the pass found it."""
    sim = _grow(ColumnCreator(), FailingInitializer())
    before = _population(sim)

    with pytest.raises(ValueError, match=FailingInitializer.ERROR_MESSAGE):
        sim.step()

    manager = sim._population
    assert manager._staged_columns is None
    assert not manager.adding_simulants
    assert not manager.creating_initial_population
    pd.testing.assert_frame_equal(_population(sim), before)


def test_failed_initial_population_creation_leaves_manager_uninitialized() -> None:
    """A first-pass failure commits nothing, so the population is still absent."""
    sim = InteractiveContext(
        components=[ColumnCreator(), FailingInitializer(fail_on_pass=1)],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=False,
    )

    with pytest.raises(ValueError, match=FailingInitializer.ERROR_MESSAGE):
        sim.setup()

    manager = sim._population
    assert manager._staged_columns is None
    assert not manager.adding_simulants
    assert not manager.creating_initial_population
    with pytest.raises(PopulationError, match="Population has not been initialized."):
        manager.private_columns


def test_mid_sim_addition_matches_state_of_an_equally_sized_initial_population() -> None:
    """End-to-end: growing to N simulants yields the same state as starting with N."""
    grown = InteractiveContext(
        components=[
            ColumnCreator(),
            TypedColumnCreator(),
            SimulantAdder(count=INITIAL_POP_SIZE),
        ],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=True,
    )
    grown.step()

    started_large = InteractiveContext(
        components=[ColumnCreator(), TypedColumnCreator()],
        configuration={"population": {"population_size": 2 * INITIAL_POP_SIZE}},
        setup=True,
    )

    pd.testing.assert_frame_equal(_population(grown), _population(started_large))


def test_initializer_reads_across_frames_through_the_population_view() -> None:
    """A view read spanning committed and staged simulants serves both correctly."""
    staged_first = StagingRecorder(name="first_recorder", column="recorded_first")
    reader = CrossFrameReader(attribute="recorded_first", requires=["recorded_first"])
    sim = _grow(staged_first, reader)
    committed = _column(sim, "recorded_first")
    reader.reads.clear()

    sim.step()

    (read,) = reader.reads
    assert read.index.equals(pd.RangeIndex(0, INITIAL_POP_SIZE + ADDED_SIMULANTS))
    pd.testing.assert_series_equal(
        read.loc[committed.index], committed, check_index_type=False
    )
    pd.testing.assert_series_equal(
        read.loc[NEW_INDEX], _staged_values(NEW_INDEX, "recorded_first")
    )


class TrackedQueryRegistrar(Component):
    """Register a tracked query during setup, as a tracking component would."""

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query = query

    def setup(self, builder: Builder) -> None:
        builder.population.register_tracked_query(self.query)


# Excludes one committed simulant and one staged one, so a query applied to only
# half the population would show up as an unfiltered row on the other side.
TRACKED_QUERY = "recorded != 20 and recorded != 70"


def test_tracked_query_is_suppressed_for_reads_during_initial_creation() -> None:
    """The initial creation pass reads every new simulant, tracked query or not."""
    reader = CrossFrameReader(attribute="recorded", requires=["recorded"])
    sim = _grow(TrackedQueryRegistrar(TRACKED_QUERY), StagingRecorder(), reader)
    assert sim._population.tracked_queries == [TRACKED_QUERY]

    (read,) = reader.reads
    pd.testing.assert_series_equal(
        read, _staged_values(pd.RangeIndex(0, INITIAL_POP_SIZE)), check_index_type=False
    )


def test_tracked_query_filters_a_creation_pass_read_across_both_frames() -> None:
    """A read that asks for the tracked query has it applied to both frames."""
    reader = CrossFrameReader(
        attribute="recorded", requires=["recorded"], include_untracked=False
    )
    sim = _grow(TrackedQueryRegistrar(TRACKED_QUERY), StagingRecorder(), reader)

    sim.step()

    pd.testing.assert_series_equal(
        reader.reads[-1],
        _staged_values(pd.Index([0, 1, 3, 4, 5, 6, 8, 9])),
        check_index_type=False,
    )


def test_mid_sim_addition_of_zero_simulants_changes_nothing() -> None:
    """Creating zero simulants leaves the population exactly as it was."""
    sim = InteractiveContext(
        components=[ColumnCreator(), TypedColumnCreator(), SimulantAdder(count=0)],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=True,
    )
    before = _population(sim)

    sim.step()

    after = _population(sim)
    pd.testing.assert_frame_equal(after, before)
    assert after.index.equals(pd.RangeIndex(0, INITIAL_POP_SIZE))
    assert not after.isna().to_numpy().any()
    assert sim._population._staged_columns is None


def test_initial_population_of_zero_simulants_is_coherent() -> None:
    """A zero-size initial population is an empty state table, not a broken one."""
    typed = TypedColumnCreator()
    sim = InteractiveContext(
        components=[ColumnCreator(), typed],
        configuration={"population": {"population_size": 0}},
        setup=True,
    )
    populated = InteractiveContext(
        components=[ColumnCreator(), TypedColumnCreator()],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=True,
    )

    empty = _population(sim)
    full = _population(populated)
    assert empty.index.equals(pd.RangeIndex(0, 0))
    assert set(empty.columns) == set(full.columns)
    # Only the columns whose initializer returns a scalar keep their dtype here. One
    # building its values by comprehension yields an empty list for an empty index,
    # which pandas types as float64 whatever the values would have been.
    typed_columns = typed.columns_created
    pd.testing.assert_series_equal(empty[typed_columns].dtypes, full[typed_columns].dtypes)
    assert sim._population._staged_columns is None


def test_repeated_mid_sim_additions_keep_the_population_coherent() -> None:
    """Successive additions each extend the population without damaging it."""
    counts = [2, 3, 4]
    sim = InteractiveContext(
        components=[
            ColumnCreator(),
            TypedColumnCreator(),
            *[
                SimulantAdder(count=count, on_step=step)
                for step, count in enumerate(counts, start=1)
            ],
        ],
        configuration={"population": {"population_size": INITIAL_POP_SIZE}},
        setup=True,
    )
    dtypes = _population(sim).dtypes
    expected_size = INITIAL_POP_SIZE

    for count in counts:
        sim.step()

        expected_size += count
        pop = _population(sim)
        assert pop.index.equals(pd.RangeIndex(0, expected_size))
        assert not pop.isna().to_numpy().any()
        assert pop.dtypes.equals(dtypes)
        assert sim._population._staged_columns is None
