"""Guard the Builder interface methods that re-declare a manager method's signature.

A pass-through interface method spells out the parameters of the manager method
it forwards to, so a parameter added to the manager would otherwise be silently
unreachable from the public API.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import pytest
from pytest_vivarium.signatures import SignatureAspect, assert_signatures_agree

from tests.framework.pass_throughs import (
    PassThrough,
    find_framework_pass_throughs,
    find_pass_throughs,
)
from vivarium.engine.framework.components.manager import ComponentManager
from vivarium.engine.framework.randomness.manager import RandomnessManager
from vivarium.engine.framework.results.interface import ResultsInterface
from vivarium.engine.framework.time.manager import SimulationClock
from vivarium.engine.manager import Interface

_PUBLIC_DEFAULTS = (
    "The public interface supplies a default for a parameter that the internal "
    "manager method requires."
)

KNOWN_DIVERGENCES: dict[str, dict[tuple[SignatureAspect, str], str]] = {
    "ResultsInterface.register_stratification": {
        ("defaults", "excluded_categories"): _PUBLIC_DEFAULTS,
        ("defaults", "mapper"): _PUBLIC_DEFAULTS,
        ("defaults", "is_vectorized"): _PUBLIC_DEFAULTS,
    },
    "ResultsInterface.register_binned_stratification": {
        ("defaults", "bin_edges"): _PUBLIC_DEFAULTS,
        ("defaults", "labels"): _PUBLIC_DEFAULTS,
        ("defaults", "excluded_categories"): _PUBLIC_DEFAULTS,
    },
}
"""Intentional divergences, keyed by ``PassThrough.qualname``, each with its reason."""


class _WidgetManager:
    """A synthetic manager for the discovery tests."""

    def make_widget(self, name: str, size: int = 1) -> str:
        return name * size

    def create(self, name: str) -> str:
        return name

    def collect(self, name: str, *args: Any, **kwargs: Any) -> str:
        return name

    def notify(self, message: str) -> None:
        pass

    def describe(self, name: str, verbose: bool = False) -> str:
        return name

    def pair(self, first: str, second: str) -> str:
        return first + second

    def tag(self, name: str, *, label: str) -> str:
        return label + name


class _InstanceOnlyManager:
    """A synthetic manager whose target exists only on its instances."""

    def __init__(self) -> None:
        self.on_instance = partial(self._on_instance, prefix="widget")

    def _on_instance(self, name: str, prefix: str) -> str:
        return prefix + name


class _SameNameInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def make_widget(self, name: str, size: int = 1) -> str:
        return self._manager.make_widget(name, size)


class _RenamedInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def build_widget(self, name: str) -> str:
        return self._manager.create(name)


class _KeywordAndVariadicInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def make_widget(self, name: str, size: int = 1) -> str:
        return self._manager.make_widget(name=name, size=size)

    def collect(self, name: str, *args: Any, **kwargs: Any) -> str:
        return self._manager.collect(name, *args, **kwargs)

    def pair(self, first: str, second: str) -> str:
        return self._manager.pair(second=second, first=first)

    def join(self, first: str, second: str) -> str:
        return self._manager.pair(first, second=second)


class _OutOfOrderInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def pair(self, first: str, second: str) -> str:
        return self._manager.pair(second, first)


class _LaterPositionalFirstInterface(Interface):
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def pair(self, first: str, second: str) -> Any:
        return self._manager.pair(second, first=first)


class _KeywordOnlyPositionallyInterface(Interface):
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def tag(self, name: str, *, label: str) -> Any:
        return self._manager.tag(name, label)


class _DocstringInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def notify(self, message: str) -> None:
        """Send a message to the manager."""
        self._manager.notify(message)


class _AddsArgumentInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager
        self._verbose = True

    def describe(self, name: str) -> str:
        return self._manager.describe(name, True)

    def describe_by_keyword(self, name: str) -> str:
        return self._manager.describe(name, verbose=self._verbose)


class _DropsParameterInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def make_widget(self, name: str, size: int = 1) -> str:
        return self._manager.make_widget(name)


class _MultiStatementInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager
        self._calls = 0

    def make_widget(self, name: str, size: int = 1) -> str:
        widget = self._manager.make_widget(name, size)
        return widget

    def build_widget(self, name: str) -> str:
        self._calls += 1
        return self._manager.create(name)


class _ReturnsWithoutCallInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def get_widget_maker(self) -> Any:
        return self._manager.make_widget

    def get_lazy_widget(self, name: str) -> Any:
        return lambda: self._manager.create(name)


class _BaseWidgetInterface(Interface):
    def __init__(self, manager: _WidgetManager) -> None:
        self._manager = manager

    def make_widget(self, name: str, size: int = 1) -> str:
        return self._manager.make_widget(name, size)


class _DerivedWidgetInterface(_BaseWidgetInterface):
    def __call__(self, name: str) -> str:
        return self._manager.create(name)

    def build_widget(self, name: str) -> str:
        return self._manager.create(name)


class _InstanceOnlyInterface(Interface):
    def __init__(self, manager: _InstanceOnlyManager) -> None:
        self._manager = manager

    def on_instance(self, name: str) -> str:
        return self._manager.on_instance(name)


def _by_method_name(pass_throughs: list[PassThrough]) -> dict[str, PassThrough]:
    return {pass_through.method_name: pass_through for pass_through in pass_throughs}


@pytest.fixture(scope="module")
def framework_pass_throughs() -> dict[str, PassThrough]:
    return {
        pass_through.qualname: pass_through for pass_through in find_framework_pass_throughs()
    }


#######################
# find_pass_throughs  #
#######################


def test_finds_pass_through_with_same_target_name() -> None:
    """A method that forwards all its parameters to the same-named manager method is found."""
    [pass_through] = find_pass_throughs(_SameNameInterface, _WidgetManager)

    assert pass_through.interface is _SameNameInterface
    assert pass_through.method_name == "make_widget"
    assert pass_through.manager is _WidgetManager
    assert pass_through.target_name == "make_widget"
    assert pass_through.interface_method is _SameNameInterface.make_widget
    assert pass_through.manager_method is _WidgetManager.make_widget


def test_finds_pass_through_with_different_target_name() -> None:
    """A method that forwards to a manager method with another name is paired with that name."""
    [pass_through] = find_pass_throughs(_RenamedInterface, _WidgetManager)

    assert pass_through.method_name == "build_widget"
    assert pass_through.target_name == "create"
    assert pass_through.interface_method is _RenamedInterface.build_widget
    assert pass_through.manager_method is _WidgetManager.create


def test_finds_pass_through_forwarding_by_keyword_and_variadics() -> None:
    """Forwarding as ``name=name``, ``*args``, and ``**kwargs`` still counts as a pass-through."""
    found = find_pass_throughs(_KeywordAndVariadicInterface, _WidgetManager)

    assert [pass_through.method_name for pass_through in found] == [
        "make_widget",
        "collect",
        "pair",
        "join",
    ]
    by_name = _by_method_name(found)
    assert by_name["make_widget"].target_name == "make_widget"
    assert by_name["collect"].target_name == "collect"
    assert by_name["pair"].target_name == "pair"
    assert by_name["join"].target_name == "pair"


def test_finds_pass_through_with_docstring_and_no_return() -> None:
    """A docstring before the call, and a bare call without ``return``, still count."""
    [pass_through] = find_pass_throughs(_DocstringInterface, _WidgetManager)

    assert pass_through.method_name == "notify"
    assert pass_through.target_name == "notify"


def test_method_that_adds_an_argument_is_not_a_pass_through() -> None:
    """A call that passes a value that is not one of the method's parameters is skipped."""
    assert find_pass_throughs(_AddsArgumentInterface, _WidgetManager) == []


def test_method_that_drops_a_parameter_is_not_a_pass_through() -> None:
    """A call that does not forward every parameter of the method is skipped."""
    assert find_pass_throughs(_DropsParameterInterface, _WidgetManager) == []


def test_method_forwarding_positionals_out_of_order_is_not_a_pass_through() -> None:
    """A call that passes the method's positional parameters in a different order is skipped."""
    assert find_pass_throughs(_OutOfOrderInterface, _WidgetManager) == []


def test_method_forwarding_a_later_positional_first_is_not_a_pass_through() -> None:
    """A call whose positional arguments skip the method's leading parameters is skipped."""
    assert find_pass_throughs(_LaterPositionalFirstInterface, _WidgetManager) == []


def test_method_passing_keyword_only_positionally_is_not_a_pass_through() -> None:
    """A call that passes a keyword-only parameter positionally is skipped."""
    assert find_pass_throughs(_KeywordOnlyPositionallyInterface, _WidgetManager) == []


def test_method_with_more_than_one_statement_is_not_a_pass_through() -> None:
    """A body with any statement besides the forwarding call is skipped."""
    assert find_pass_throughs(_MultiStatementInterface, _WidgetManager) == []


def test_returning_bound_method_or_lambda_is_not_a_pass_through() -> None:
    """Returning ``self._manager.x`` or a lambda over the manager, without a call, is skipped."""
    assert find_pass_throughs(_ReturnsWithoutCallInterface, _WidgetManager) == []


def test_dunder_and_inherited_methods_are_not_reported() -> None:
    """Dunder methods and methods inherited from a base interface are not reported."""
    found = find_pass_throughs(_DerivedWidgetInterface, _WidgetManager)

    assert [pass_through.method_name for pass_through in found] == ["build_widget"]
    assert all(pass_through.interface is _DerivedWidgetInterface for pass_through in found)


def test_manager_method_is_none_for_instance_only_target() -> None:
    """A target that the manager only assigns on an instance has ``manager_method`` None."""
    [pass_through] = find_pass_throughs(_InstanceOnlyInterface, _InstanceOnlyManager)

    assert pass_through.target_name == "on_instance"
    assert pass_through.manager_method is None


def test_qualname_names_interface_and_method() -> None:
    """``qualname`` is ``"<interface class name>.<method name>"``."""
    pass_through = PassThrough(
        interface=_RenamedInterface,
        method_name="build_widget",
        manager=_WidgetManager,
        target_name="create",
    )

    assert pass_through.qualname == "_RenamedInterface.build_widget"
    [found] = find_pass_throughs(_RenamedInterface, _WidgetManager)
    assert found.qualname == "_RenamedInterface.build_widget"


#################################
# find_framework_pass_throughs  #
#################################


def test_framework_scan_finds_known_pass_throughs(
    framework_pass_throughs: dict[str, PassThrough],
) -> None:
    """The scan finds representative pass-throughs, including one whose target name differs."""
    get_stream = framework_pass_throughs.get("RandomnessInterface.get_stream")
    assert get_stream is not None, "RandomnessInterface.get_stream was not found"
    assert get_stream.manager is RandomnessManager
    assert get_stream.target_name == "get_randomness_stream"
    assert get_stream.manager_method is RandomnessManager.get_randomness_stream

    get_component = framework_pass_throughs.get("ComponentInterface.get_component")
    assert get_component is not None, "ComponentInterface.get_component was not found"
    assert get_component.manager is ComponentManager
    assert get_component.target_name == "get_component"
    assert get_component.manager_method is ComponentManager.get_component


def test_framework_scan_excludes_observation_adapters(
    framework_pass_throughs: dict[str, PassThrough],
) -> None:
    """The results observation adapters, which add arguments, are not reported."""
    assert "ResultsInterface.register_stratification" in framework_pass_throughs
    adapters = {
        "ResultsInterface.register_adding_observation",
        "ResultsInterface.register_stratified_observation",
        "ResultsInterface.register_unstratified_observation",
        "ResultsInterface.register_concatenating_observation",
    }
    missing = sorted(
        name for name in adapters if name.split(".")[1] not in vars(ResultsInterface)
    )
    assert not missing, f"ResultsInterface no longer defines {missing}"
    assert adapters.isdisjoint(framework_pass_throughs)


def test_step_size_modifier_pairs_with_clock_method(
    framework_pass_throughs: dict[str, PassThrough],
) -> None:
    """``TimeInterface.register_step_size_modifier`` pairs with a real ``SimulationClock.register_step_modifier``."""
    pass_through = framework_pass_throughs.get("TimeInterface.register_step_size_modifier")
    assert pass_through is not None, "TimeInterface.register_step_size_modifier was not found"
    assert pass_through.manager is SimulationClock
    assert pass_through.target_name == "register_step_modifier"

    clock_method = vars(SimulationClock).get("register_step_modifier")
    assert callable(clock_method), "SimulationClock defines no register_step_modifier method"
    assert pass_through.manager_method is clock_method


def test_every_known_divergence_names_a_discovered_pass_through(
    framework_pass_throughs: dict[str, PassThrough],
) -> None:
    """Each ``KNOWN_DIVERGENCES`` key is the qualname of a discovered pass-through."""
    unknown = sorted(set(KNOWN_DIVERGENCES) - set(framework_pass_throughs))
    assert not unknown, f"KNOWN_DIVERGENCES names no discovered pass-through: {unknown}"


##############################
# End-to-end signature guard #
##############################


def test_every_pass_through_agrees_with_its_manager_method() -> None:
    """Every framework pass-through resolves to a manager method and agrees with it, apart from ``KNOWN_DIVERGENCES``."""
    pass_throughs = find_framework_pass_throughs()
    assert pass_throughs, "The framework scan found no pass-throughs"

    failures: list[str] = []
    for pass_through in pass_throughs:
        manager_method = pass_through.manager_method
        if manager_method is None:
            failures.append(
                f"{pass_through.qualname}: {pass_through.manager.__name__} defines no method "
                f"named {pass_through.target_name!r} to compare against"
            )
            continue
        try:
            assert_signatures_agree(
                pass_through.interface_method,
                manager_method,
                allowed=KNOWN_DIVERGENCES.get(pass_through.qualname, {}),
            )
        except AssertionError as error:
            failures.append(f"{pass_through.qualname}: {error}")

    assert not failures, "\n\n".join(failures)
