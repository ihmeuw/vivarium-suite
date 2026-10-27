"""Author a pipeline step as a plain function.

A step is a function that takes its declared inputs and returns a ``pandas``
DataFrame. :func:`python_step` compiles one such function into a single backend
:class:`~vivarium.cluster_tools.core.backend.task.Task`, resolving the step's
inputs and its product through the shared
:class:`~vivarium.cluster_tools.core.backend.wiring.ArtifactRegistry` so pytask
draws the producer-to-consumer edges from node identity. The returned DataFrame
is autosaved to the product's path, in the format named by the product key's
suffix.

Code identity defaults to always-stale: a per-target code fingerprint is deferred
(MIC-7520), so in this cut a step re-runs on every build.
"""

from collections.abc import Callable, Mapping
from typing import Any

from vivarium.cluster_tools.core.backend.task import Task
from vivarium.cluster_tools.core.backend.wiring import ArtifactKey, ArtifactRegistry


def python_step(
    target: Callable[..., Any],
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
        A function returning a DataFrame. It is called with each declared input
        loaded and passed by its parameter name.
    inputs
        Maps each of ``target``'s parameter names to the artifact key the registry
        resolves to the producing step's node (or a declared root input).
    produces
        The artifact key for ``target``'s product. Its suffix names the save
        format (``.parquet`` or ``.csv``). Registered with ``registry`` so a
        downstream step naming this key gets a real edge to this step.
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
        One :class:`~vivarium.cluster_tools.core.backend.task.Task` whose product
        is the registry's node for ``produces`` and whose inputs are the resolved
        nodes, ready to collect into a list and hand to ``build`` or ``preview``.

    Raises
    ------
    vivarium.cluster_tools.core.backend.wiring.WiringError
        If an input key resolves to no producer and no root input.
    ValueError
        If ``produces`` has an unsupported suffix.
    """
    raise NotImplementedError
