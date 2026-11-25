"""Tests for the signature agreement helpers."""

from collections.abc import Callable
from typing import Any

import pytest

from pytest_vivarium.signatures import (
    SignatureDivergence,
    assert_signatures_agree,
    compare_signatures,
)


class _Widget:
    """A module-level type for string annotations to resolve to."""


class _Twin:
    """A module-level type that shares its name with ``_OTHER_TWIN``."""


_OTHER_TWIN = type("_Twin", (), {})


def _pairs(divergences: list[SignatureDivergence]) -> list[tuple[str, str]]:
    return [(divergence.aspect, divergence.parameter) for divergence in divergences]


def _with_annotations(
    function: Callable[..., Any], annotations: dict[str, Any]
) -> Callable[..., Any]:
    function.__annotations__ = annotations
    return function


########################
# compare_signatures   #
########################


def test_identical_signatures_have_no_divergences() -> None:
    """Two callables with the same signature produce an empty list."""

    def declared(a: int, /, b: str = "x", *args: Any, c: float = 1.0, **kwargs: Any) -> None:
        pass

    def source(a: int, /, b: str = "x", *args: Any, c: float = 1.0, **kwargs: Any) -> None:
        pass

    assert compare_signatures(declared, source) == []


def test_different_default_reports_only_defaults() -> None:
    """A different default is reported as one "defaults" divergence for that parameter."""

    def declared(a: int, retries: int = 1) -> None:
        pass

    def source(a: int, retries: int = 2) -> None:
        pass

    divergences = compare_signatures(declared, source)

    assert _pairs(divergences) == [("defaults", "retries")]
    assert all(isinstance(divergence, SignatureDivergence) for divergence in divergences)


def test_different_annotation_reports_only_annotations() -> None:
    """A different annotation is reported as one "annotations" divergence for that parameter."""

    def declared(a: int, label: int) -> None:
        pass

    def source(a: int, label: str) -> None:
        pass

    assert _pairs(compare_signatures(declared, source)) == [("annotations", "label")]


def test_different_kind_reports_only_kinds() -> None:
    """A different parameter kind is reported as one "kinds" divergence for that parameter."""

    def declared(a: int, b: int) -> None:
        pass

    def source(a: int, *, b: int) -> None:
        pass

    assert _pairs(compare_signatures(declared, source)) == [("kinds", "b")]


def test_reordered_parameters_report_order_not_names() -> None:
    """The same names in a different order are reported as "order", never "names"."""

    def declared(first: int, second: int, third: int) -> None:
        pass

    def source(second: int, first: int, third: int) -> None:
        pass

    pairs = _pairs(compare_signatures(declared, source))

    assert set(pairs) == {("order", "first"), ("order", "second")}
    assert len(pairs) == 2


def test_missing_and_extra_parameters_report_names() -> None:
    """A parameter in only one signature is reported as "names" for that parameter, on either side."""

    def declared(shared: int, declared_only: int = 0) -> None:
        pass

    def source(shared: int, source_only: int = 0) -> None:
        pass

    pairs = _pairs(compare_signatures(declared, source))

    assert set(pairs) == {("names", "declared_only"), ("names", "source_only")}
    assert len(pairs) == 2


@pytest.mark.parametrize("extra_on", ["declared", "source"])
def test_ignored_parameters_are_excluded_from_every_aspect(extra_on: str) -> None:
    """A parameter named in ``ignore`` causes no divergence even when only one side has it."""

    def with_extra(a: int, *, extra: bool = False) -> None:
        pass

    def without_extra(a: int) -> None:
        pass

    declared: Callable[..., None]
    source: Callable[..., None]
    if extra_on == "declared":
        declared, source = with_extra, without_extra
    else:
        declared, source = without_extra, with_extra

    assert compare_signatures(declared, source, ignore=("extra",)) == []
    assert _pairs(compare_signatures(declared, source)) == [("names", "extra")]


def test_self_is_ignored_by_default() -> None:
    """A method and a plain function with the same parameters agree under the default ``ignore``."""

    class Holder:
        def method(self, a: int, b: str = "x") -> None:
            pass

    def function(a: int, b: str = "x") -> None:
        pass

    assert compare_signatures(Holder.method, function) == []
    assert compare_signatures(function, Holder.method) == []


def test_string_annotation_matches_resolved_annotation() -> None:
    """A string annotation and the equivalent resolved object do not diverge."""

    def resolved(widget: _Widget) -> None:
        pass

    def as_text(widget: "_Widget") -> None:
        pass

    assert as_text.__annotations__["widget"] == "_Widget"
    assert compare_signatures(resolved, as_text) == []
    assert compare_signatures(as_text, resolved) == []


def test_unresolvable_annotation_compares_by_text() -> None:
    """An annotation that cannot be resolved compares by text without raising."""

    def declared(thing: Any) -> None:
        pass

    def same_text(thing: Any) -> None:
        pass

    def other_text(thing: Any) -> None:
        pass

    declared = _with_annotations(declared, {"thing": "_NoSuchType"})
    same_text = _with_annotations(same_text, {"thing": "_NoSuchType"})
    other_text = _with_annotations(other_text, {"thing": "_AnotherMissingType"})

    assert compare_signatures(declared, same_text) == []
    assert _pairs(compare_signatures(declared, other_text)) == [("annotations", "thing")]


def test_resolved_annotations_that_differ_diverge_despite_same_text() -> None:
    """Two resolvable annotations that are different objects diverge even when their unqualified text matches."""

    def declared(thing: _Twin) -> None:
        pass

    def source(thing: Any) -> None:
        pass

    source = _with_annotations(source, {"thing": _OTHER_TWIN, "return": None})

    assert _OTHER_TWIN.__name__ == _Twin.__name__
    assert _OTHER_TWIN is not _Twin
    assert _pairs(compare_signatures(declared, source)) == [("annotations", "thing")]


def test_return_annotation_is_not_compared() -> None:
    """Different return annotations produce no divergence."""

    def declared(a: int) -> int:
        return a

    def source(a: int) -> str:
        return str(a)

    assert compare_signatures(declared, source) == []


def test_divergences_follow_declared_parameter_order() -> None:
    """Divergences come in the declared parameter order, followed by parameters only the source has."""

    def declared(alpha: int, beta: int = 1, declared_only: int = 0) -> None:
        pass

    def source(source_only: int, alpha: str, beta: int = 2) -> None:
        pass

    assert _pairs(compare_signatures(declared, source)) == [
        ("annotations", "alpha"),
        ("defaults", "beta"),
        ("names", "declared_only"),
        ("names", "source_only"),
    ]


###########################
# assert_signatures_agree #
###########################


def _declared(alpha: int = 1, beta: int = 1) -> None:
    pass


def _source(alpha: int = 2, beta: int = 2) -> None:
    pass


def _source_matching_beta(alpha: int = 2, beta: int = 1) -> None:
    pass


def test_assert_passes_when_signatures_agree() -> None:
    """Agreeing signatures with no allowances pass."""

    def copy(alpha: int = 1, beta: int = 1) -> None:
        pass

    assert_signatures_agree(_declared, copy)
    assert_signatures_agree(_declared, copy, allowed={})


def test_assert_fails_on_undeclared_divergence() -> None:
    """An undeclared divergence fails, and the message names its aspect and parameter."""

    def declared(a: int, retries_count: int = 1) -> None:
        pass

    def source(a: int, retries_count: int = 2) -> None:
        pass

    with pytest.raises(AssertionError) as error:
        assert_signatures_agree(declared, source)

    assert "defaults" in str(error.value)
    assert "retries_count" in str(error.value)


def test_assert_passes_with_matching_allowance() -> None:
    """An allowed (aspect, parameter) divergence passes."""
    assert_signatures_agree(
        _declared,
        _source_matching_beta,
        allowed={("defaults", "alpha"): "The public API supplies a friendlier default."},
    )


def test_allowance_does_not_cover_other_parameters() -> None:
    """An allowance for one parameter does not excuse the same aspect on another parameter."""
    with pytest.raises(AssertionError) as error:
        assert_signatures_agree(
            _declared,
            _source,
            allowed={("defaults", "alpha"): "The public API supplies a friendlier default."},
        )

    assert "beta" in str(error.value)


def test_assert_fails_on_stale_allowance() -> None:
    """An allowance that matches no divergence fails, and the message names it."""

    def copy(alpha: int = 1, beta: int = 1) -> None:
        pass

    with pytest.raises(AssertionError) as error:
        assert_signatures_agree(
            _declared, copy, allowed={("defaults", "stale_gamma"): "No longer true."}
        )

    assert "stale_gamma" in str(error.value)


def test_assert_reports_every_problem_at_once() -> None:
    """One failure message lists every undeclared divergence and every stale allowance."""

    def declared(undeclared_first: int = 1, undeclared_second: int = 1) -> None:
        pass

    def source(undeclared_first: int = 2, undeclared_second: float = 1) -> None:
        pass

    with pytest.raises(AssertionError) as error:
        assert_signatures_agree(
            declared,
            source,
            allowed={
                ("defaults", "stale_first"): "No longer true.",
                ("kinds", "stale_second"): "No longer true.",
            },
        )

    message = str(error.value)
    for name in ("undeclared_first", "undeclared_second", "stale_first", "stale_second"):
        assert name in message


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_assert_rejects_empty_reason(reason: str) -> None:
    """An allowance whose reason is empty or only whitespace raises ValueError."""
    with pytest.raises(ValueError):
        assert_signatures_agree(
            _declared,
            _source_matching_beta,
            allowed={("defaults", "alpha"): reason},
        )
