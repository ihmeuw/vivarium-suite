"""Run or preview a whole pipeline by step name.

A :class:`Pipeline` holds a pipeline's compiled tasks, the directory for its pytask
state, and the artifact registry its steps were wired through. A pipeline definition
returns one, and the verbs here take one. :func:`run` builds the pipeline, or only the
named steps and their ancestors, and can force named steps to run even when they are
current. :func:`preview` returns the plan without running anything, with each reason
naming, where it can, an artifact key or a step instead of a file path or a pytask node
name.
"""

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Any

from _pytask.lockfile import LockfileError, LockfileState
from pytask import ExitCode, PathNode, Session, TaskOutcome

from vivarium.workflow.backend.build import COMMAND_KEY, build, failure_message, outcomes
from vivarium.workflow.backend.preview import StepPreview
from vivarium.workflow.backend.preview import preview as backend_preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry

# TODO(MIC-7541): once iterate() fans a step out over coordinates, map each task name
# back to its step and coordinates; until then every task name is a step name.

_INTERNAL_PACKAGES = ("_pytask", "pluggy")
"""Top-level packages whose leading frames a failed step's traceback drops."""


@dataclass(frozen=True)
class Pipeline:
    """A compiled pipeline, the directory that holds its pytask state, and its registry."""

    tasks: Sequence[Task]
    """The compiled tasks. Each task's name is a step name."""
    root: Path
    """Directory for pytask's configuration, state database, and lockfile, as
    :func:`~vivarium.workflow.backend.build.build` uses it."""
    registry: ArtifactRegistry | None = None
    """The registry the steps were wired through. With it, :func:`preview` names files by
    artifact key; without it, by path."""


class PipelineError(Exception):
    """A verb named an unknown step or the pipeline cannot be built.

    A failed step raises the subclass :class:`StepFailed`.
    """


class StepFailed(PipelineError):
    """One or more steps failed during :func:`run`.

    The message names each failed step with its error's type and message. An error that
    carries ``exc_type`` and ``exc_message`` attributes, as a failure reported from
    another process does, is described by those attributes instead of its own type and
    message.

    Parameters
    ----------
    failures
        Each failed step's exception, keyed by step name, in pytask's report order.
    outcomes
        Every step's outcome from the run, keyed by step name, in pytask's report order.
    """

    failures: dict[str, BaseException]
    """Each failed step's exception, keyed by step name, in pytask's report order."""
    outcomes: dict[str, TaskOutcome]
    """Every step's outcome from the run, keyed by step name, in pytask's report order."""

    def __init__(
        self, failures: dict[str, BaseException], outcomes: dict[str, TaskOutcome]
    ) -> None:
        self.failures = failures
        self.outcomes = outcomes
        lines = (
            f"Step {step!r} failed: {_describe(error)}" for step, error in failures.items()
        )
        super().__init__("\n".join(lines))


def run(
    pipeline: Pipeline,
    *,
    only: str | Iterable[str] | None = None,
    force: str | Iterable[str] | None = None,
    **build_kwargs: Any,
) -> dict[str, TaskOutcome]:
    """Build ``pipeline`` and return each step's outcome, keyed by step name.

    Parameters
    ----------
    pipeline
        The pipeline to build.
    only
        Names of the steps to run. pytask runs each named step and its ancestors and
        reports every other step as ``SKIP``. A string is one step name. ``None`` runs
        every step.
    force
        Names of steps to run even if they are current. Their entries are removed from
        ``pytask.lock`` under ``pipeline.root`` before the build starts, and stay removed
        if the build is a dry run or is rejected, so those steps run on the next build.
        When ``only`` is given, the forced steps are added to it. In a local build, a
        forced step's descendants run again only if one of their inputs changed, so a
        forced step that writes identical bytes leaves them ``SKIP_UNCHANGED``. A string
        is one step name.
    build_kwargs
        Extra keyword arguments passed to
        :func:`~vivarium.workflow.backend.build.build`, and from there to
        ``pytask.build``.

    Returns
    -------
        Each step's outcome, keyed by step name.

    Raises
    ------
    PipelineError
        If ``only`` or ``force`` names a step that is not in the pipeline, ``pytask.lock``
        cannot be read to force steps, the backend rejects the tasks (for example, two
        steps share a name or a declared env does not resolve), or pytask stops before
        any step fails. An unknown step is reported before anything is built or written,
        with the valid step names and the closest match. A rejection by the backend is
        chained from its ``ValueError``, and an unreadable lockfile from pytask's error.
        When pytask stops, the message is
        :func:`~vivarium.workflow.backend.build.failure_message`'s; for a cause pytask
        does not record, such as an error in a pytask plugin, that is only the exit
        code's name, and pytask's printed output shows the cause.
    StepFailed
        If one or more steps fail. Each failure's traceback starts at its first frame
        outside pytask and pluggy that is not marked with ``__tracebackhide__``, which
        for a step body is the first frame of the step's own code; when no frame
        qualifies, the full traceback is kept. The first failure in report order is the
        cause. A ``KeyboardInterrupt`` or ``SystemExit`` raised in a step is re-raised
        as itself instead.
    """
    selected = None if only is None else _resolve(pipeline, only)
    forced: set[str] = set() if force is None else _resolve(pipeline, force)
    if selected is not None:
        selected |= forced
    _reset(pipeline.root, forced)
    try:
        session = build(pipeline.tasks, pipeline.root, selected=selected, **build_kwargs)
    except ValueError as error:
        raise PipelineError(str(error)) from error
    result = outcomes(session)
    failures = _failures(session)
    for failure in failures.values():
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise failure
    if failures:
        raise StepFailed(failures, result) from next(iter(failures.values()))
    if session.exit_code != ExitCode.OK:
        raise PipelineError(failure_message(session))
    return result


def preview(
    pipeline: Pipeline, *, only: str | Iterable[str] | None = None
) -> list[StepPreview]:
    """Return the plan for ``pipeline``, with each reason's node named in pipeline terms.

    Like the backend :func:`~vivarium.workflow.backend.preview.preview`, this runs
    nothing and writes no product. Each reason's ``node`` is relabeled. A file the
    pipeline's registry knows reads ``artifact '<key>'``, a step's code identity reads
    ``code of '<step>'``, a step's command reads ``command of '<step>'``, and a step name
    reads ``step '<step>'``. Any other node, including every file when the pipeline has
    no registry, keeps pytask's name.

    Parameters
    ----------
    pipeline
        The pipeline to plan.
    only
        Names of the steps to plan, as for :func:`run`. Every other step reports
        ``SKIP`` with no reasons.

    Returns
    -------
        One :class:`~vivarium.workflow.backend.preview.StepPreview` per step, in
        pytask's report order.

    Raises
    ------
    PipelineError
        If ``only`` names a step that is not in the pipeline, the backend rejects the
        tasks, or pytask cannot collect or plan them, as for :func:`run`.
    """
    selected = None if only is None else _resolve(pipeline, only)
    try:
        plan = backend_preview(pipeline.tasks, pipeline.root, selected=selected)
    except (ValueError, RuntimeError) as error:
        raise PipelineError(str(error)) from error
    labels = _labels(pipeline)
    for step in plan:
        step.reasons = [
            replace(reason, node=labels.get(reason.node, reason.node))
            for reason in step.reasons
        ]
    return plan


def _resolve(pipeline: Pipeline, steps: str | Iterable[str]) -> set[str]:
    """Return ``steps`` as a set of step names; raise PipelineError for an unknown one."""
    names = {steps} if isinstance(steps, str) else set(steps)
    valid = sorted(task.name for task in pipeline.tasks)
    unknown = sorted(names.difference(valid))
    if not unknown:
        return names
    hints: list[str] = []
    for name in unknown:
        closest = difflib.get_close_matches(name, valid, n=1)
        if closest:
            subject = "" if len(unknown) == 1 else f" for {name!r}"
            hints.append(f" Did you mean {closest[0]!r}{subject}?")
    listed = ", ".join(repr(name) for name in unknown)
    choices = ", ".join(repr(name) for name in valid) or "none"
    raise PipelineError(f"No step named {listed}.{''.join(hints)} Valid steps: {choices}.")


def _reset(root: Path, steps: set[str]) -> None:
    """Remove ``steps``' entries from ``root``'s ``pytask.lock`` so pytask re-runs them."""
    if not steps:
        return
    path = root / "pytask.lock"
    try:
        state = LockfileState.from_path(path, root)
    except LockfileError as error:
        raise PipelineError(f"Cannot read {path} to force steps: {error}") from error
    state.remove_task_entries(steps)
    state.flush()


def _failures(session: Session) -> dict[str, BaseException]:
    """Return each failed step's exception by step name, trimmed to its first user frame."""
    failures: dict[str, BaseException] = {}
    for report in session.execution_reports:
        error = None if report.exc_info is None else report.exc_info[1]
        if report.outcome == TaskOutcome.FAIL and error is not None:
            failures[report.task.name] = _trimmed(error)
    return failures


def _trimmed(error: BaseException) -> BaseException:
    """Return ``error`` with its traceback starting at its first frame that is not hidden."""
    full = error.__traceback__
    start = full
    while start is not None and _is_hidden(start):
        start = start.tb_next
    return error.with_traceback(full if start is None else start)


def _is_hidden(entry: TracebackType) -> bool:
    """Return whether ``entry`` is in pytask or pluggy or sets ``__tracebackhide__``."""
    frame = entry.tb_frame
    if frame.f_locals.get("__tracebackhide__", False):
        return True
    module = str(frame.f_globals.get("__name__", ""))
    return module.partition(".")[0] in _INTERNAL_PACKAGES


def _describe(error: BaseException) -> str:
    """Return ``error``'s type and message, from ``exc_type`` and ``exc_message`` if set."""
    exc_type = getattr(error, "exc_type", None)
    exc_message = getattr(error, "exc_message", None)
    kind = type(error).__name__ if exc_type is None else str(exc_type)
    message = str(error) if exc_message is None else str(exc_message)
    return f"{kind}: {message}" if message else kind


def _labels(pipeline: Pipeline) -> dict[str, str]:
    """Map the node names in pytask's reasons to the labels :func:`preview` shows."""
    registry = pipeline.registry
    labels: dict[str, str] = {}
    for task in pipeline.tasks:
        labels[task.name] = f"step {task.name!r}"
        if task.code_id is not None:
            labels[task.code_id.name] = f"code of {task.name!r}"
        if isinstance(task.run, str):
            labels[f"{task.name}::{COMMAND_KEY}"] = f"command of {task.name!r}"
        if registry is None:
            continue
        for node in (*task.inputs.values(), *task.outputs.values()):
            if isinstance(node, PathNode) and isinstance(node.path, Path):
                key = registry.key_for(node.path)
                if key is not None:
                    labels[node.name] = f"artifact {key!r}"
    return labels
