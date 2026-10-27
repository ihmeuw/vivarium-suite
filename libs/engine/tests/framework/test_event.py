from __future__ import annotations

from typing import Any, TypedDict

import numpy as np
import pandas as pd
import pytest
from vivarium.interactive import Simulation

from vivarium.engine import Component
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.event import Event, EventManager
from vivarium.engine.framework.lifecycle import LifeCycleError, lifecycle_states


class EventData(TypedDict):
    name: str
    index: pd.Index[int]
    user_data: dict[str, str]
    time: pd.Timestamp
    step_size: int
    priority: int


_EVENT_INIT_TYPE = dict[str, EventData]


@pytest.fixture
def event_init() -> _EVENT_INIT_TYPE:
    return {
        "orig": {
            "name": "test_event",
            "index": pd.Index(range(10)),
            "user_data": {"key": "value"},
            "time": pd.Timestamp("1/1/2000"),
            "step_size": 12,
            "priority": 5,
        },
        "new_val": {
            "name": "test_event",
            "index": pd.Index(range(3)),
            "user_data": {"new_key": "new_value"},
            "time": pd.Timestamp.now(),
            "step_size": 30,
            "priority": 5,
        },
    }


def test_proper_access(event_init: _EVENT_INIT_TYPE) -> None:
    # Event attributes are meant to be read-only
    event_data = event_init["orig"]
    e1 = Event(**event_data)

    assert event_data["name"] == e1.name
    assert (event_data["index"] == e1.index).all()
    assert event_data["user_data"] == e1.user_data
    assert event_data["time"] == e1.time
    assert event_data["step_size"] == e1.step_size

    attribute_data = event_init["new_val"]
    for key, value in attribute_data.items():
        with pytest.raises(AttributeError) as _:
            setattr(e1, key, value)


def test_split_event(event_init: _EVENT_INIT_TYPE) -> None:
    event_data = event_init["orig"]
    e1 = Event(**event_data)

    new_idx = event_init["new_val"]["index"]
    e2 = e1.split(new_idx)

    assert e1.index is event_data["index"]
    assert e2.index is new_idx


def test_emission(event_init: _EVENT_INIT_TYPE) -> None:
    signal = [False]

    def listener(*_: Any, **__: Any) -> None:
        signal[0] = True

    manager = EventManager()
    manager.clock = lambda: pd.Timestamp(1990, 1, 1)
    manager.step_size = lambda: pd.Timedelta(30, unit="D")
    manager.add_constraint = lambda f, **kwargs: f
    emitter = manager.get_emitter(lifecycle_states.TIME_STEP)
    manager.register_listener(lifecycle_states.TIME_STEP, listener)
    emitter(event_init["orig"]["index"], None)

    assert signal[0]

    signal[0] = False

    emitter = manager.get_emitter(lifecycle_states.TIME_STEP_CLEANUP)
    emitter(event_init["new_val"]["index"], None)
    assert not signal[0]


def test_listener_priority(event_init: _EVENT_INIT_TYPE) -> None:
    signal = [False, False, False]

    def listener1(*_: Any, **__: Any) -> None:
        signal[0] = True
        assert not signal[1]
        assert not signal[2]

    def listener2(*_: Any, **__: Any) -> None:
        signal[1] = True
        assert signal[0]
        assert not signal[2]

    def listener3(*_: Any, **__: Any) -> None:
        signal[2] = True
        assert signal[0]
        assert signal[1]

    manager = EventManager()
    manager.clock = lambda: pd.Timestamp(1990, 1, 1)
    manager.step_size = lambda: pd.Timedelta(30, "D")
    manager.add_constraint = lambda f, **kwargs: f
    emitter = manager.get_emitter(lifecycle_states.TIME_STEP)
    manager.register_listener(lifecycle_states.TIME_STEP, listener1, priority=0)
    manager.register_listener(lifecycle_states.TIME_STEP, listener2)
    manager.register_listener(lifecycle_states.TIME_STEP, listener3, priority=9)

    emitter(event_init["orig"]["index"], None)
    assert np.all(signal)


def test_contains() -> None:
    event = lifecycle_states.TIME_STEP

    manager = EventManager()
    manager.add_constraint = lambda f, **kwargs: f
    assert event not in manager
    manager.get_emitter(event)
    assert event in manager


def test_list_events() -> None:
    manager = EventManager()
    manager.add_constraint = lambda f, **kwargs: f
    _ = manager.get_channel("event1")
    _ = manager.get_emitter(lifecycle_states.TIME_STEP)

    def event3_listener(*_: Any, **__: Any) -> None:
        pass

    manager.register_listener(lifecycle_states.TIME_STEP_CLEANUP, event3_listener)
    _ = manager.get_listeners("event4")
    assert manager.list_events() == [
        "event1",
        lifecycle_states.TIME_STEP,
        lifecycle_states.TIME_STEP_CLEANUP,
        "event4",
    ]


class TestEventNamesMustBeLifecycleStates:
    @pytest.fixture
    def manager(self) -> EventManager:
        manager = EventManager()
        manager.add_constraint = lambda f, **kwargs: f
        return manager

    def test_register_listener_rejects_an_unknown_name(self, manager: EventManager) -> None:
        with pytest.raises(
            LifeCycleError,
            match=(
                "'my_event' is not a life cycle state.*"
                "Use one of: initialization, setup, .*, report."
            ),
        ):
            manager.register_listener("my_event", lambda event: None)
        assert "my_event" not in manager

    def test_get_emitter_rejects_an_unknown_name(self, manager: EventManager) -> None:
        with pytest.raises(LifeCycleError, match="'my_event' is not a life cycle state"):
            manager.get_emitter("my_event")
        assert "my_event" not in manager

    @pytest.mark.parametrize("state", lifecycle_states.ALL_STATES)
    def test_a_lifecycle_state_is_accepted(self, manager: EventManager, state: str) -> None:
        manager.register_listener(state, lambda event: None)
        manager.get_emitter(state)
        assert state in manager

    def test_a_component_using_an_unknown_name_fails_during_setup(self) -> None:
        class CustomEventListener(Component):
            def setup(self, builder: Builder) -> None:
                builder.event.register_listener("my_event", lambda event: None)

        with pytest.raises(LifeCycleError, match="'my_event' is not a life cycle state"):
            Simulation(components=[CustomEventListener()])
