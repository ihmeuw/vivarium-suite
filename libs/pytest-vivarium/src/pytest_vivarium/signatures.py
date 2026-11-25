"""Check a hand-written signature against the definition it mirrors.

A wrapper, a subclass, or a public interface method often spells out the
parameters of the callable it forwards to instead of passing ``*args`` and
``**kwargs`` through. Nothing then keeps the two in step: a parameter added to
the source is silently unreachable through the copy. These helpers compare the
two signatures parameter by parameter so a test can pin them together.
"""

from __future__ import annotations

import inspect
import re
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

SignatureAspect = Literal["names", "order", "kinds", "annotations", "defaults"]
"""An aspect of a signature that two signatures can disagree on."""


@dataclass(frozen=True)
class SignatureDivergence:
    """One way a declared signature differs from its source, for one parameter."""

    aspect: SignatureAspect
    """The aspect that differs."""
    parameter: str
    """The name of the parameter that differs."""
    detail: str
    """A human-readable description of the difference."""


def compare_signatures(
    declared: Callable[..., Any],
    source: Callable[..., Any],
    *,
    ignore: Collection[str] = ("self",),
) -> list[SignatureDivergence]:
    """Compare the signature of ``declared`` with the signature of ``source``.

    Parameters named in ``ignore`` are removed from both signatures before any
    comparison, so a parameter that only one side has on purpose (for example,
    an argument that a subclass adds) does not count as a divergence. Each
    remaining difference is reported as one divergence per parameter:

    - ``names``: the parameter is in only one of the two signatures.
    - ``order``: the parameter is in both signatures, but its position among
      the parameters they share differs.
    - ``kinds``: the parameter kind (positional-only, keyword-only, and so on)
      differs.
    - ``annotations``: the annotations differ. String annotations are resolved
      where possible, so ``"int"`` and ``int`` agree. When either side cannot be
      resolved (for example, a name imported only under ``TYPE_CHECKING``), the
      two compare as text with module qualifiers removed.
    - ``defaults``: the default values differ. Two defaults agree when they are
      the same object or compare equal.

    Return annotations are not compared.

    Parameters
    ----------
    declared
        The callable whose signature was written to mirror ``source``.
    source
        The callable that ``declared`` mirrors.
    ignore
        Names of parameters to leave out of every comparison.

    Returns
    -------
        The divergences, in the parameter order of ``declared`` followed by any
        parameters only ``source`` has. The list is empty when the signatures
        agree.
    """
    declared_params = _parameters(declared, ignore)
    source_params = _parameters(source, ignore)
    declared_globals = _globals_of(declared)
    source_globals = _globals_of(source)

    shared_in_declared = [name for name in declared_params if name in source_params]
    shared_in_source = [name for name in source_params if name in declared_params]

    divergences: list[SignatureDivergence] = []
    for name, declared_param in declared_params.items():
        if name not in source_params:
            divergences.append(
                SignatureDivergence("names", name, "only the declared signature has it")
            )
            continue
        source_param = source_params[name]

        declared_index = shared_in_declared.index(name)
        source_index = shared_in_source.index(name)
        if declared_index != source_index:
            divergences.append(
                SignatureDivergence(
                    "order",
                    name,
                    f"position {declared_index} among shared parameters in the declared "
                    f"signature, {source_index} in the source",
                )
            )

        if declared_param.kind != source_param.kind:
            divergences.append(
                SignatureDivergence(
                    "kinds",
                    name,
                    f"declared {declared_param.kind.description}, "
                    f"source {source_param.kind.description}",
                )
            )

        annotation_divergence = _compare_annotations(
            declared_param.annotation,
            declared_globals,
            source_param.annotation,
            source_globals,
        )
        if annotation_divergence is not None:
            divergences.append(
                SignatureDivergence("annotations", name, annotation_divergence)
            )

        if not _defaults_agree(declared_param.default, source_param.default):
            divergences.append(
                SignatureDivergence(
                    "defaults",
                    name,
                    f"declared {_format_default(declared_param.default)}, "
                    f"source {_format_default(source_param.default)}",
                )
            )

    for name in source_params:
        if name not in declared_params:
            divergences.append(
                SignatureDivergence("names", name, "only the source signature has it")
            )
    return divergences


def assert_signatures_agree(
    declared: Callable[..., Any],
    source: Callable[..., Any],
    *,
    ignore: Collection[str] = ("self",),
    allowed: Mapping[tuple[SignatureAspect, str], str] | None = None,
) -> None:
    """Assert that ``declared`` mirrors ``source``, apart from declared exceptions.

    Each key of ``allowed`` is an ``(aspect, parameter)`` pair that is allowed to
    diverge, and its value is the reason the divergence is intentional. The
    assertion fails when there is a divergence that ``allowed`` does not list,
    or when ``allowed`` lists a pair that no longer diverges. The failure
    message names every such divergence and every such stale allowance.

    Parameters
    ----------
    declared
        The callable whose signature was written to mirror ``source``.
    source
        The callable that ``declared`` mirrors.
    ignore
        Names of parameters to leave out of every comparison.
    allowed
        The intentional divergences, each mapped to the reason for it.

    Raises
    ------
    ValueError
        If a reason in ``allowed`` is empty or only whitespace.
    AssertionError
        If there is an undeclared divergence or a stale allowance.
    """
    allowances = dict(allowed or {})
    for (aspect, parameter), reason in allowances.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"The allowance for ({aspect!r}, {parameter!r}) needs a non-empty reason."
            )

    divergences = compare_signatures(declared, source, ignore=ignore)
    undeclared = [d for d in divergences if (d.aspect, d.parameter) not in allowances]
    diverging_keys = {(d.aspect, d.parameter) for d in divergences}
    stale = [key for key in allowances if key not in diverging_keys]
    if not undeclared and not stale:
        return

    lines = [f"The signature of {_describe(declared)} does not mirror {_describe(source)}:"]
    if undeclared:
        lines.append("Undeclared divergences:")
        lines.extend(f"  - {d.aspect} of {d.parameter!r}: {d.detail}" for d in undeclared)
    if stale:
        lines.append("Stale allowances (these no longer diverge):")
        lines.extend(f"  - {aspect} of {parameter!r}" for aspect, parameter in stale)
    raise AssertionError("\n".join(lines))


_MISSING = object()

_MODULE_QUALIFIER = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)+(?=[A-Za-z_])")


def _parameters(
    function: Callable[..., Any], ignore: Collection[str]
) -> dict[str, inspect.Parameter]:
    """Return the parameters of ``function`` by name, without the ignored ones."""
    return {
        name: parameter
        for name, parameter in inspect.signature(function).parameters.items()
        if name not in ignore
    }


def _globals_of(function: Callable[..., Any]) -> dict[str, Any]:
    """Return the global namespace that string annotations of ``function`` refer to."""
    unwrapped = inspect.unwrap(function)
    unwrapped = getattr(unwrapped, "__func__", unwrapped)
    namespace = getattr(unwrapped, "__globals__", None)
    if isinstance(namespace, dict):
        return namespace
    module = sys.modules.get(getattr(unwrapped, "__module__", None) or "")
    return vars(module) if module is not None else {}


def _resolve_annotation(annotation: Any, namespace: dict[str, Any]) -> Any:
    """Evaluate a string annotation in ``namespace``, or return ``_MISSING``."""
    if not isinstance(annotation, str):
        return annotation
    try:
        return eval(annotation, dict(namespace))
    except Exception:
        return _MISSING


def _annotation_text(annotation: Any, resolved: Any) -> str:
    """Return the annotation as text without module qualifiers."""
    if resolved is _MISSING:
        text = annotation
    elif resolved is inspect.Parameter.empty:
        text = "<no annotation>"
    else:
        text = inspect.formatannotation(resolved)
    return _MODULE_QUALIFIER.sub("", text)


def _compare_annotations(
    declared: Any,
    declared_namespace: dict[str, Any],
    source: Any,
    source_namespace: dict[str, Any],
) -> str | None:
    """Return a description of how two annotations differ, or None if they agree."""
    declared_resolved = _resolve_annotation(declared, declared_namespace)
    source_resolved = _resolve_annotation(source, source_namespace)
    declared_text = _annotation_text(declared, declared_resolved)
    source_text = _annotation_text(source, source_resolved)
    if declared_resolved is not _MISSING and source_resolved is not _MISSING:
        if _values_equal(declared_resolved, source_resolved):
            return None
        if declared_text == source_text:
            # Distinct objects that print alike are only told apart by qualified names.
            declared_text = inspect.formatannotation(declared_resolved)
            source_text = inspect.formatannotation(source_resolved)
        return f"declared {declared_text}, source {source_text}"
    if "".join(declared_text.split()) == "".join(source_text.split()):
        return None
    return f"declared {declared_text}, source {source_text}"


def _values_equal(first: Any, second: Any) -> bool:
    """Return whether two values are identical or compare equal as a plain truth value."""
    if first is second:
        return True
    try:
        result = first == second
    except Exception:
        return False
    if isinstance(result, bool):
        return result
    # Array-like results (numpy, pandas) do not say whether the values agree.
    if getattr(result, "ndim", 0) != 0:
        return False
    try:
        return bool(result)
    except Exception:
        return False


def _defaults_agree(declared: Any, source: Any) -> bool:
    """Return whether two parameter defaults agree."""
    return _values_equal(declared, source)


def _format_default(default: Any) -> str:
    """Return a default value as text for a divergence detail."""
    if default is inspect.Parameter.empty:
        return "no default"
    return f"default {default!r}"


def _describe(function: Callable[..., Any]) -> str:
    """Return a readable name for ``function``."""
    module = getattr(function, "__module__", None)
    qualname = getattr(function, "__qualname__", None) or repr(function)
    return f"{module}.{qualname}" if module else qualname
