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

Code identity defaults to always-stale: a per-target code fingerprint is deferred,
so in this cut a step re-runs on every build.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactKey, ArtifactRegistry

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


def python_step(
    target: Callable[..., pd.DataFrame],
    *,
    inputs: Mapping[str, ArtifactKey],
    produces: ArtifactKey,
    registry: ArtifactRegistry,
    resources: dict[str, Any] | None = None,
    env: str | None = None,
) -> Task:
    """Compile ``target`` into one backend task wired through ``registry``.

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
    env
        Environment the step runs in, carried on the task; ``None`` means the
        pipeline's own environment.

    Returns
    -------
        One :class:`~vivarium.workflow.backend.task.Task` whose product
        is the registry's node for ``produces`` and whose inputs are the resolved
        nodes, ready to collect into a list and hand to ``build`` or ``preview``.

    Raises
    ------
    vivarium.workflow.backend.wiring.WiringError
        If an input key resolves to no producer and no root input.
    ValueError
        If ``produces`` has an unsupported suffix, or the product's parameter name
        collides with one of ``target``'s input parameter names.
    """
    suffix = Path(produces).suffix
    if suffix not in _SAVERS:
        supported = ", ".join(sorted(_SAVERS))
        raise ValueError(
            f"produces key {produces!r} has unsupported suffix {suffix!r}; "
            f"supported suffixes are {supported}."
        )
    save = _SAVERS[suffix]

    if _PRODUCT_ARG in inputs:
        raise ValueError(
            f"product parameter name {_PRODUCT_ARG!r} collides with an input "
            f"parameter of the same name."
        )

    task_inputs = {param: registry.resolve_input(key) for param, key in inputs.items()}
    product_node = registry.register_output(produces)

    def _run_step(**kwargs: Any) -> None:
        """Call ``target`` with its loaded inputs and autosave the DataFrame product."""
        product_path = Path(kwargs.pop(_PRODUCT_ARG))
        product_path.parent.mkdir(parents=True, exist_ok=True)
        frame = target(**kwargs)
        save(frame, product_path)

    # TODO(MIC-7520): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=produces,
        run=_run_step,
        inputs=task_inputs,
        outputs={_PRODUCT_ARG: product_node},
        resources={} if resources is None else resources,
        env=env,
    )
