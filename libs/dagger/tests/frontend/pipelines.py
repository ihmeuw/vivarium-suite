"""Make a public Pipeline from compiled tasks, or a snapshot with stable code identities."""

import dataclasses
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from vivarium.dagger.backend.task import Task
from vivarium.dagger.frontend import Pipeline
from vivarium.dagger.frontend.builder import _PipelineSnapshot, freeze
from vivarium.dagger.harness import with_stable_code_id


def pipeline_of(
    tasks: Iterable[Task], state: Path, resources: Mapping[str, Any] | None = None
) -> Pipeline:
    """Return a Pipeline whose state directory is ``state``, with ``tasks`` added in order.

    The tasks are added with :meth:`~vivarium.dagger.frontend.builder.Pipeline.add_task`,
    so the pipeline's registry does not know their file keys and its own output root is
    not used.
    """
    pipeline = Pipeline(state.parent / "output", "run", state=state, resources=resources)
    for task in tasks:
        pipeline.add_task(task)
    return pipeline


def stable_snapshot(pipeline: Pipeline) -> _PipelineSnapshot:
    """Return ``pipeline`` frozen, with a stable code identity on each of its tasks.

    A step method cannot set its task's code identity, so a test that needs both the
    pipeline's file keys in a preview and a step that stays current between runs passes
    this snapshot to the private verbs ``_run`` and ``_preview``.
    """
    snapshot = freeze(pipeline)
    stable = [with_stable_code_id(task) for task in snapshot.tasks]
    return dataclasses.replace(snapshot, tasks=stable)
