from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture
from pytest_mock import MockerFixture

from tests.framework.population.helpers import (
    assert_squeezing_multi_level_single_outer_multi_inner,
    assert_squeezing_multi_level_single_outer_single_inner,
    assert_squeezing_single_level_single_col,
)
from tests.framework.results.helpers import (
    HARRY_POTTER_CONFIG,
    Hogwarts,
    HogwartsResultsStratifier,
    HousePointsObserver,
)
from tests.helpers import (
    AttributePipelineCreator,
    ColumnCreator,
    ColumnCreatorAndRequirer,
    DiamondAttributeCreator,
    ManyModifierCreator,
    MultiLevelMultiColumnCreator,
    MultiLevelSingleColumnCreator,
    NestedAttributeCreator,
    NestedLookupCaller,
    OrderedDependencyCreator,
    ReversedNameCreator,
    SingleColumnCreator,
)
from tests.interface.conftest import FakeWidget
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.exceptions import ObservationError
from vivarium.engine.framework.engine import Builder, SimulationContext
from vivarium.engine.framework.lookup.table import LookupTable
from vivarium.engine.framework.resource.resource import Column, Resource
from vivarium.engine.framework.results import Observer
from vivarium.engine.framework.results.observation import VALUE_COLUMN
from vivarium.engine.framework.values import AttributePipeline, Pipeline
from vivarium.engine.interface.interactive import _match_rank, _render_dependency_tree
from vivarium.engine.types import ClockTime


def test_list_values() -> None:
    sim = InteractiveContext()
    # a 'simulant_step_size' value is created by default upon setup
    assert sim.list_values() == ["simulant_step_size"]
    assert isinstance(sim.get_value("simulant_step_size"), Pipeline)
    with pytest.raises(ValueError, match="No value pipeline 'foo' registered."):
        sim.get_value("foo")
    # ensure that 'foo' did not get added to the list of values
    assert sim.list_values() == ["simulant_step_size"]


class TestGetAttribute:
    """Tests for retrieving an attribute pipeline from an interactive simulation."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(components=[ColumnCreator()])

    def test_a_created_column_is_an_attribute(self, sim: InteractiveContext) -> None:
        """A column a component creates is reachable as an attribute."""
        assert "test_column_1" in sim.get_attribute_names()

    def test_the_pipeline_itself_is_returned(self, sim: InteractiveContext) -> None:
        """The getter hands back the pipeline, not the value it produces."""
        assert isinstance(sim.get_attribute("test_column_1"), AttributePipeline)

    def test_an_unregistered_name_raises(self, sim: InteractiveContext) -> None:
        """An unknown name is rejected rather than quietly accepted."""
        with pytest.raises(ValueError, match="No attribute pipeline 'foo' registered."):
            sim.get_attribute("foo")

    def test_a_rejected_lookup_registers_nothing(self, sim: InteractiveContext) -> None:
        """The guard is what keeps a typo from polluting the simulation.

        ``ValuesManager.get_attribute`` creates a pipeline for an unknown name, so
        without the guard in ``InteractiveContext.get_attribute`` a misspelling would
        leave a new pipeline behind.
        """
        registered = sim.get_attribute_names()

        with pytest.raises(ValueError):
            sim.get_attribute("foo")

        assert sim.get_attribute_names() == registered


def test_get_value_and_get_attribute_point_at_each_other() -> None:
    """A name looked up on the wrong getter says which one to use instead."""
    sim = InteractiveContext(components=[ColumnCreator()])

    with pytest.raises(ValueError, match="Try get_attribute\\(\\)"):
        sim.get_value("test_column_1")
    with pytest.raises(ValueError, match="Try get_value\\(\\)"):
        sim.get_attribute("simulant_step_size")


class TestGetLookupTable:
    """Tests for retrieving a lookup table from an interactive simulation."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(components=[NestedLookupCaller()])

    def test_the_table_itself_is_returned(self, sim: InteractiveContext) -> None:
        """The getter hands back the table, not the data it holds."""
        table = sim.get_lookup_table("nested_lookup_caller.inner_lookup")
        assert isinstance(table, LookupTable)
        assert table.name == "nested_lookup_caller.inner_lookup"

    def test_the_returned_table_is_callable(self, sim: InteractiveContext) -> None:
        """Calling it with an index is the point of having it, so check the values
        it produces and not just their count."""
        table = sim.get_lookup_table("nested_lookup_caller.inner_lookup")
        index = sim.get_population_index()
        assert len(index) > 0
        # The fixture keys the table on 'inner', which is the index modulo three.
        assert (table(index) == (index % 3 + 1) * 10).all()

    def test_an_unregistered_name_raises(self, sim: InteractiveContext) -> None:
        """An unknown name is rejected rather than quietly accepted."""
        with pytest.raises(ValueError, match="No lookup table 'foo' registered."):
            sim.get_lookup_table("foo")

    def test_every_lookup_table_name_find_resources_reports_can_be_fetched(
        self, sim: InteractiveContext
    ) -> None:
        """The two halves agree: whatever the search surfaces, the getter accepts."""
        found = sim.find_resources()
        names = found.loc[found["resource_type"] == "lookup_table", "name"]
        assert len(names) > 0
        for name in names:
            assert sim.get_lookup_table(name).name == name


def test_run_for_duration() -> None:
    sim = InteractiveContext()
    initial_time = sim._clock.time

    sim.run_for(pd.Timedelta("10 days"))
    assert sim._clock.time == initial_time + pd.Timedelta("10 days")  # type: ignore[operator]

    sim.run_for("5 days")
    assert sim._clock.time == initial_time + pd.Timedelta("15 days")  # type: ignore[operator]


class AttributeNamed(Component):
    """Register one attribute pipeline under a caller-chosen name."""

    def __init__(self, attribute_name: str):
        super().__init__()
        self._attribute_name = attribute_name

    @property
    def name(self) -> str:
        return f"attribute_named.{self._attribute_name}"

    def setup(self, builder: Builder) -> None:
        builder.value.register_attribute_producer(
            self._attribute_name, lambda index: pd.Series(0, index=index)
        )


JAN_1 = pd.Timestamp("2020-01-01")
JAN_8 = pd.Timestamp("2020-01-08")
JAN_15 = pd.Timestamp("2020-01-15")
JAN_31 = pd.Timestamp("2020-01-31")
STEP_SIZE = pd.Timedelta(days=7)
FIRST_STEP_AT_OR_AFTER_END = pd.Timestamp("2020-02-05")
SHORT_SIM_CONFIGURATION: dict[str, Any] = {
    "time": {
        "start": {"year": JAN_1.year, "month": JAN_1.month, "day": JAN_1.day},
        "end": {"year": JAN_31.year, "month": JAN_31.month, "day": JAN_31.day},
        "step_size": STEP_SIZE.days,
    }
}


@pytest.fixture
def short_sim() -> InteractiveContext:
    """A sim configured from Jan 1 to Jan 31 in 7-day steps."""
    return InteractiveContext(configuration=SHORT_SIM_CONFIGURATION)


def reached(time: pd.Timestamp) -> Callable[[InteractiveContext], bool]:
    return lambda sim: bool(sim.current_time >= time)  # type: ignore [operator]


def info_messages(caplog: LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelname == "INFO"]


class TestRunUntilTime:
    """Tests for run_until with a clock time as the target."""

    def test_returns_true(self, short_sim: InteractiveContext) -> None:
        """A time target is always reached."""
        assert short_sim.run_until(JAN_15) is True
        assert short_sim.current_time == JAN_15

    def test_logs_the_number_of_steps(
        self,
        short_sim: InteractiveContext,
        caplog: LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The completion message is logged at INFO, not printed."""
        short_sim.run_until(JAN_15)

        assert "Target reached after 2 iterations" in info_messages(caplog)
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("days_back", [0, 1, 8])
    def test_a_time_at_or_before_now_takes_zero_steps(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture, days_back: int
    ) -> None:
        """A time that has already passed is reached without stepping."""
        short_sim.step()

        assert short_sim.run_until(JAN_8 - pd.Timedelta(days=days_back)) is True
        assert "Target reached after 0 iterations" in info_messages(caplog)
        assert short_sim.current_time == JAN_8

    @pytest.mark.parametrize("days_back, warns", [(0, False), (1, True), (8, True)])
    def test_a_time_in_the_past_warns(
        self,
        short_sim: InteractiveContext,
        caplog: LogCaptureFixture,
        days_back: int,
        warns: bool,
    ) -> None:
        """A time before now is likely a mistake, so it warns; the current time does not."""
        short_sim.step()

        short_sim.run_until(JAN_8 - pd.Timedelta(days=days_back))

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("before the current time" in message for message in warnings) is warns

    def test_max_steps_with_a_time_raises(self, short_sim: InteractiveContext) -> None:
        """max_steps only applies to callables."""
        with pytest.raises(ValueError, match="callable"):
            short_sim.run_until(JAN_15, max_steps=3)
        assert short_sim.current_time == JAN_1


class TestRunUntilCondition:
    """Tests for run_until with a condition (callable) as the target."""

    def test_stops_at_the_first_step_where_the_condition_holds(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture
    ) -> None:
        """The run stops on the first step where the condition is true and stays there."""
        assert short_sim.run_until(reached(JAN_15)) is True
        assert short_sim.current_time == JAN_15
        assert "Target reached after 2 iterations" in info_messages(caplog)

    @pytest.mark.parametrize("value", [True, np.True_], ids=["bool", "numpy_bool"])
    def test_a_condition_already_true_takes_zero_steps(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture, value: bool
    ) -> None:
        """A condition that is already true stops before stepping."""
        assert short_sim.run_until(lambda sim: value) is True
        assert short_sim.current_time == JAN_1
        assert "Target reached after 0 iterations" in info_messages(caplog)

    def test_a_condition_never_true_stops_at_the_configured_end(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture
    ) -> None:
        """The default bound is the configured stop time."""
        assert short_sim.run_until(lambda sim: False) is False
        assert short_sim.current_time == FIRST_STEP_AT_OR_AFTER_END
        assert "Target not reached after 5 iterations (reached max_steps)" in info_messages(
            caplog
        )

    def test_max_steps_can_stop_before_the_configured_end(
        self, short_sim: InteractiveContext
    ) -> None:
        """A small bound stops the run early."""
        assert short_sim.run_until(lambda sim: False, max_steps=2) is False
        assert short_sim.current_time == JAN_1 + STEP_SIZE * 2

    def test_max_steps_can_run_past_the_configured_end(
        self, short_sim: InteractiveContext
    ) -> None:
        """A large bound runs further than the default bound would."""
        assert short_sim.run_until(lambda sim: False, max_steps=7) is False
        assert short_sim.current_time == JAN_1 + STEP_SIZE * 7
        assert short_sim.current_time > FIRST_STEP_AT_OR_AFTER_END  # type: ignore [operator]

    def test_a_negative_max_steps_raises(self, short_sim: InteractiveContext) -> None:
        """A negative bound is a mistake, not zero steps."""
        with pytest.raises(ValueError, match="max_steps"):
            short_sim.run_until(lambda sim: False, max_steps=-1)
        assert short_sim.current_time == JAN_1

    def test_no_steps_remaining_takes_zero_steps(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture
    ) -> None:
        """With the default bound, a sim already past its stop time takes zero steps."""
        short_sim.run()

        assert short_sim.run_until(lambda sim: False) is False
        assert short_sim.current_time == FIRST_STEP_AT_OR_AFTER_END
        assert "Target not reached after 0 iterations (reached max_steps)" in info_messages(
            caplog
        )

    @pytest.mark.parametrize(
        "condition, match",
        [
            (lambda sim: pd.Series([True, False]), r"returned Series\.$"),
            (lambda sim: 1, r"returned int\.$"),
        ],
        ids=["series", "int"],
    )
    def test_a_non_bool_condition_raises(
        self,
        short_sim: InteractiveContext,
        condition: Callable[[InteractiveContext], Any],
        match: str,
    ) -> None:
        """A condition must return a bool, not a value that is merely truthy or falsy."""
        with pytest.raises(TypeError, match=match):
            short_sim.run_until(condition)


class TestRunUntilProgressBar:
    """The notebook progress bar counts the steps run_until actually takes."""

    @pytest.fixture
    def in_notebook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "vivarium.engine.interface.interactive.run_from_ipython", lambda: True
        )

    def test_counts_the_steps_taken(
        self,
        short_sim: InteractiveContext,
        in_notebook: None,
        progress_widgets: list[FakeWidget],
    ) -> None:
        """A run that stops early shows its step count and finishes the bar."""
        short_sim.run_until(reached(JAN_15))

        progress = progress_widgets[0]
        assert progress.value == 2
        assert progress.bar_style == "success"

    def test_no_bar_when_already_reached(
        self,
        short_sim: InteractiveContext,
        in_notebook: None,
        progress_widgets: list[FakeWidget],
    ) -> None:
        """A run that takes no steps draws no bar."""
        short_sim.run_until(lambda sim: True)

        assert progress_widgets == []

    def test_marks_the_bar_failed_when_a_callable_raises(
        self,
        short_sim: InteractiveContext,
        in_notebook: None,
        progress_widgets: list[FakeWidget],
    ) -> None:
        """An error mid-run leaves the bar marked as failed, not finished."""

        def breaks_on_jan_15(sim: InteractiveContext) -> bool:
            if reached(JAN_15)(sim):
                raise KeyError("no such column")
            return False

        with pytest.raises(KeyError):
            short_sim.run_until(breaks_on_jan_15)

        assert progress_widgets[0].bar_style == "danger"


def clock(sim: InteractiveContext) -> ClockTime:
    """Return the current clock time, so an observed value shows when it was evaluated."""
    return sim.current_time


def days_elapsed(sim: InteractiveContext) -> int:
    return int((sim.current_time - JAN_1) / pd.Timedelta(days=1))  # type: ignore [operator]


def hogwarts(observe: bool = True, setup: bool = True) -> InteractiveContext:
    """Return a Hogwarts sim whose model registers the ``house_points`` observation."""
    return InteractiveContext(
        configuration=HARRY_POTTER_CONFIG,
        components=[Hogwarts(), HousePointsObserver(), HogwartsResultsStratifier()],
        observe=observe,
        setup=setup,
    )


class TestObserving:
    """Observations are evaluated at registration and after every step."""

    def test_an_observation_registered_mid_run(self, short_sim: InteractiveContext) -> None:
        """A later observation starts at its registration time; an earlier one keeps its values."""
        short_sim.register_observation(early=clock)
        short_sim.step()
        short_sim.register_observation(late=clock)
        short_sim.step()

        results = short_sim.get_results()
        assert list(results["early"]) == [JAN_1, JAN_8, JAN_15]
        assert list(results["late"]) == [JAN_8, JAN_15]

    def test_values_are_stored_as_returned(self, short_sim: InteractiveContext) -> None:
        """Series, DataFrame, None and arbitrary object returns are stored as the very objects returned."""
        series = pd.Series([1.0, 2.0])
        frame = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        anything = object()
        short_sim.register_observation(
            series=lambda sim: series,
            frame=lambda sim: frame,
            none=lambda sim: None,
            anything=lambda sim: anything,
        )
        short_sim.step()

        results = short_sim.get_results()
        for time in (JAN_1, JAN_8):
            assert results["series"][time] is series
            assert results["frame"][time] is frame
            assert time in results["none"]
            assert results["none"][time] is None
            assert results["anything"][time] is anything

    def test_get_results_returns_a_copy(self, short_sim: InteractiveContext) -> None:
        """Changing the returned dicts at either level does not change the observations."""
        short_sim.register_observation(clock=clock, days=days_elapsed)

        results = short_sim.get_results()
        del results["days"]
        results["added"] = {}
        results["clock"][JAN_1] = "replaced"

        assert short_sim.get_results() == {"clock": {JAN_1: JAN_1}, "days": {JAN_1: 0}}


class TestObservationsInResults:
    """get_results() returns the model's results and the registered observations together."""

    def test_model_results_and_observations_are_returned_together(self) -> None:
        sim = hogwarts()
        start = sim.current_time
        sim.register_observation(clock=clock)
        sim.step()

        results = sim.get_results()
        assert results["house_points"][VALUE_COLUMN].sum() > 0
        assert list(results["clock"]) == [start, sim.current_time]

    def test_pausing_leaves_model_observations_running(self) -> None:
        """Pausing every registered observation does not stop the model's observations."""
        sim = hogwarts()
        sim.register_observation(clock=clock)
        sim.step()
        points_before = sim.get_results()["house_points"][VALUE_COLUMN].sum()

        sim.pause_observation("clock")
        sim.step()

        assert sim.get_results()["house_points"][VALUE_COLUMN].sum() > points_before

    def test_observations_alone_do_not_warn(self, caplog: LogCaptureFixture) -> None:
        """With observe=False, registered observations are results, so there is nothing to warn about."""
        sim = hogwarts(observe=False)
        sim.register_observation(clock=clock)

        assert list(sim.get_results()) == ["clock"]
        assert "pass observe=True" not in caplog.text


class TestObservationRegistration:
    """register_observation() registers all of its observations or none of them."""

    def test_a_duplicate_name_raises_and_keeps_the_original(
        self, short_sim: InteractiveContext
    ) -> None:
        """Registering a name that is already an observation raises ValueError and keeps the original."""
        short_sim.register_observation(clock=clock)

        with pytest.raises(ValueError, match="clock"):
            short_sim.register_observation(
                other=days_elapsed, clock=lambda sim: "replacement"
            )

        short_sim.step()
        assert short_sim.get_results() == {"clock": {JAN_1: JAN_1, JAN_8: JAN_8}}

    def test_a_model_observation_name_raises_after_setup(self) -> None:
        """A name the model already observes raises at registration, naming it."""
        sim = hogwarts()

        with pytest.raises(ValueError, match="house_points"):
            sim.register_observation(house_points=clock)

        assert "house_points" not in sim._interactive_observations

    def test_a_model_observation_name_raises_at_setup(self) -> None:
        """A name registered before setup is checked against the model's observations by setup."""
        sim = hogwarts(setup=False)
        sim.register_observation(house_points=clock)

        with pytest.raises(ValueError, match="house_points.*register_observation"):
            sim.setup()

    def test_registering_before_setup_observes_at_setup(self) -> None:
        """An observation registered before setup gets its first value when setup runs."""
        sim = InteractiveContext(configuration=SHORT_SIM_CONFIGURATION, setup=False)

        sim.register_observation(clock=clock)
        assert sim.get_results() == {"clock": {}}

        sim.setup()
        assert sim.get_results() == {"clock": {JAN_1: JAN_1}}


class TestObservationFailure:
    """An observation that raises is attributable and leaves consistent values."""

    @staticmethod
    def fails_from(
        time: pd.Timestamp, error: Exception | None = None
    ) -> Callable[[InteractiveContext], ClockTime]:
        """Return an observation that returns the clock until ``time`` and then raises."""
        to_raise = error if error is not None else KeyError("no such column")

        def observation(sim: InteractiveContext) -> ClockTime:
            if reached(time)(sim):
                raise to_raise
            return sim.current_time

        return observation

    @pytest.mark.parametrize(
        "fails_at", [JAN_1, JAN_8], ids=["at_registration", "after_a_step"]
    )
    def test_a_failure_raises_observation_error(
        self, short_sim: InteractiveContext, fails_at: pd.Timestamp
    ) -> None:
        """The ObservationError names the observation and the clock time and chains the original."""
        original = KeyError("no such column")

        with pytest.raises(ObservationError) as error:
            short_sim.register_observation(broken=self.fails_from(fails_at, original))
            short_sim.step()

        message = str(error.value)
        assert "broken" in message
        assert str(fails_at) in message
        assert error.value.__cause__ is original

    @pytest.mark.parametrize(
        "failing, expected",
        [
            (fails_from(JAN_1), {}),
            (
                fails_from(JAN_15),
                {
                    name: {JAN_1: JAN_1, JAN_8: JAN_8}
                    for name in ("before", "failing", "after")
                },
            ),
        ],
        ids=["fails_at_registration", "fails_after_a_step"],
    )
    def test_a_failure_stores_nothing_at_the_failing_time(
        self,
        short_sim: InteractiveContext,
        failing: Callable[[InteractiveContext], ClockTime],
        expected: dict[str, dict[ClockTime, Any]],
    ) -> None:
        """A failing observation leaves no value for any observation at the time it failed."""
        # Working observations on both sides of the failing one, so neither evaluation
        # order hides a stored value.
        with pytest.raises(ObservationError):
            short_sim.register_observation(before=clock, failing=failing, after=clock)
            short_sim.take_steps(3)

        assert short_sim.get_results() == expected

    def test_a_failing_observation_still_restores_a_custom_step_size(
        self, short_sim: InteractiveContext
    ) -> None:
        """After an observation fails on a step with a custom step_size, the next step uses the configured size.

        This is a regression test for the order of operations in ``step``: the step size
        must be restored before the observations are evaluated, or a failing observation
        would skip the restore.
        """
        three_days_on = JAN_1 + pd.Timedelta(days=3)
        short_sim.register_observation(broken=self.fails_from(three_days_on))

        with pytest.raises(ObservationError):
            short_sim.step(pd.Timedelta(days=3))

        short_sim.pause_observation("broken")
        short_sim.step()

        assert short_sim.current_time == three_days_on + STEP_SIZE

    def test_an_observation_may_pause_another_while_running(
        self, short_sim: InteractiveContext
    ) -> None:
        """An observation that pauses another mid-step stops it without breaking observing."""

        def stops_other(sim: InteractiveContext) -> ClockTime:
            if reached(JAN_8)(sim):
                sim.pause_observation("other")
            return sim.current_time

        short_sim.register_observation(stopper=stops_other, other=clock)
        short_sim.take_steps(2)

        assert short_sim.get_results() == {
            "stopper": {JAN_1: JAN_1, JAN_8: JAN_8, JAN_15: JAN_15},
            "other": {JAN_1: JAN_1, JAN_8: JAN_8},
        }


class TestPauseObservation:
    """pause_observation() stops observations, keeping their values, and register_observation() resumes them."""

    def test_stops_calling_but_keeps_the_values(self, short_sim: InteractiveContext) -> None:
        """A paused observation is not called again but keeps its values; others continue."""
        calls: list[ClockTime] = []

        def counted(sim: InteractiveContext) -> None:
            calls.append(sim.current_time)

        short_sim.register_observation(counted=counted, days=days_elapsed)
        short_sim.step()
        short_sim.pause_observation("counted")
        short_sim.step()

        assert calls == [JAN_1, JAN_8]
        assert short_sim.get_results() == {
            "counted": {JAN_1: None, JAN_8: None},
            "days": {JAN_1: 0, JAN_8: 7, JAN_15: 14},
        }

    def test_a_paused_observation_resumes_by_name(
        self, short_sim: InteractiveContext
    ) -> None:
        """Passing a paused observation's name to register_observation resumes it, leaving a gap."""
        jan_22, jan_29 = JAN_1 + STEP_SIZE * 3, JAN_1 + STEP_SIZE * 4
        short_sim.register_observation(clock=clock)
        short_sim.step()
        short_sim.pause_observation("clock")
        short_sim.take_steps(2)

        short_sim.register_observation("clock")
        short_sim.step()

        assert list(short_sim.get_results()["clock"]) == [JAN_1, JAN_8, jan_22, jan_29]

    def test_resuming_a_registered_observation_does_nothing(
        self, short_sim: InteractiveContext, caplog: LogCaptureFixture
    ) -> None:
        """Passing the name of an observation that is still registered changes nothing and logs that."""
        short_sim.register_observation(clock=clock)

        short_sim.register_observation("clock")

        assert short_sim.get_results() == {"clock": {JAN_1: JAN_1}}
        assert "Observations ['clock'] are already registered." in info_messages(caplog)

    @pytest.mark.parametrize(
        "call",
        [
            lambda sim: sim.pause_observation("clock", "not_observed"),
            lambda sim: sim.register_observation("not_observed"),
        ],
        ids=["pause_observation", "register_observation"],
    )
    def test_an_unknown_name_raises_and_changes_nothing(
        self, short_sim: InteractiveContext, call: Callable[[InteractiveContext], None]
    ) -> None:
        """An unknown name raises ValueError and the observations are left as they were."""
        short_sim.register_observation(clock=clock)

        with pytest.raises(ValueError, match="not_observed"):
            call(short_sim)

        short_sim.step()
        assert short_sim.get_results() == {"clock": {JAN_1: JAN_1, JAN_8: JAN_8}}


def test_observes_an_unmodified_model(disease_model_spec: Path) -> None:
    """An observation on the example disease model is stored every step with no spec or component change."""
    sim = InteractiveContext(str(disease_model_spec))
    sim.register_observation(mean_age=lambda s: s.get_population("age").mean())
    start = sim.current_time

    sim.take_steps(3)

    observed = sim.get_results()["mean_age"]
    assert len(observed) == 4
    assert list(observed)[0] == start
    assert observed[sim.current_time] == sim.get_population("age").mean()


class TestFindResources:
    """Tests for locating simulation resources by name or registering component."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(
            components=[
                ColumnCreator(),
                ColumnCreatorAndRequirer(),
                AttributePipelineCreator(),
            ]
        )

    def test_the_frame_has_the_conventional_columns(self, sim: InteractiveContext) -> None:
        """This shape is the convention the other introspection tools follow."""
        found = sim.find_resources("test_column_1")
        assert list(found.columns) == ["name", "resource_type", "component"]

    def test_a_fragment_need_not_be_the_whole_name(self, sim: InteractiveContext) -> None:
        assert "test_column_1" in set(sim.find_resources("column_1")["name"])

    def test_unsearchable_types_are_not_reported(self, sim: InteractiveContext) -> None:
        reported = set(sim.find_resources()["resource_type"])
        assert not {"column", "stream", "initializer"} & reported

    def test_lookup_tables_are_reported(self) -> None:
        """The shared fixture registers none, so this one needs its own sim."""
        sim = InteractiveContext(components=[NestedLookupCaller()])
        found = sim.find_resources("inner_lookup")
        assert set(found["resource_type"]) == {"lookup_table"}

    def test_matching_is_case_insensitive(self, sim: InteractiveContext) -> None:
        assert sim.find_resources("TEST_COLUMN_1").equals(sim.find_resources("test_column_1"))

    def test_the_pattern_is_literal_by_default(self, sim: InteractiveContext) -> None:
        """A name copied out of an earlier result finds itself, so regex syntax in
        it is matched as text rather than silently changing the search."""
        assert sim.find_resources("test_column_.").empty

    def test_the_pattern_is_a_regular_expression_when_asked(
        self, sim: InteractiveContext
    ) -> None:
        """The same pattern, with the flag, treats '.' as a wildcard."""
        assert not sim.find_resources("test_column_.", regex=True).empty

    def test_regex_anchors_isolate_a_precise_name(self, sim: InteractiveContext) -> None:
        """Anchoring narrows a broad search rather than finding something else."""
        broad = sim.find_resources("test_column_")
        precise = sim.find_resources("^test_column_[12]$", regex=True)
        assert set(precise["name"]) == {"test_column_1", "test_column_2"}
        assert set(precise.itertuples(index=False)) < set(broad.itertuples(index=False))

    def test_a_component_name_matches_the_resources_it_registered(
        self, sim: InteractiveContext
    ) -> None:
        """No node is named 'column_creator_and_requirer', so every row here
        matched on its component rather than its own name."""
        found = sim.find_resources("^column_creator_and_requirer$", regex=True)
        assert set(found["component"]) == {"column_creator_and_requirer"}

    def test_an_exact_name_match_comes_first(self) -> None:
        """The thing you named outranks everything merely containing it, even one
        whose name sorts earlier alphabetically."""
        sim = InteractiveContext(
            components=[
                AttributeNamed("needle"),
                AttributeNamed("here_is_a_needle_in_a_haystack"),
            ]
        )
        assert list(sim.find_resources("needle")["name"]) == [
            "needle",
            "here_is_a_needle_in_a_haystack",
        ]

    @pytest.mark.parametrize(
        "pattern, name, expected",
        [
            ("a.b.c", "a.b.c", 0),
            ("a.b", "a.b.c", 1),
            ("a", "a.b.c", 1),
            ("b", "a.b.c", 2),
            ("c", "a.b.c", 2),
            ("b.c", "a.b.c", 2),
            ("b_x", "a.b_x_y.c", 3),
            ("y.c", "a.b_x_y.c", 3),
            ("ever", "test_ever_eligible", 3),
            ("umn_1", "test_column_1", 4),
            ("ever", "never_treated", 4),
            ("", "a.b.c", 1),
            ("zzz", "a.b.c", 5),
        ],
        ids=[
            "whole name",
            "leading segments",
            "one leading segment",
            "middle segment",
            "last segment",
            "several segments to the end",
            "whole words inside a segment",
            "whole words across a segment boundary",
            "a whole word in a name with no dots",
            "part of a word in a name with no dots",
            "a fragment buried in a longer word",
            "an empty pattern matches at the start",
            "no match on the name at all",
        ],
    )
    def test_each_rank_is_assigned_as_documented(
        self, pattern: str, name: str, expected: int
    ) -> None:
        """Pin the tiers directly: an ordering test cannot distinguish them when the
        alphabetical tiebreak happens to agree with the ranking."""
        matcher = re.compile(re.escape(pattern), re.IGNORECASE)
        assert _match_rank(matcher, name) == expected

    def test_a_whole_segment_outranks_part_of_one(self) -> None:
        """Names are dotted hierarchies, so filling a segment means more than
        straddling one. The names are chosen so alphabetical order contradicts the
        ranking: only the segment rule can put ``zz_outer.needle`` first."""
        sim = InteractiveContext(
            components=[AttributeNamed("zz_outer.needle"), AttributeNamed("aa_needle_part")]
        )
        assert list(sim.find_resources("needle")["name"]) == [
            "zz_outer.needle",
            "aa_needle_part",
        ]

    def test_a_whole_word_outranks_a_fragment_of_one(self) -> None:
        """Segments are underscore-separated words, so a pattern filling whole words
        means more than one buried inside a longer word. Alphabetical order again
        contradicts the ranking, so only the word rule can put ``zz_`` first."""
        sim = InteractiveContext(
            components=[
                AttributeNamed("zz_test_ever_eligible"),
                AttributeNamed("aa_never_treated"),
            ]
        )
        assert list(sim.find_resources("ever")["name"]) == [
            "zz_test_ever_eligible",
            "aa_never_treated",
        ]

    def test_regex_mode_is_ranked_the_same_way(self) -> None:
        """The rank comes from the match's span, so the flag does not change it.
        These are the names of the segment test, ordered against the alphabet, so
        a regex path that lost the span would reorder them."""
        sim = InteractiveContext(
            components=[AttributeNamed("zz_outer.needle"), AttributeNamed("aa_needle_part")]
        )
        assert list(sim.find_resources("n[e]+dle", regex=True)["name"]) == [
            "zz_outer.needle",
            "aa_needle_part",
        ]

    def test_a_component_only_match_comes_last(self) -> None:
        """Matching through the component is the surprising hit, so it sorts last.
        Only ``zz_column_creator`` matches on its own name; the rest are reached
        through their component, and alphabetical order would lead with them."""
        sim = InteractiveContext(
            components=[AttributeNamed("zz_column_creator"), ColumnCreator()]
        )
        assert list(sim.find_resources("column_creator")["name"]) == [
            "zz_column_creator",
            "test_column_1",
            "test_column_2",
            "test_column_3",
        ]

    def test_ties_break_by_resource_type(self) -> None:
        """Within one rank, attributes come before modifiers, not alphabetically.
        The lookup table is what makes this discriminating: the other three types
        happen to fall in alphabetical order on their own, so a sim without one
        would pass under either rule."""
        sim = InteractiveContext(
            components=[
                ColumnCreator(),
                AttributePipelineCreator(),
                NestedLookupCaller(),
            ]
        )
        order = ["attribute", "value", "value_modifier", "lookup_table"]
        types = list(sim.find_resources()["resource_type"])
        assert set(types) == set(order)
        assert types == sorted(types, key=order.index)

    def test_ties_then_break_by_name(self, sim: InteractiveContext) -> None:
        """Within one rank and one resource type, names run alphabetically."""
        found = sim.find_resources()
        attributes = list(found.loc[found["resource_type"] == "attribute", "name"])
        assert attributes == sorted(attributes)

    def test_every_other_resource_in_the_graph_is_reachable(
        self, sim: InteractiveContext
    ) -> None:
        """Only columns, streams, and initializers are withheld; nothing else is."""
        expected = {
            (str(node.name), node.RESOURCE_TYPE, node.component.name)
            for node in sim._resource.get_graph().nodes
            if node.RESOURCE_TYPE not in ("column", "stream", "initializer")
        }
        assert set(sim.find_resources().itertuples(index=False)) == expected

    def test_no_pattern_lists_every_resource(self, sim: InteractiveContext) -> None:
        pd.testing.assert_frame_equal(sim.find_resources(), sim.find_resources(""))

    def test_no_match_returns_an_empty_frame(self, sim: InteractiveContext) -> None:
        assert sim.find_resources("no_such_resource").empty

    def test_an_empty_result_keeps_the_conventional_columns(
        self, sim: InteractiveContext
    ) -> None:
        found = sim.find_resources("no_such_resource")
        assert list(found.columns) == ["name", "resource_type", "component"]

    def test_an_invalid_regex_raises_a_useful_error(self, sim: InteractiveContext) -> None:
        with pytest.raises(ValueError, match="Invalid regular expression 'test_column_\\['"):
            sim.find_resources("test_column_[", regex=True)

    def test_that_same_pattern_is_harmless_without_the_regex_flag(
        self, sim: InteractiveContext
    ) -> None:
        """Literal matching cannot raise, whatever the text contains."""
        assert sim.find_resources("test_column_[").empty


class TestPrintDependencies:
    """Tests for printing what a resource uses and what uses it."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(
            components=[
                DiamondAttributeCreator(),
                ColumnCreator(),
                ColumnCreatorAndRequirer(),
            ]
        )

    @staticmethod
    def printed(
        sim: InteractiveContext,
        capsys: pytest.CaptureFixture[str],
        *args: Any,
        **kwargs: Any,
    ) -> list[str]:
        sim.print_dependencies(*args, **kwargs)
        return capsys.readouterr().out.splitlines()

    def test_uses_reports_what_the_resource_reads(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert self.printed(sim, capsys, "attribute.diamond_base", "uses") == [
            "attribute.diamond_base",
            "└── attribute.diamond_root",
        ]

    def test_used_by_reports_what_reads_the_resource(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The same resource, walked the other way, reports different neighbors."""
        assert self.printed(sim, capsys, "attribute.diamond_base", "used-by") == [
            "attribute.diamond_base",
            "├── attribute.diamond_left  (1 direct branch hidden)",
            "└── attribute.diamond_right  (1 direct branch hidden)",
        ]

    def test_dependencies_are_grouped_by_resource_type(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """What feeds a resource comes first and what is layered over it comes last.
        Sorting by resource id alone would put the lookup table above the value, and
        find_resources would put the modifier above the lookup table."""
        sim = InteractiveContext(components=[OrderedDependencyCreator()])
        assert self.printed(sim, capsys, "attribute.ordered", "uses") == [
            "attribute.ordered",
            "├── attribute.ordered_attribute",
            "├── value.ordered_value",
            "├── lookup_table.ordered_dependency_creator.ordered_table",
            "└── value_modifier.ordered.1.ordered_dependency_creator.modifier",
        ]

    def test_a_depth_of_none_walks_the_whole_chain(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert self.printed(sim, capsys, "attribute.test_column_4", "uses", depth=None) == [
            "attribute.test_column_4",
            "└── column.test_column_4",
            "    └── initializer.2.column_creator_and_requirer.initialize_test_column_4",
            "        ├── attribute.test_column_2",
            "        │   └── column.test_column_2",
            "        │       └── initializer.1.column_creator.initialize_test_columns",
            "        └── stream.stream_1",
        ]

    def test_a_shared_node_is_drawn_under_every_parent(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Both halves of the diamond keep their edge to what they share, but only
        the first one unrolls it. TestRenderDependencyTree covers the shape rules
        against graphs; this checks one of them on a graph the engine really builds."""
        assert self.printed(sim, capsys, "attribute.diamond_top", "uses", depth=3) == [
            "attribute.diamond_top",
            "├── attribute.diamond_left",
            "│   └── attribute.diamond_base",
            "│       └── attribute.diamond_root",
            "└── attribute.diamond_right",
            "    └── attribute.diamond_base  (expanded above)",
        ]

    def test_a_root_with_no_dependencies_says_so(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Otherwise the bare name alone reads as though the call failed."""
        assert self.printed(sim, capsys, "attribute.diamond_top", "used-by") == [
            "attribute.diamond_top",
            "    (none)",
        ]

    def test_a_bare_name_a_full_id_and_an_object_agree(
        self, sim: InteractiveContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        node = sim.get_attribute("diamond_base")
        by_name = self.printed(sim, capsys, "diamond_base", "uses")
        assert by_name[0] == "attribute.diamond_base"
        assert by_name == self.printed(sim, capsys, "attribute.diamond_base", "uses")
        assert by_name == self.printed(sim, capsys, node, "uses")

    def test_an_ambiguous_bare_name_lists_the_candidates(
        self, sim: InteractiveContext
    ) -> None:
        """A private column usually shares its name with an attribute, so the bare
        name has to be refused rather than resolved to whichever comes first."""
        with pytest.raises(ValueError, match="matches more than one resource") as error:
            sim.print_dependencies("test_column_1", "uses")
        assert "'attribute.test_column_1'" in str(error.value)
        assert "'column.test_column_1'" in str(error.value)

    def test_an_unknown_name_points_at_find_resources(self, sim: InteractiveContext) -> None:
        with pytest.raises(ValueError, match="No resource named 'no_such_resource'") as error:
            sim.print_dependencies("no_such_resource", "uses")
        assert "find_resources" in str(error.value)

    def test_a_resource_outside_the_graph_raises(self, sim: InteractiveContext) -> None:
        unregistered = Column("not_registered", ColumnCreator())
        with pytest.raises(ValueError, match="is not in the resource graph"):
            sim.print_dependencies(unregistered, "uses")

    @pytest.mark.parametrize("depth", [0, -1])
    def test_a_depth_below_one_raises(self, sim: InteractiveContext, depth: int) -> None:
        with pytest.raises(ValueError, match=f"A depth of {depth} is not valid"):
            sim.print_dependencies("attribute.diamond_top", "uses", depth=depth)

    def test_an_unknown_direction_raises(self, sim: InteractiveContext) -> None:
        """The annotation rules this out, but nothing stops an untyped caller."""
        unknown_direction: Any = "upstream"
        with pytest.raises(ValueError, match="Unknown direction 'upstream'"):
            sim.print_dependencies("attribute.diamond_top", unknown_direction)


class TestGetDependencies:
    """Tests for getting the resources a resource uses and the ones that use it."""

    @pytest.fixture(scope="class")
    def sim(self) -> InteractiveContext:
        return InteractiveContext(
            components=[
                DiamondAttributeCreator(),
                ColumnCreator(),
                ColumnCreatorAndRequirer(),
            ]
        )

    @staticmethod
    def ids(resources: list[Resource]) -> list[str]:
        return [str(resource.resource_id) for resource in resources]

    def test_uses_reports_what_the_resource_reads(self, sim: InteractiveContext) -> None:
        assert self.ids(sim.get_dependencies("attribute.diamond_base", "uses")) == [
            "attribute.diamond_root"
        ]

    def test_used_by_reports_what_reads_the_resource(self, sim: InteractiveContext) -> None:
        """The same resource, walked the other way, reports different neighbors."""
        assert self.ids(sim.get_dependencies("attribute.diamond_base", "used-by")) == [
            "attribute.diamond_left",
            "attribute.diamond_right",
        ]

    def test_the_graph_nodes_themselves_are_returned(self, sim: InteractiveContext) -> None:
        """The point of this over print_dependencies is having the resource in hand."""
        assert sim.get_dependencies("attribute.diamond_base", "uses") == [
            sim.get_attribute("diamond_root")
        ]

    def test_an_unreported_type_one_level_away_yields_nothing(
        self, sim: InteractiveContext
    ) -> None:
        """An attribute is backed by a column, so walking one level reports nothing
        even though print_dependencies draws that column."""
        assert sim.get_dependencies("attribute.test_column_4", "uses") == []

    def test_reported_and_unreported_neighbors_are_separated(
        self, sim: InteractiveContext
    ) -> None:
        """This initializer reads one attribute and one randomness stream, and only
        the attribute comes back."""
        initializer = "initializer.2.column_creator_and_requirer.initialize_test_column_4"
        assert self.ids(sim.get_dependencies(initializer, "uses")) == [
            "attribute.test_column_2"
        ]

    def test_results_are_grouped_by_resource_type(self) -> None:
        """The same order print_dependencies draws, which sorting by resource id
        alone would not give."""
        sim = InteractiveContext(components=[OrderedDependencyCreator()])
        assert self.ids(sim.get_dependencies("attribute.ordered", "uses")) == [
            "attribute.ordered_attribute",
            "value.ordered_value",
            "lookup_table.ordered_dependency_creator.ordered_table",
            "value_modifier.ordered.1.ordered_dependency_creator.modifier",
        ]

    def test_same_type_results_are_sorted_by_name(self) -> None:
        """Two neighbors of one type, registered in the reverse of name order, so
        a sort that ignored the name would hand these back the other way around."""
        sim = InteractiveContext(components=[ReversedNameCreator()])
        assert self.ids(sim.get_dependencies("attribute.reversed_root", "uses")) == [
            "attribute.alpha",
            "attribute.zulu",
        ]

    def test_modifiers_past_the_ninth_stay_in_application_order(self) -> None:
        """The number in a modifier's name is the order it is applied in. Past the
        ninth that number is two digits, which a plain text sort gets wrong."""
        sim = InteractiveContext(components=[ManyModifierCreator()])
        assert self.ids(sim.get_dependencies("value.many_modifiers", "uses")) == [
            f"value_modifier.many_modifiers.{number}.many_modifier_creator"
            f".modifier_{number - 1:02d}"
            for number in range(1, ManyModifierCreator.MODIFIER_COUNT + 1)
        ]

    def test_a_root_with_no_dependencies_returns_nothing(
        self, sim: InteractiveContext
    ) -> None:
        assert sim.get_dependencies("attribute.diamond_top", "used-by") == []

    def test_a_bare_name_a_full_id_and_an_object_agree(self, sim: InteractiveContext) -> None:
        node = sim.get_attribute("diamond_base")
        by_name = sim.get_dependencies("diamond_base", "uses")
        assert by_name == sim.get_dependencies("attribute.diamond_base", "uses")
        assert by_name == sim.get_dependencies(node, "uses")

    def test_an_ambiguous_bare_name_lists_the_candidates(
        self, sim: InteractiveContext
    ) -> None:
        with pytest.raises(ValueError, match="matches more than one resource") as error:
            sim.get_dependencies("test_column_1", "uses")
        assert "'attribute.test_column_1'" in str(error.value)
        assert "'column.test_column_1'" in str(error.value)

    def test_an_unknown_name_points_at_find_resources(self, sim: InteractiveContext) -> None:
        with pytest.raises(ValueError, match="No resource named 'no_such_resource'") as error:
            sim.get_dependencies("no_such_resource", "uses")
        assert "find_resources" in str(error.value)

    def test_a_resource_outside_the_graph_raises(self, sim: InteractiveContext) -> None:
        unregistered = Column("not_registered", ColumnCreator())
        with pytest.raises(ValueError, match="is not in the resource graph"):
            sim.get_dependencies(unregistered, "uses")

    def test_an_unknown_direction_raises(self, sim: InteractiveContext) -> None:
        """The annotation rules this out, but nothing stops an untyped caller."""
        unknown_direction: Any = "upstream"
        with pytest.raises(ValueError, match="Unknown direction 'upstream'"):
            sim.get_dependencies("attribute.diamond_top", unknown_direction)


class _Node(Resource):
    """A bare graph node, typed so that rendered ids stay short in assertions."""

    RESOURCE_TYPE = "node"


class TestRenderDependencyTree:
    """Tests for how the renderer draws a shape, independent of any simulation.

    Reaching these shapes through the public method costs a registered component
    apiece, which buries the rule under pipeline registration. Stating the graph
    directly keeps the rule in view.
    """

    @staticmethod
    def build_graph(
        dependencies: dict[str, str]
    ) -> tuple[nx.DiGraph[Resource], dict[str, _Node]]:
        """Build a resource graph from a mapping of each node to the names it uses."""
        names = set(dependencies) | {
            name for used in dependencies.values() for name in used.split()
        }
        nodes = {name: _Node(name, None) for name in names}
        graph: nx.DiGraph[Resource] = nx.DiGraph()
        graph.add_nodes_from(nodes.values())
        for parent, children in dependencies.items():
            for child in children.split():
                graph.add_edge(nodes[child], nodes[parent])
        return graph, nodes

    @pytest.fixture
    def diamond(self) -> tuple[nx.DiGraph[Resource], dict[str, _Node]]:
        """``a`` uses ``b`` and ``c``, both use ``d``, and ``d`` uses ``e``.

        The names are listed out of alphabetical order so that the expected output
        differs from the order networkx hands back. Listing them in order would let
        a renderer that never sorted still pass.
        """
        return self.build_graph({"a": "c b", "c": "d", "b": "d", "d": "e"})

    def test_a_shared_node_keeps_both_edges_but_unrolls_once(
        self, diamond: tuple[nx.DiGraph[Resource], dict[str, _Node]]
    ) -> None:
        graph, nodes = diamond
        assert _render_dependency_tree(graph, nodes["a"], "uses", 3) == [
            "node.a",
            "├── node.b",
            "│   └── node.d",
            "│       └── node.e",
            "└── node.c",
            "    └── node.d  (expanded above)",
        ]

    def test_a_node_cut_off_at_the_limit_is_never_called_expanded(
        self, diamond: tuple[nx.DiGraph[Resource], dict[str, _Node]]
    ) -> None:
        """Neither arrival expanded it, so both report what they are hiding."""
        graph, nodes = diamond
        assert _render_dependency_tree(graph, nodes["a"], "uses", 2) == [
            "node.a",
            "├── node.b",
            "│   └── node.d  (1 direct branch hidden)",
            "└── node.c",
            "    └── node.d  (1 direct branch hidden)",
        ]

    def test_a_node_reached_again_above_the_limit_is_expanded_there(self) -> None:
        """``shared`` is reached under ``branch`` first, at the limit, and then as a
        direct dependency where there is room for it."""
        graph, nodes = self.build_graph(
            {"a": "branch shared", "branch": "shared", "shared": "tail"}
        )
        assert _render_dependency_tree(graph, nodes["a"], "uses", 2) == [
            "node.a",
            "├── node.branch",
            "│   └── node.shared  (1 direct branch hidden)",
            "└── node.shared",
            "    └── node.tail",
        ]

    def test_an_expanded_node_reached_at_the_limit_is_not_called_hidden(self) -> None:
        """``s`` is expanded as a direct dependency, then reached again under ``t``
        exactly at the limit, where both annotations apply. Having been expanded
        wins, because its dependencies are on the screen already."""
        graph, nodes = self.build_graph({"a": "t s", "t": "s", "s": "u"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", 2) == [
            "node.a",
            "├── node.s",
            "│   └── node.u",
            "└── node.t",
            "    └── node.s  (expanded above)",
        ]

    def test_a_cycle_stops_where_it_rejoins_an_expanded_node(self) -> None:
        """``b`` is marked expanded before the walk descends into it, so arriving
        back at it from ``c`` ends the branch. Marking it afterwards instead
        recurses without end, and no acyclic graph tells the two orders apart."""
        graph, nodes = self.build_graph({"a": "b", "b": "c", "c": "b"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", None) == [
            "node.a",
            "└── node.b",
            "    └── node.c",
            "        └── node.b  (expanded above)",
        ]

    def test_a_repeated_leaf_is_left_unmarked(self) -> None:
        """Nothing was expanded and nothing is hidden, so both arrivals print plain."""
        graph, nodes = self.build_graph({"a": "c b", "c": "d", "b": "d"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", None) == [
            "node.a",
            "├── node.b",
            "│   └── node.d",
            "└── node.c",
            "    └── node.d",
        ]

    def test_a_hidden_branch_counts_what_it_hides(self) -> None:
        graph, nodes = self.build_graph({"a": "b", "b": "e d c"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", 1) == [
            "node.a",
            "└── node.b  (3 direct branches hidden)",
        ]

    def test_a_leaf_at_the_limit_reports_nothing(self) -> None:
        """A branch that ends at the limit hides nothing, so it is left unmarked."""
        graph, nodes = self.build_graph({"a": "b"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", 1) == [
            "node.a",
            "└── node.b",
        ]

    def test_a_run_of_digits_in_a_name_sorts_as_a_number(self) -> None:
        """Resource names carry unpadded indices, so ``10`` is drawn after ``2``.

        The tree and the list share one sort key, so this is what keeps the
        renderer on it.
        """
        graph, nodes = self.build_graph({"a": "b10 b9 b2 b1"})
        assert _render_dependency_tree(graph, nodes["a"], "uses", 1) == [
            "node.a",
            "├── node.b1",
            "├── node.b2",
            "├── node.b9",
            "└── node.b10",
        ]

    def test_used_by_walks_the_other_way(
        self, diamond: tuple[nx.DiGraph[Resource], dict[str, _Node]]
    ) -> None:
        """The same graph, read from the far end, inverts every edge."""
        graph, nodes = diamond
        assert _render_dependency_tree(graph, nodes["e"], "used-by", None) == [
            "node.e",
            "└── node.d",
            "    ├── node.b",
            "    │   └── node.a",
            "    └── node.c",
            "        └── node.a",
        ]


def test_get_attribute_names() -> None:
    sim = InteractiveContext(
        components=[MultiLevelMultiColumnCreator(), AttributePipelineCreator()]
    )
    expected_attributes = [
        # MultiLevelMultiColumnCreator attributes
        "some_attribute",
        "some_other_attribute",
        # AttributePipelineCreator attributes
        "attribute_generating_columns_4_5",
        "attribute_generating_column_8",
        "test_attribute",
        "attribute_generating_columns_6_7",
    ]
    assert set(sim.get_attribute_names()) == set(expected_attributes)
    # Make sure there's nothing unexpected compared to the actual population df
    assert set(sim.get_attribute_names()) == set(
        sim.get_population(expected_attributes).columns.get_level_values(0)
    )


def test_get_population_querying() -> None:
    sim = InteractiveContext(components=[ColumnCreator()])
    assert set(sim.get_population("test_column_1")) == {0, 1, 2}
    assert set(sim.get_population("test_column_1", query="test_column_1 > 0")) == {1, 2}


@pytest.mark.parametrize("include_untracked", [None, True, False])
def test_get_population_include_untracked(
    include_untracked: bool | None, mocker: MockerFixture
) -> None:
    sim = InteractiveContext(components=[ColumnCreator()])
    sim._population.register_tracked_query("test_column_1 > 0")
    # Need to mock lifecycle state away from initialization or population-creation
    mocker.patch.object(sim._population, "get_current_state", lambda: "on_time_step")

    kwargs = {}
    if include_untracked is not None:
        kwargs["include_untracked"] = include_untracked
    pop = sim.get_population("test_column_1", **kwargs)  # type: ignore[call-overload]
    if include_untracked is True:
        assert set(pop) == {0, 1, 2}
    else:
        assert set(pop) == {1, 2}


def test_get_population_squeezing() -> None:

    # Single-level, single-column -> series
    sim = InteractiveContext(components=[SingleColumnCreator()])
    unsqueezed = sim.get_population(["test_column_1"])
    squeezed = sim.get_population("test_column_1")
    assert_squeezing_single_level_single_col(unsqueezed, squeezed, "test_column_1")

    # Single-level, multiple-column -> dataframe
    component = ColumnCreator()
    sim = InteractiveContext(components=[component], setup=True)
    # There's no way to request a squeezed dataframe here.
    df = sim.get_population(["test_column_1", "test_column_2", "test_column_3"])
    assert isinstance(df, pd.DataFrame)
    assert not isinstance(df.columns, pd.MultiIndex)

    # Multi-level, single outer, single inner -> series
    sim = InteractiveContext(components=[MultiLevelSingleColumnCreator()], setup=True)
    unsqueezed = sim.get_population(["some_attribute"])
    squeezed = sim.get_population("some_attribute")
    assert_squeezing_multi_level_single_outer_single_inner(
        unsqueezed, squeezed, ("some_attribute", "some_column")
    )

    # Multi-level, single outer, multiple inner -> inner dataframe
    sim = InteractiveContext(components=[MultiLevelMultiColumnCreator()], setup=True)
    sim._population._attribute_pipelines.pop("some_other_attribute")
    unsqueezed = sim.get_population(["some_attribute"])
    squeezed = sim.get_population("some_attribute")
    assert_squeezing_multi_level_single_outer_multi_inner(unsqueezed, squeezed)

    # Multi-level, multiple outer -> full unsqueezed multi-level dataframe
    sim = InteractiveContext(components=[MultiLevelMultiColumnCreator()], setup=True)
    # There's no way to request a squeezed dataframe here.
    df = sim.get_population(["some_attribute", "some_other_attribute"])
    assert isinstance(df, pd.DataFrame)
    assert isinstance(df.columns, pd.MultiIndex)


class TestGetPopulationNestedAttributes:
    """Tests query behavior with nested attribute calls.

    These tests leverage the NestedAttributeCreator component which registers an
    "outer" attribute pipieline whose source method calls another attribute
    "foo". Note that "outer" is by definition never a simple pipeline
    because its source is not a list of columns.
    """

    #########
    # Tests #
    #########

    def test_no_tracked_query(self) -> None:
        """Without a tracked query, all inner values {0, 1, 2} are returned."""
        sim = self._create_sim(NestedAttributeCreator)
        assert set(sim.get_population("inner")) == {0, 1, 2}
        sim._population.register_tracked_query("inner == 1")
        assert all(sim.get_population("inner") == 1)

    @pytest.mark.parametrize("include_untracked", [None, True, False])
    def test_tracked_queries_not_reapplied(
        self,
        include_untracked: bool | None,
        mocker: MockerFixture,
    ) -> None:
        """Tracked queries are not re-applied inside nested pipeline calls."""
        sim = self._create_sim(
            NestedAttributeCreator,
            tracked_query="inner == 1",
            mocker=mocker,
        )
        self._assert_nested_query_suppression(
            sim,
            include_untracked,
            mocker,
            expected_filtered_inner={1},
        )

    @pytest.mark.parametrize("include_untracked", [None, True, False])
    def test_explicit_queries_preserved(
        self,
        include_untracked: bool | None,
        mocker: MockerFixture,
    ) -> None:
        """Check that explicit queries inside nested pipeline calls are preserved.

        This modifies outer's source to call 'inner' with different queries.
        Those explicit queries must still work even when tracked queries are suppressed
        during pipeline evaluation.
        """

        def outer_source(self_: NestedAttributeCreator, idx: pd.Index[int]) -> pd.DataFrame:
            ones = self_.population_view.get(idx, "inner", query="inner == 1")
            not_ones = self_.population_view.get(idx, "inner", query="inner != 1")
            combined = pd.concat([ones, not_ones]).sort_index()
            return pd.DataFrame({"doubled_inner": combined * 2})

        sim = self._create_sim(
            NestedAttributeCreator,
            outer_source_override=outer_source,
            tracked_query="inner != 0",
            mocker=mocker,
        )
        self._assert_nested_query_suppression(
            sim,
            include_untracked,
            mocker,
            expected_filtered_inner={1, 2},
            outer_column=("outer", "doubled_inner"),
            expected_baseline_outer={0, 2, 4},
            expected_filtered_outer={2, 4},
        )

    def test_explicit_false_inside_nested_call(self, mocker: MockerFixture) -> None:
        """Check explicit include_untracked=False inside a pipeline source.

        This should force the tracked query to be re-applied even at depth > 0."""

        def outer_source(self_: NestedAttributeCreator, idx: pd.Index[int]) -> pd.DataFrame:
            # include_untracked=False at depth > 0: tracked query IS applied
            tracked = self_.population_view.get(idx, "inner", include_untracked=False)
            # include_untracked=True at depth > 0: tracked query NOT applied
            all_inner = self_.population_view.get(idx, "inner", include_untracked=True)
            return pd.DataFrame(
                {"tracked_count": len(tracked), "all_count": len(all_inner)}, index=idx
            )

        sim = self._create_sim(
            NestedAttributeCreator,
            outer_source_override=outer_source,
            tracked_query="inner == 1",
            mocker=mocker,
        )
        max_depth = self._patch_depth_tracking(sim, mocker)

        # Use include_untracked=True at top level so we see all simulants
        pop = sim.get_population(["outer", "inner"], include_untracked=True)
        total = len(pop)
        tracked_count = pop[("outer", "tracked_count")].iloc[0]
        all_count = pop[("outer", "all_count")].iloc[0]

        # True inside the nested source should have returned all simulants
        assert all_count == total
        # False inside the nested source should have applied the tracked query
        assert tracked_count == pop["inner"].eq(1).sum()

        self._assert_depth(sim, max_depth, 2)

    @pytest.mark.parametrize("include_untracked", [None, True, False])
    def test_lookup_table_nested_path(
        self,
        include_untracked: bool | None,
        mocker: MockerFixture,
    ) -> None:
        """Lookup table inside a nested pipeline call suppresses tracked queries.

        Uses NestedLookupCaller whose outer_source calls a lookup table keyed
        on 'inner'. The table internally calls get(index, ["inner"])
        with the default include_untracked=None, exercising the lookup path.
        """
        sim = self._create_sim(
            NestedLookupCaller,
            tracked_query="inner == 1",
            mocker=mocker,
        )
        self._assert_nested_query_suppression(
            sim,
            include_untracked,
            mocker,
            expected_filtered_inner={1},
            outer_column=("outer", "lookup_value"),
            expected_baseline_outer={10, 20, 30},
            expected_filtered_outer={20},
        )

    ##################
    # Helper methods #
    ##################

    def _assert_nested_query_suppression(
        self,
        sim: InteractiveContext,
        include_untracked: bool | None,
        mocker: MockerFixture,
        expected_filtered_inner: set[int],
        outer_column: str | tuple[str, str] | None = None,
        expected_baseline_outer: set[int] | None = None,
        expected_filtered_outer: set[int] | None = None,
        expected_depth: int = 2,
    ) -> None:
        """Common assertion pattern for nested query suppression tests.

        Verifies that:
        1. With a tracked query, inner values are filtered appropriately
           (unless include_untracked is True, in which case all are returned).
        2. Pipeline evaluation depth reaches the expected level.
        """
        kwargs: dict[str, bool] = {}
        if include_untracked is not None:
            kwargs["include_untracked"] = include_untracked

        max_depth = self._patch_depth_tracking(sim, mocker)
        pop = sim.get_population(sim.get_attribute_names(), **kwargs)  # type: ignore[call-overload]
        if include_untracked is True:
            assert set(pop["inner"]) == {0, 1, 2}
            if outer_column is not None:
                assert set(pop[outer_column]) == expected_baseline_outer
        else:
            assert set(pop["inner"]) == expected_filtered_inner
            if outer_column is not None:
                assert set(pop[outer_column]) == expected_filtered_outer

        self._assert_depth(sim, max_depth, expected_depth)

    @staticmethod
    def _create_sim(
        component_class: type[NestedAttributeCreator],
        outer_source_override: Callable[..., pd.DataFrame] | None = None,
        tracked_query: str | None = None,
        mocker: MockerFixture | None = None,
    ) -> InteractiveContext:
        """Create a sim with a nested pipeline component and verify simplicity."""

        class _Component(component_class):  # type: ignore[valid-type, misc]
            def setup(self, builder: Builder) -> None:
                super().setup(builder)
                # Registering a modifier make the pipeline non-simple
                builder.value.register_attribute_modifier(
                    "inner",
                    lambda index, series: series,
                )

        if outer_source_override is not None:
            setattr(_Component, "outer_source", outer_source_override)

        sim = InteractiveContext(components=[_Component()])
        assert not sim._population._attribute_pipelines["outer"].is_simple
        assert sim._population._attribute_pipelines["inner"].is_simple == False

        if tracked_query is not None:
            assert mocker is not None
            pop_mgr = sim._population
            pop_mgr.register_tracked_query(tracked_query)
            # Change lifecycle state so that tracked query doesn't get skipped
            mocker.patch.object(pop_mgr, "get_current_state", lambda: "on_time_step")

        return sim

    @staticmethod
    def _patch_depth_tracking(sim: InteractiveContext, mocker: MockerFixture) -> list[int]:
        """Patch ``_get_attributes`` to record max pipeline evaluation depth.

        Returns a single-element list (rather than an int which is immutable) so
        that mutations by the closure are visible to the caller after ``return``.
        """
        pop_mgr = sim._population
        max_depth: list[int] = [0]
        original = pop_mgr._get_attributes

        def _tracking_wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            # +1 because the depth counter reflects the current nesting level
            # before this _get_attributes call executes
            max_depth[0] = max(max_depth[0], pop_mgr.pipeline_evaluation_depth + 1)
            return original(*args, **kwargs)

        mocker.patch.object(pop_mgr, "_get_attributes", side_effect=_tracking_wrapper)
        return max_depth

    @staticmethod
    def _assert_depth(sim: InteractiveContext, max_depth: list[int], expected: int) -> None:
        """Assert max depth reached *expected* and counter has reset to 0."""
        assert max_depth[0] == expected
        assert sim._population.pipeline_evaluation_depth == 0


def test_init_signature_agrees_with_simulation_context() -> None:
    """Guard the parent's parameters, which are re-declared here rather than forwarded.

    ``InteractiveContext`` spells out ``SimulationContext``'s parameters instead of
    passing ``*args``/``**kwargs`` through, so a new parent parameter would otherwise
    be silently dropped.

    Covers signature shape only: names, order, annotations, defaults, and kinds.
    """
    parent = inspect.signature(SimulationContext.__init__).parameters
    child = inspect.signature(InteractiveContext.__init__).parameters

    child_only = ("self", "observe", "setup")
    inherited = [name for name in parent if name != "self"]
    assert [name for name in child if name not in child_only] == inherited

    for name in inherited:
        assert child[name].annotation == parent[name].annotation, name
        # Kind parity is what lets callers pass these positionally to
        # InteractiveContext, as test_positional_arguments_map_to_parameters does.
        assert child[name].kind == parent[name].kind, name
        if name == "logging_verbosity":
            # Deliberately quieter than the parent; see the class docstring.
            assert child[name].default == 0 and parent[name].default == 1
        else:
            assert child[name].default == parent[name].default, name


def test_positional_arguments_map_to_parameters() -> None:
    """Exercise the call the signature guard never makes.

    ``configuration`` and ``plugin_configuration`` share a type, so transposing them
    would type-check and pass the signature guard. Verbosity stays 0 here: logging is
    configured in ``__init__``, first-writer-wins per process, so a higher value
    would leak to every later test.
    """
    sim = InteractiveContext(
        None,
        None,
        {"population": {"population_size": 123}},
        None,
        "positional_arguments",
        0,
        setup=False,
    )

    assert sim.name == "positional_arguments"
    assert sim.configuration.population.population_size == 123


@pytest.mark.parametrize("parameter", ["setup", "observe"])
def test_args_are_keyword_only(parameter: str) -> None:
    """keyword-only args are not parent parameters so must not take that slot."""
    param = inspect.signature(InteractiveContext.__init__).parameters[parameter]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_keyword_only_parameters_reject_a_positional_call() -> None:
    """The runtime half of the guard above.

    The parent contributes six positional parameters, so a seventh argument has
    nowhere to bind. Were either keyword-only parameter to drift in front of the
    ``*``, it would quietly accept that seventh instead of raising.
    """
    with pytest.raises(TypeError):
        # Deliberately invalid: mypy catches this statically, and the ignore lets
        # us also pin the runtime behaviour.
        InteractiveContext(None, None, None, None, None, 0, False)  # type: ignore[call-arg]


class TestResultsGathering:
    """An InteractiveContext does not gather results unless it is asked to.

    Gathering is switched off by leaving the results manager's per-step listeners
    unregistered. Observers stay registered, so the state table an interactive run
    produces is the same one a gathering run produces.
    """

    def test_observe_defaults_to_off(self) -> None:
        """Constructed without the flag, so a change to the default fails here.

        Named for the parameter rather than "by default" because the manager's
        own ``to_observe`` defaults the other way, for non-interactive runs.
        """
        sim = InteractiveContext(
            configuration=HARRY_POTTER_CONFIG,
            components=[Hogwarts(), HousePointsObserver(), HogwartsResultsStratifier()],
        )
        sim.step()

        # No frames are allocated, so there are no zeros to mistake for
        # measurements of nothing.
        assert sim._results._raw_results == {}
        assert sim.get_results() == {}

    def test_empty_results_say_why(self, caplog: LogCaptureFixture) -> None:
        hogwarts(observe=False).get_results()
        assert "pass observe=True" in caplog.text

    def test_no_warning_when_observing_was_requested(self, caplog: LogCaptureFixture) -> None:
        """Empty results with observing on must stay quiet.

        Built without an observer so the results really are empty. The shared
        fixture would make them non-empty, which satisfies the guard on its own
        and never exercises the flag.
        """
        sim = InteractiveContext(
            configuration=HARRY_POTTER_CONFIG,
            components=[Hogwarts()],
            observe=True,
        )
        # Confirm that results are "empty" (not just all-zero)
        assert sim.get_results() == {}
        # Despite empty results, confirm that we do not log the observe warning
        assert "pass observe=True" not in caplog.text

    def test_observed_when_requested(self) -> None:
        sim = hogwarts(observe=True)
        sim.step()
        assert sim.get_results()["house_points"][VALUE_COLUMN].sum() > 0

    def test_observers_are_still_registered(self) -> None:
        """The point of gating the manager rather than filtering by type."""
        sim = hogwarts(observe=False)
        observers = [
            c.name for c in sim._component_manager._components if isinstance(c, Observer)
        ]
        assert observers == ["house_points_observer"]

    @staticmethod
    def _gather_listeners(sim: InteractiveContext) -> int:
        """Count the results manager's per-step gathering listeners.

        Excludes its post-setup listener, which is registered either way.
        """
        return sum(
            1
            for channel in sim._events._event_types.values()
            for priority in channel.listeners
            for listener in priority
            if getattr(listener, "__self__", None) is sim._results
            and listener.__name__ != "on_post_setup"
        )

    def test_whether_listeners_are_registered(self) -> None:
        """The results manager registers at every priority in four phases."""
        assert self._gather_listeners(hogwarts(observe=False)) == 0
        assert self._gather_listeners(hogwarts(observe=True)) > 0

    def test_state_table_is_unchanged(self, disease_model_spec: Path) -> None:
        """Observers are still registered and can manage columns.

        Excluding an observer would take its own columns with it - the
        ``disease_model`` example's ``DeathsObserver`` owns ``previous_alive`` -
        and an InteractiveContext exists to inspect the state table.
        """

        def run(observe: bool) -> pd.DataFrame:
            sim = InteractiveContext(str(disease_model_spec), observe=observe)
            sim.take_steps(5)
            return sim.get_population(sim.get_attribute_names())

        on, off = run(observe=True), run(observe=False)
        assert "previous_alive" in off.columns
        assert sorted(on.columns) == sorted(off.columns)
        for column in on.columns:
            assert on[column].equals(off[column]), column

    def test_observations_from_a_non_observer_are_not_gathered(self) -> None:
        """Even non-Observers are technically allowed to register observations."""

        class SneakyComponent(Component):
            def setup(self, builder: Builder) -> None:
                builder.results.register_adding_observation(
                    name="sneaky",
                    requires_attributes=["house_points"],
                    aggregator_sources=["house_points"],
                    aggregator=lambda df: df.sum(),
                    excluded_stratifications=["student_house", "power_level_group"],
                )

        def run(observe: bool) -> dict[str, pd.DataFrame]:
            sim = InteractiveContext(
                configuration=HARRY_POTTER_CONFIG,
                components=[Hogwarts(), SneakyComponent()],
                observe=observe,
            )
            sim.step()
            return sim.get_results()

        # Confirm that the sneaky observer did successfully register an observation
        assert run(observe=True)["sneaky"][VALUE_COLUMN].sum() > 0
        # Now confirm that the sneaky observer's observation is not gathered
        assert run(observe=False) == {}
