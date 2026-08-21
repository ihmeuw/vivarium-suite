from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Callable
from typing import Any, NamedTuple

import pandas as pd
import pytest
from pytest_mock import MockerFixture

from vivarium.engine import Component
from vivarium.engine.framework.engine import Builder, SimulationContext
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.population import PopulationManager, SimulantData
from vivarium.engine.framework.values import ValuesManager

# FIXME: Streamline with already-existing classes in tests/helpers.py
PIE_COL_NAMES = ["pie", "pi"]
PIES = ["apple", "chocolate", "pecan", "pumpkin", "sweet_potato"]
PIS = [math.pi**i for i in range(1, 11)]
PIE_RECORDS = [(pie, pi) for pie, pi in itertools.product(PIES, PIS)]
PIE_DF = pd.DataFrame(data=PIE_RECORDS, columns=PIE_COL_NAMES)
CUBE_COL_NAMES = ["cube", "cube_string"]
CUBE = [i**3 for i in range(len(PIE_RECORDS))]
CUBE_STRING = [str(i**3) for i in range(len(PIE_RECORDS))]
CUBE_DF = pd.DataFrame(
    zip(CUBE, CUBE_STRING),
    columns=CUBE_COL_NAMES,
    index=PIE_DF.index,
)


class PieComponent(Component):
    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.make_pie, columns=PIE_COL_NAMES
        )

    def make_pie(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(self.get_initial_state(pop_data.index))

    def get_initial_state(self, index: pd.Index[int]) -> pd.DataFrame:
        return PIE_DF


class CubeComponent(Component):
    def setup(self, builder: Builder) -> None:
        builder.population.register_initializer(
            initializer=self.cubify, columns=CUBE_COL_NAMES
        )

    def cubify(self, pop_data: SimulantData) -> None:
        self.population_view.initialize(self.get_initial_state(pop_data.index))

    def get_initial_state(self, index: pd.Index[int]) -> pd.DataFrame:
        return CUBE_DF


class SimulantAdder(Component):
    """Adds simulants on a given time step, for exercising mid-simulation growth."""

    def __init__(self, count: int, on_step: int = 1) -> None:
        super().__init__()
        self.count = count
        self.on_step = on_step
        self._steps_taken = 0

    def setup(self, builder: Builder) -> None:
        self.simulant_creator = builder.population.get_simulant_creator()

    def on_time_step(self, event: Event) -> None:
        self._steps_taken += 1
        if self._steps_taken == self.on_step:
            self.simulant_creator(self.count, {})


class TypedColumnCreator(Component):
    """Creates one private column per dtype, so dtype handling can be checked.

    The initial values are supplied by ``initial_values``, keyed by column name, and
    every new simulant gets the first value for its column. Passing ``dtypes``
    overrides the dtype each column is cast to, which is how a mid-simulation
    addition can be made to arrive in a different dtype than the column holds.
    """

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


class StagingObservation(NamedTuple):
    """What a ``StagingRecorder`` saw from inside a single creation pass."""

    committed_index: pd.Index[int]
    population_index: pd.Index[int]
    read: pd.DataFrame | None


class StagingRecorder(Component):
    """Create one private column and record the manager's state from inside a pass.

    The staged frame is gone by the time a creation pass returns, so anything about it
    has to be captured while an initializer is still running. Pass ``requires`` to order
    this initializer after another one, ``reads`` to record what the manager serves for
    those columns, and ``on_initialized`` to drive the manager directly once this
    component's column has been staged.
    """

    @property
    def name(self) -> str:
        return self._name

    def __init__(
        self,
        name: str = "staging_recorder",
        column: str = "recorded",
        requires: list[str] | None = None,
        reads: list[str] | None = None,
        on_initialized: (Callable[[PopulationManager, SimulantData], None] | None) = None,
    ) -> None:
        super().__init__()
        self._name = name
        self.column = column
        self.requires = requires if requires is not None else []
        self.reads = reads if reads is not None else []
        self.on_initialized = on_initialized
        self.observations: list[StagingObservation] = []

    def setup(self, builder: Builder) -> None:
        self.population_manager: PopulationManager = builder.population._manager
        builder.population.register_initializer(
            initializer=self.record_and_initialize,
            columns=[self.column],
            required_resources=self.requires,
        )

    def record_and_initialize(self, pop_data: SimulantData) -> None:
        manager = self.population_manager
        self.observations.append(
            StagingObservation(
                committed_index=pd.DataFrame(manager._private_columns).index.copy(),
                population_index=manager.get_population_index().copy(),
                read=(
                    manager._read_frame(index=pop_data.index, columns=self.reads).copy()
                    if self.reads
                    else None
                ),
            )
        )
        self.population_view.initialize(
            pd.Series(
                [i * 10 for i in pop_data.index], index=pop_data.index, name=self.column
            )
        )
        if self.on_initialized is not None:
            self.on_initialized(manager, pop_data)


@pytest.fixture(scope="function")
def pies_and_cubes_pop_mgr(mocker: MockerFixture) -> PopulationManager:
    """A mocked PopulationManager with some private columns set up.

    This fixture is tied directly to the PieComponent and CubeComponent helper classes.

    """

    class _PopulationManager(PopulationManager):
        def __init__(self) -> None:
            super().__init__()
            self._private_columns: pd.DataFrame = pd.concat([PIE_DF, CUBE_DF], axis=1)

        def _add_constraint(self, *args: Any, **kwargs: Any) -> None:
            pass

    mgr = _PopulationManager()

    # Use SimulationContext just for builder and mock as appropriate
    sim = SimulationContext()
    builder = sim._builder
    mocker.patch.object(ValuesManager, "logger", mocker.Mock(), create=True)
    mocker.patch.object(ValuesManager, "resources", mocker.Mock(), create=True)
    mocker.patch.object(ValuesManager, "add_constraint", mocker.Mock(), create=True)
    mocked_attribute_pipelines = {}
    sim._lifecycle.set_state("setup")
    mgr.setup(builder)
    sim._lifecycle.set_state("post_setup")
    sim._lifecycle.set_state("population_creation")

    for col in mgr._private_columns.columns:
        mocked_attribute_pipelines[col] = mocker.Mock()
    mgr._attribute_pipelines = mocked_attribute_pipelines
    mgr._private_column_metadata = defaultdict(
        list,
        {
            "pie_component": PIE_COL_NAMES,
            "cube_component": CUBE_COL_NAMES,
        },
    )
    # Change lifecycle phase to ensure tracked queries are applied appropriately
    mocker.patch.object(mgr, "get_current_state", lambda: "on_time_step")
    return mgr
