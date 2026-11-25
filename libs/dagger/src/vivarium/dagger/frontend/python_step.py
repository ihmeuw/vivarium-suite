"""Author a pipeline step as a plain function.

A step is a function that reads its declared inputs and returns a ``pandas``
DataFrame, or a dict of named DataFrames. :func:`python_step` compiles one such function
into a single backend :class:`~vivarium.dagger.backend.task.Task`, resolving the step's
inputs and its outputs through the shared
:class:`~vivarium.dagger.backend.wiring.FileRegistry`, so each input is the producer's own
node and pytask draws the producer-to-consumer edge from its path. Each input reaches the
target as its resolved file path, which the target reads itself; only the returned
DataFrames are autosaved, each to its output's path, in the format named by the output
key's suffix.

The task's ``run`` is a :class:`~vivarium.dagger.runtime.python_run.PythonStepRun`, a
data-only object rather than a closure, so the same step can be rebuilt in another
process from the target's import reference (:func:`target_reference`) and the output
suffix or suffixes.

Code identity defaults to always-stale: a per-target code fingerprint is deferred,
so in this cut a step re-runs on every build.
"""

import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileKey, FileRegistry, WiringError
from vivarium.dagger.frontend.command_step import (
    _check_names,
    _check_paths,
    _check_shared_keys,
    _no_output,
    _reserved_name,
    _step_name,
)
from vivarium.dagger.frontend.notebook_step import _PYTHON_OUTPUT, _RESERVED_NAMES
from vivarium.dagger.runtime.python_run import PythonStepRun, _check_suffix, resolve_reference


def target_reference(target: Callable[..., Any]) -> str:
    """Return the import reference that names ``target`` in another process.

    Parameters
    ----------
    target
        The callable to name.

    Returns
    -------
        ``"package.module:qualname"``, which
        :func:`~vivarium.dagger.runtime.python_run.resolve_reference` turns back into
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


def python_step(
    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]],
    *,
    inputs: Mapping[str, FileKey],
    outputs: FileKey | Mapping[str, FileKey],
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
        A function returning a DataFrame, or, when ``outputs`` is a mapping, a dict that
        maps each of its names to a DataFrame. Each declared input is passed to it, by
        its parameter name, as the input's resolved file path; the function reads the
        path itself.
    inputs
        Maps each of ``target``'s parameter names to the file key the registry
        resolves to the producing step's node (or a declared root input). No input name
        may be reserved.
    outputs
        The file key of the output, or a mapping of output names to file keys. Each
        key's suffix names the format its DataFrame is saved in (``.parquet`` or
        ``.csv``), and each key is registered with ``registry`` so that a downstream
        step that names it gets an edge to this step. An output name must be a valid
        Python identifier, not a keyword, not a reserved name, and not one of
        ``inputs``' names. With one key, the output's path reaches the run under the
        reserved name ``__output__``. The mapping must have at least one entry. Each
        output's parent directory is created when the step runs.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster
        bridge to read later.
    name
        The step's name, which becomes the task name. Defaults to ``outputs`` when it
        is a key, or to its one key when it is a mapping with one entry. Required when
        it has more than one.

    Returns
    -------
        One :class:`~vivarium.dagger.backend.task.Task` whose ``run`` is a
        :class:`~vivarium.dagger.runtime.python_run.PythonStepRun` of ``target`` and the
        output suffix (or each output name's suffix), whose outputs are the registry's
        node for ``outputs`` under ``"__output__"``, or for each mapped key under its
        name, and whose inputs are the resolved nodes, ready to collect into a list and
        hand to ``build`` or ``preview``.

    Raises
    ------
    vivarium.dagger.backend.wiring.WiringError
        If an input key resolves to no producer and no root input, or an ``outputs``
        key is a declared root input or resolves to the path of a different key that
        is already registered. The message starts with ``Step '<name>':``, followed by
        the registry's message, which names the file key. Raised before any key is
        registered.
    ValueError
        If an ``outputs`` key has an unsupported suffix (the message names the key, and
        with a mapping its output name); ``name`` is empty; the mapping is empty; the
        mapping has more than one entry and ``name`` is ``None``; an input name is
        reserved; an output name is not a valid Python identifier, is a Python keyword,
        is reserved, or is also an input name; two outputs resolve to one path; or an
        input and an output are one file. The messages for an empty name, an empty
        mapping with no name, and several outputs with no name start with
        ``python_step:``; every other message starts with ``Step '<name>':``. Every one
        of these is raised before anything is registered.
    """
    if isinstance(outputs, Mapping):
        return _compile_outputs(target, inputs, dict(outputs), registry, resources, name)
    task_name = outputs if name is None else name
    if not task_name:
        raise ValueError(f"python_step: name must be a non-empty string, got {task_name!r}.")
    suffix = Path(outputs).suffix
    _check_suffix(suffix, f"Step {task_name!r}: output key {outputs!r}")
    run = PythonStepRun(target, suffix)
    for param in inputs:
        if param in _RESERVED_NAMES:
            raise _reserved_name(task_name, param, "inputs")
    try:
        dependencies = {param: registry.resolve_input(key) for param, key in inputs.items()}
        node = registry.register_output(outputs)
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=run,
        inputs=dependencies,
        outputs={_PYTHON_OUTPUT: node},
        resources={} if resources is None else resources,
        env=None,
    )


def _compile_outputs(
    target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]],
    inputs: Mapping[str, FileKey],
    outputs: dict[str, FileKey],
    registry: FileRegistry,
    resources: dict[str, Any] | None,
    name: str | None,
) -> Task:
    """Compile a python step whose ``outputs`` maps output names to file keys."""
    task_name = _step_name(outputs, name, "python_step")
    if not outputs:
        raise ValueError(_no_output(f"Step {task_name!r}"))
    input_keys = dict(inputs)
    _check_names(task_name, input_keys, outputs)
    suffixes: dict[str, str] = {}
    for output, key in outputs.items():
        suffixes[output] = Path(key).suffix
        _check_suffix(
            suffixes[output], f"Step {task_name!r}: output {output!r} (key {key!r})"
        )
    _check_shared_keys(task_name, input_keys, outputs)
    try:
        dependencies = {
            param: registry.resolve_input(key) for param, key in input_keys.items()
        }
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    output_paths = {output: registry.path_for(key) for output, key in outputs.items()}
    _check_paths(task_name, input_keys, dependencies, outputs, output_paths)
    try:
        for key in outputs.values():
            registry.check_output(key)
        nodes = {output: registry.register_output(key) for output, key in outputs.items()}
    except WiringError as error:
        raise WiringError(f"Step {task_name!r}: {error}") from None
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=task_name,
        run=PythonStepRun(target, suffixes),
        inputs=dependencies,
        outputs=nodes,
        resources={} if resources is None else resources,
        env=None,
    )
