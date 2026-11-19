"""Author a pipeline step as a plain function.

A step is a function that reads its declared inputs and returns a ``pandas``
DataFrame, or a dict of named DataFrames. :func:`python_step` compiles one such function
into a single backend :class:`~vivarium.dagger.backend.task.Task`, resolving the step's
inputs and its products through the shared
:class:`~vivarium.dagger.backend.wiring.FileRegistry`, so each input is the producer's own
node and pytask draws the producer-to-consumer edge from its path. Each input reaches the
target as its resolved file path, which the target reads itself; only the returned
DataFrames are autosaved, each to its product's path, in the format named by the product
key's suffix.

The task's ``run`` is a :class:`PythonStepRun`, a data-only object rather than a
closure, so the same step can be rebuilt in another process from the target's
import reference (:func:`target_reference`) and the product suffix or suffixes.

Code identity defaults to always-stale: a per-target code fingerprint is deferred,
so in this cut a step re-runs on every build.
"""

import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileKey, FileRegistry, WiringError
from vivarium.dagger.frontend.command_step import (
    _check_names,
    _check_paths,
    _check_shared_keys,
    _no_product,
    _step_name,
)

_PRODUCT_ARG = "product"
"""Parameter name under which the loaded product path reaches the run wrapper."""


def _save_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Write ``frame`` to ``path`` as Parquet."""
    frame.to_parquet(path)


def _save_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write ``frame`` to ``path`` as CSV without the index."""
    frame.to_csv(path, index=False)


_SAVERS: dict[str, Callable[[pd.DataFrame, Path], None]] = {
    ".parquet": _save_parquet,
    ".csv": _save_csv,
}
"""Supported product suffixes mapped to the DataFrame save they dispatch to."""


def _check_suffix(suffix: str, subject: str) -> None:
    """Raise ValueError naming ``subject`` if ``suffix`` has no save format."""
    if suffix not in _SAVERS:
        raise ValueError(
            f"{subject} has unsupported suffix {suffix!r}; supported suffixes are "
            f"{', '.join(sorted(_SAVERS))}."
        )


_MISSING_RETURN = " Check for a missing return statement."
"""The hint added to the message for a target that returned ``None``."""


@dataclass(frozen=True)
class PythonStepRun:
    """Call a step's target with its loaded inputs and save the frames it returns.

    The target returns one DataFrame, or a dict of them by product name. This is the
    ``run`` of every task :func:`python_step` builds. It holds only the target and the
    product suffix, or each product name's suffix, so another process can rebuild an equal
    run from :func:`target_reference` of the target and the suffixes.
    """

    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]]
    """The step's function. Any callable runs locally; running the step in another
    process needs :func:`target_reference` to succeed for it."""
    suffix: str | Mapping[str, str]
    """The product key's suffix, which names the save format (``.parquet`` or ``.csv``),
    for a step with one product under ``"product"``; or, for a step whose produces is a
    mapping, each product name mapped to its key's suffix."""

    def __post_init__(self) -> None:
        """Reject a suffix that has no save format, or an empty mapping; copy a mapping."""
        if isinstance(self.suffix, str):
            _check_suffix(self.suffix, "Product")
            return
        if not self.suffix:
            raise ValueError(
                "A python step run needs at least one product suffix, got an empty mapping."
            )
        for name, suffix in self.suffix.items():
            _check_suffix(suffix, f"Product {name!r}")
        # A copy, so that a later change to the caller's mapping cannot change the run.
        object.__setattr__(self, "suffix", dict(self.suffix))

    def __hash__(self) -> int:
        """Hash the target and the suffix, or the mapping's entries in any order."""
        if isinstance(self.suffix, str):
            return hash((self.target, self.suffix))
        return hash((self.target, frozenset(self.suffix.items())))

    def __call__(self, **kwargs: Any) -> None:
        """Call ``target`` and save what it returns at each product's path.

        With one product, ``kwargs["product"]`` is the product's path, and ``target``
        returns the DataFrame to save there. With a mapping of suffixes, each product name
        in it is a keyword argument that holds that product's path, and ``target`` returns
        a dict that maps each product name to its DataFrame. The type of the result, its
        names and each of its values are checked before anything is saved, and the frames
        are then saved in the mapping's order.

        Each product's parent directory is created before ``target`` is called. Every
        other keyword argument is passed to ``target`` unchanged.

        Raises
        ------
        TypeError
            With one product, if ``target`` returns something other than a DataFrame;
            with a mapping, if it returns something other than a dict, or a dict with a
            value that is not a DataFrame. The message names ``target`` and the type it
            returned, or each product name whose value has the wrong type and that
            value's type. For ``None`` it suggests a missing ``return``.
        ValueError
            With a mapping, if the keys of the returned dict are not exactly the product
            names. The message names ``target``, each missing product name, each extra
            key and the product names.
        """
        __tracebackhide__ = True
        if isinstance(self.suffix, str):
            product = Path(kwargs.pop(_PRODUCT_ARG))
            product.parent.mkdir(parents=True, exist_ok=True)
            result: object = self.target(**kwargs)
            if not isinstance(result, pd.DataFrame):
                if result is None:
                    hint = _MISSING_RETURN
                elif isinstance(result, Mapping):
                    hint = (
                        " To write several products, pass produces a mapping of product "
                        "names to file keys."
                    )
                else:
                    hint = ""
                raise TypeError(
                    f"Python step target {_label(self.target)} returned "
                    f"{type(result).__name__}; a python step must return a pandas "
                    f"DataFrame.{hint}"
                )
            _SAVERS[self.suffix](result, product)
            return
        products = {name: Path(kwargs.pop(name)) for name in self.suffix}
        for path in products.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        returned: object = self.target(**kwargs)
        frames = _check_result(_label(self.target), returned, list(self.suffix))
        for name, suffix in self.suffix.items():
            _SAVERS[suffix](frames[name], products[name])


def _label(target: Callable[..., Any]) -> str:
    """Return ``target``'s qualified name, or its repr when it has none."""
    qualname = getattr(target, "__qualname__", None)
    return qualname if isinstance(qualname, str) else repr(target)


def _joined(parts: Sequence[str]) -> str:
    """Return ``parts`` joined as ``a, b and c``."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _product_names(names: Sequence[str]) -> str:
    """Return the sentence that lists the product names, sorted."""
    noun = "name is" if len(names) == 1 else "names are"
    return f"The product {noun} {_joined([repr(name) for name in sorted(names)])}."


def _check_result(
    label: str, result: object, names: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """Return ``result`` as frames by product name; raise if it does not match ``names``."""
    __tracebackhide__ = True
    if not isinstance(result, Mapping):
        hint = _MISSING_RETURN if result is None else ""
        raise TypeError(
            f"Python step target {label} returned {type(result).__name__}; a python step "
            "whose produces is a mapping must return a dict that maps each product name "
            f"to a pandas DataFrame. {_product_names(names)}{hint}"
        )
    missing = sorted(set(names) - set(result))
    extra = sorted((key for key in result if key not in names), key=repr)
    parts: list[str] = []
    if missing:
        quoted = _joined([repr(name) for name in missing])
        verb = "product {} is" if len(missing) == 1 else "products {} are"
        parts.append(f"the {verb.format(quoted)} missing")
    if extra:
        quoted = _joined([repr(key) for key in extra])
        if len(extra) == 1:
            parts.append(f"the key {quoted} is not a product name")
        else:
            parts.append(f"the keys {quoted} are not product names")
    if parts:
        raise ValueError(
            f"Python step target {label} returned a dict whose keys do not match its "
            f"product names: {'; '.join(parts)}. {_product_names(names)}"
        )
    wrong = [
        f"for {name!r} is {type(result[name]).__name__}"
        for name in names
        if not isinstance(result[name], pd.DataFrame)
    ]
    if wrong:
        raise TypeError(
            f"Python step target {label} returned a dict whose value {_joined(wrong)}; "
            "each value must be a pandas DataFrame."
        )
    return {name: result[name] for name in names}


def target_reference(target: Callable[..., Any]) -> str:
    """Return the import reference that names ``target`` in another process.

    Parameters
    ----------
    target
        The callable to name.

    Returns
    -------
        ``"package.module:qualname"``, which :func:`resolve_reference` turns back into
        ``target`` itself.

    Raises
    ------
    ValueError
        If ``target`` cannot be imported by reference: it is defined in ``__main__`` (a
        script or a notebook), is a lambda, is defined inside another function (including
        a function wrapped by a decorator that does not use ``functools.wraps``), has no
        module or qualified name (a ``functools.partial`` or a callable instance), its
        reference cannot be imported, or its reference imports a different object (a bound
        method or a classmethod, or a function whose module was reloaded after the step
        was built). The message names the case and says what to do: rebuild the steps
        after a reload, and otherwise define the function at the top level of an
        importable module.
    """
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    cause: Exception | None = None
    advice = "Define the function at the top level of an importable module."
    if not isinstance(module, str) or not isinstance(qualname, str):
        reason = (
            "it has no module or qualified name, as with a functools.partial or a "
            "callable instance"
        )
    elif module == "__main__":
        reason = "it is defined in __main__ (a script or a notebook)"
    elif "<lambda>" in qualname:
        reason = "it is a lambda"
    elif "<locals>" in qualname:
        reason = (
            "it is defined inside another function (a nested function, or one returned "
            "by a decorator that does not use functools.wraps)"
        )
    else:
        reference = f"{module}:{qualname}"
        try:
            resolved = resolve_reference(reference)
        except (ImportError, AttributeError) as error:
            cause = error
            reason = (
                f"it cannot be imported as {reference!r} "
                f"(importing it raised {type(error).__name__}: {error})"
            )
        else:
            if resolved is target:
                return reference
            if _is_reloaded(target, resolved):
                reason = (
                    f"its reference {reference!r} imports a newer copy of it: its module "
                    "was reloaded after the step was built (importlib.reload or IPython's "
                    "autoreload)"
                )
                advice = "Build the steps again after reloading the module."
            else:
                reason = (
                    f"its reference {reference!r} imports a different object (as for a "
                    "bound method or a classmethod)"
                )
    raise ValueError(f"Cannot import {target!r} by reference: {reason}. {advice}") from cause


def _is_reloaded(target: Callable[..., Any], resolved: Callable[..., Any]) -> bool:
    """Return whether ``resolved`` is a reloaded module's copy of the plain function ``target``."""
    return (
        inspect.isfunction(target)
        and inspect.isfunction(resolved)
        and resolved.__module__ == target.__module__
        and resolved.__qualname__ == target.__qualname__
    )


def resolve_reference(reference: str) -> Callable[..., Any]:
    """Import and return the callable that ``reference`` names.

    Parameters
    ----------
    reference
        ``"package.module:qualname"``, as :func:`target_reference` returns.

    Returns
    -------
        The named callable.

    Raises
    ------
    ValueError
        If ``reference`` is not of the form ``"module:qualname"``.
    ImportError
        If the module cannot be imported.
    AttributeError
        If the module has no object at the qualified name.
    """
    parts = reference.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Reference {reference!r} is not of the form 'module:qualname'.")
    module_name, qualname = parts
    found: Any = importlib.import_module(module_name)
    for attribute in qualname.split("."):
        found = getattr(found, attribute)
    return cast(Callable[..., Any], found)


def python_step(
    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]],
    *,
    inputs: Mapping[str, FileKey],
    produces: FileKey | Mapping[str, FileKey],
    registry: FileRegistry,
    resources: dict[str, Any] | None = None,
    name: str | None = None,
) -> Task:
    """Compile ``target`` into one backend task wired through ``registry``.

    The step runs in the pipeline's own environment; a step that needs another
    environment is a :func:`~vivarium.dagger.frontend.command_step.command_step`.

    Parameters
    ----------
    target
        A function returning a DataFrame, or, when ``produces`` is a mapping, a dict that
        maps each of its names to a DataFrame. Each declared input is passed to it, by
        its parameter name, as the input's resolved file path; the function reads the
        path itself.
    inputs
        Maps each of ``target``'s parameter names to the file key the registry
        resolves to the producing step's node (or a declared root input).
    produces
        The file key of the product, or a mapping of product names to file keys. Each
        key's suffix names the format its DataFrame is saved in (``.parquet`` or
        ``.csv``), and each key is registered with ``registry`` so that a downstream
        step that names it gets an edge to this step. A product name must be a valid
        Python identifier, not a keyword, not a reserved name, and not one of
        ``inputs``' names. With one key, ``product`` cannot be an input name. The
        mapping must have at least one entry. Each product's parent directory is
        created when the step runs.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster
        bridge to read later.
    name
        The step's name, which becomes the task name. Defaults to ``produces`` when it
        is a key, or to its one key when it is a mapping with one entry. Required when
        it has more than one.

    Returns
    -------
        One :class:`~vivarium.dagger.backend.task.Task` whose ``run`` is a
        :class:`PythonStepRun` of ``target`` and the product suffix (or each product
        name's suffix), whose outputs are the registry's node for ``produces`` under
        ``"product"``, or for each mapped key under its name, and whose inputs are the
        resolved nodes, ready to collect into a list and hand to ``build`` or
        ``preview``.

    Raises
    ------
    vivarium.dagger.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or a ``produces``
        key is a declared root input or resolves to the path of a different key that
        is already registered. The message starts with ``Step '<name>':``, followed by
        the registry's message, which names the file key. Raised before any key is
        registered.
    ValueError
        If a ``produces`` key has an unsupported suffix (the message names the key, and
        with a mapping its product name); ``name`` is empty; the mapping is empty; the
        mapping has more than one entry and ``name`` is ``None``; a product name is not
        a valid Python identifier, is a Python keyword, is reserved, or is also an input
        name; two products resolve to one path; an input and a product are one file; or,
        with one key, an input is named ``product``. The messages for an empty name, an
        empty mapping with no name, and several products with no name start with
        ``python_step:``; every other message starts with ``Step '<name>':``. Every one
        of these is raised before anything is registered.
    """
    if isinstance(produces, Mapping):
        return _compile_products(target, inputs, dict(produces), registry, resources, name)
    task_name = produces if name is None else name
    if not task_name:
        raise ValueError(f"python_step: name must be a non-empty string, got {task_name!r}.")
    suffix = Path(produces).suffix
    _check_suffix(suffix, f"Step {task_name!r}: produces key {produces!r}")
    run = PythonStepRun(target, suffix)
    if _PRODUCT_ARG in inputs:
        raise ValueError(
            f"Step {task_name!r}: input parameter name {_PRODUCT_ARG!r} collides with the "
            "parameter name reserved for the product."
        )
    try:
        dependencies = {param: registry.resolve_input(key) for param, key in inputs.items()}
        product = registry.register_output(produces)
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=run,
        inputs=dependencies,
        outputs={_PRODUCT_ARG: product},
        resources={} if resources is None else resources,
        env=None,
    )


def _compile_products(
    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]],
    inputs: Mapping[str, FileKey],
    produces: dict[str, FileKey],
    registry: FileRegistry,
    resources: dict[str, Any] | None,
    name: str | None,
) -> Task:
    """Compile a python step whose ``produces`` maps product names to file keys."""
    task_name = _step_name(produces, name, "python_step")
    if not produces:
        raise ValueError(_no_product(f"Step {task_name!r}"))
    input_keys = dict(inputs)
    _check_names(task_name, input_keys, produces)
    suffixes: dict[str, str] = {}
    for product, key in produces.items():
        suffixes[product] = Path(key).suffix
        _check_suffix(
            suffixes[product], f"Step {task_name!r}: product {product!r} (key {key!r})"
        )
    _check_shared_keys(task_name, input_keys, produces)
    try:
        dependencies = {
            param: registry.resolve_input(key) for param, key in input_keys.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    product_paths = {product: registry.path_for(key) for product, key in produces.items()}
    _check_paths(task_name, input_keys, dependencies, produces, product_paths)
    try:
        for key in produces.values():
            registry.check_output(key)
        products = {
            product: registry.register_output(key) for product, key in produces.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=PythonStepRun(target, suffixes),
        inputs=dependencies,
        outputs=products,
        resources={} if resources is None else resources,
        env=None,
    )
