"""Author a pipeline step as a plain function.

A step is a function that reads its declared inputs and returns a ``pandas``
DataFrame. :func:`python_step` compiles one such function into a single backend
:class:`~vivarium.workflow.backend.task.Task`, resolving the step's
inputs and its product through the shared
:class:`~vivarium.workflow.backend.wiring.ArtifactRegistry` so pytask
draws the producer-to-consumer edges from node identity. Each input reaches the
target as its resolved file path, which the target reads itself; only the
returned DataFrame is autosaved, to the product's path, in the format named by
the product key's suffix.

The task's ``run`` is a :class:`PythonStepRun`, a data-only object rather than a
closure, so the same step can be rebuilt in another process from the target's
import reference (:func:`target_reference`) and the product suffix.

Code identity defaults to always-stale: a per-target code fingerprint is deferred,
so in this cut a step re-runs on every build.
"""

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactKey, ArtifactRegistry, WiringError

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


@dataclass(frozen=True)
class PythonStepRun:
    """Call a step's target with its loaded inputs and save the returned DataFrame.

    This is the ``run`` of every task :func:`python_step` builds. It holds only the
    target and the product suffix, so another process can rebuild an equal run from
    :func:`target_reference` of the target and the suffix.
    """

    target: Callable[..., pd.DataFrame]
    """The step's function. Any callable runs locally; running the step in another
    process needs :func:`target_reference` to succeed for it."""
    suffix: str
    """The product key's suffix, which names the save format (``.parquet`` or ``.csv``)."""

    def __post_init__(self) -> None:
        """Reject a suffix that has no save format."""
        _check_suffix(self.suffix, "Product")

    def __call__(self, **kwargs: Any) -> None:
        """Call ``target`` and save the DataFrame it returns to ``kwargs["product"]``.

        The product's parent directory is created first. Every other keyword argument is
        passed to ``target`` unchanged.

        Raises
        ------
        TypeError
            If ``target`` returns something other than a DataFrame. The message names
            ``target`` and the type it returned, and for ``None`` suggests a missing
            ``return``.
        """
        __tracebackhide__ = True
        product = Path(kwargs.pop(_PRODUCT_ARG))
        product.parent.mkdir(parents=True, exist_ok=True)
        result: object = self.target(**kwargs)
        if not isinstance(result, pd.DataFrame):
            qualname = getattr(self.target, "__qualname__", None)
            label = qualname if isinstance(qualname, str) else repr(self.target)
            hint = " Check for a missing return statement." if result is None else ""
            raise TypeError(
                f"Python step target {label} returned {type(result).__name__}; a python "
                f"step must return a pandas DataFrame.{hint}"
            )
        _SAVERS[self.suffix](result, product)


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
        method or a classmethod). The message names the case and says to define the
        function at the top level of an importable module.
    """
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    cause: Exception | None = None
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
            reason = (
                f"its reference {reference!r} imports a different object (as for a "
                "bound method or a classmethod)"
            )
    raise ValueError(
        f"Cannot import {target!r} by reference: {reason}. Define the function at the top "
        "level of an importable module."
    ) from cause


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
    target: Callable[..., pd.DataFrame],
    *,
    inputs: Mapping[str, ArtifactKey],
    produces: ArtifactKey,
    registry: ArtifactRegistry,
    resources: dict[str, Any] | None = None,
    name: str | None = None,
) -> Task:
    """Compile ``target`` into one backend task wired through ``registry``.

    The step runs in the pipeline's own environment; a step that needs another
    environment is a command step.

    Parameters
    ----------
    target
        A function returning a DataFrame. Each declared input is passed to it, by
        its parameter name, as the input's resolved file path; the function reads
        the path itself.
    inputs
        Maps each of ``target``'s parameter names to the artifact key the registry
        resolves to the producing step's node (or a declared root input).
    produces
        The artifact key for ``target``'s product. Its suffix names the save
        format (``.parquet`` or ``.csv``). Registered with ``registry`` so a
        downstream step naming this key gets a real edge to this step. The
        product's parent directory is created when the step runs.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster
        bridge to read later.
    name
        The step's name, which becomes the task name. Defaults to ``produces``.

    Returns
    -------
        One :class:`~vivarium.workflow.backend.task.Task` whose ``run`` is a
        :class:`PythonStepRun` of ``target`` and the product suffix, whose product is
        the registry's node for ``produces``, and whose inputs are the resolved nodes,
        ready to collect into a list and hand to ``build`` or ``preview``.

    Raises
    ------
    vivarium.workflow.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or ``produces`` is a
        declared root input. The message starts with ``Step '<name>':``, followed by the
        registry's message, which names the artifact key.
    ValueError
        If ``produces`` has an unsupported suffix (the message names the step and the
        produces key), the product's parameter name collides with one of ``target``'s
        input parameter names, or ``name`` is empty. Every one of these is raised before
        anything is registered.
    """
    task_name = produces if name is None else name
    if not task_name:
        raise ValueError(f"Task name must be a non-empty string, got {task_name!r}.")
    suffix = Path(produces).suffix
    _check_suffix(suffix, f"Task '{task_name}': produces key {produces!r}")
    run = PythonStepRun(target, suffix)
    if _PRODUCT_ARG in inputs:
        raise ValueError(
            f"Task '{task_name}': input parameter name {_PRODUCT_ARG!r} collides with the "
            "parameter name reserved for the product."
        )
    try:
        dependencies = {param: registry.resolve_input(key) for param, key in inputs.items()}
        product = registry.register_output(produces)
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    # TODO(MIC-7520): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=run,
        inputs=dependencies,
        outputs={_PRODUCT_ARG: product},
        resources={} if resources is None else resources,
        env=None,
    )
