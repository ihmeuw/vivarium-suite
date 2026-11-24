"""Turn pytask's dry-run into a structured, in-memory plan.

The staleness engine is pytask's, so this module is thin: it runs a dry-run and
reads one ``(name, outcome, reasons)`` entry per step off the session, in the order
of the tasks it was given, so that a caller does not parse pytask's printed table.
:func:`vivarium.dagger.frontend.execution.preview` relabels each reason's node in
pipeline terms, and ``dagger preview`` prints that plan.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytask

from vivarium.dagger.backend._pickling import reduce_exception
from vivarium.dagger.backend.build import build, failure_message, reports_in_task_order
from vivarium.dagger.backend.task import Task


@dataclass(frozen=True)
class Reason:
    """One reason pytask gave for a step being stale.

    ``str(reason)`` is a short phrase. The phrases are ``"<node> changed"``, ``"<node> is
    missing"``, ``"<node> has no recorded state"`` (``not_in_db``), ``"first run"``,
    ``"forced"``, and ``"<node> would run first"`` (``cascade``). Any other kind reads
    ``"<node>: <kind>"``.
    """

    node: str
    """The node pytask compared. The backend preview names it as pytask does, which is an
    absolute path for a file, the code identity's name (``'<step>::code'`` by default),
    ``'<step>::__command__'`` for a command, a step name, or ``''`` for ``forced``.
    :func:`vivarium.dagger.frontend.execution.preview` replaces it with a label."""
    kind: str
    """pytask's reason, one of ``changed``, ``missing``, ``not_in_db``, ``first_run``,
    ``forced``, or ``cascade``."""

    def __str__(self) -> str:
        """Return the reason as a short phrase."""
        phrases = {
            "changed": f"{self.node} changed",
            "missing": f"{self.node} is missing",
            "not_in_db": f"{self.node} has no recorded state",
            "first_run": "first run",
            "forced": "forced",
            "cascade": f"{self.node} would run first",
        }
        return phrases.get(self.kind, f"{self.node}: {self.kind}")


@dataclass
class StepPreview:
    """One step's entry in a run preview."""

    name: str
    """The task name, which is the step name the frontend gave it."""
    outcome: pytask.TaskOutcome
    """pytask's dry-run outcome for the step."""
    reasons: list[Reason] = field(default_factory=list)
    """Why the step would run, from pytask's ``--explain``; empty for a skip."""

    @property
    def would_run(self) -> bool:
        """Whether the step would execute, as opposed to being skipped."""
        return self.outcome == pytask.TaskOutcome.WOULD_BE_EXECUTED


class PlanFailed(RuntimeError):
    """pytask could not build the plan for a preview.

    Parameters
    ----------
    message
        Why the plan could not be built.
    exit_code
        The exit code of pytask's dry run.
    """

    exit_code: pytask.ExitCode
    """The exit code of pytask's dry run."""

    def __init__(self, message: str, *, exit_code: pytask.ExitCode) -> None:
        super().__init__(message)
        self.exit_code = exit_code

    def __reduce__(self) -> tuple[Any, ...]:
        """Rebuild the error with its message and exit code when it is copied or pickled."""
        return reduce_exception(self)


def preview(tasks: Iterable[Task], root: Path, **kwargs: Any) -> list[StepPreview]:
    """Return a structured plan for ``tasks`` without running anything.

    Runs pytask with execution off (``dry_run=True, explain=True``) and reads one
    entry per step off the session's execution reports, in the order of ``tasks``. A
    preview reads ``pytask.lock`` and the ``.pytask`` store and writes nothing under the
    ``root`` of a pipeline that has run: no declared output, not ``pytask.lock`` and not
    pytask's file-hash cache, so it is safe next to a build of the same root. dagger's
    pytask plugin, :mod:`vivarium.dagger.bridge.plugin`, keeps that cache from being saved;
    pytask loads the plugin through dagger's entry point, so every environment where
    dagger is installed has it, except an editable install made before the plugin existed.
    On a ``root`` that was never built, a preview creates ``root``, its ``pytask.toml``
    and pytask's ``.pytask`` folder, as
    :func:`~vivarium.dagger.backend.build.build` does, but still writes no output, no
    ``pytask.lock`` and no file-hash cache.

    Parameters
    ----------
    tasks
        The compiled tasks, in dependency order, as for
        :func:`~vivarium.dagger.backend.build.build`.
    root
        Directory holding pytask's config, state, and lockfile (see
        :func:`~vivarium.dagger.backend.build.build`).
    kwargs
        Extra keyword arguments forwarded to
        :func:`~vivarium.dagger.backend.build.build`.

    Returns
    -------
        One :class:`StepPreview` per step, in the order of ``tasks``.

    Raises
    ------
    PlanFailed
        If the build does not complete (for example a collection or DAG failure), since
        the session's reports are then empty or partial and would misreport as an
        all-skip plan. The message starts ``Preview could not build the plan:`` and
        carries pytask's reason, including any step that failed, as
        :func:`~vivarium.dagger.backend.build.failure_message` gives it, and
        ``exit_code`` is pytask's exit code. It is a ``RuntimeError``.
    ValueError
        For any of the reasons :func:`~vivarium.dagger.backend.build.build` raises it,
        including a declared env that does not resolve and tasks that are not in
        dependency order.
    """
    session = build(tasks, root, dry_run=True, explain=True, **kwargs)
    if session.exit_code != pytask.ExitCode.OK:
        raise PlanFailed(
            f"Preview could not build the plan: {failure_message(session)}",
            exit_code=session.exit_code,
        )
    return [_step_preview(report) for report in reports_in_task_order(session)]


def _step_preview(report: pytask.ExecutionReport) -> StepPreview:
    """Read a single step's preview off its execution report."""
    return StepPreview(
        name=report.task.name,
        outcome=report.outcome,
        reasons=_reasons(report.task.attributes.get("explanation")),
    )


def _reasons(explanation: Any) -> list[Reason]:
    """Read the structured reasons off a pytask explanation object."""
    return [
        Reason(node=reason.node_name, kind=reason.reason)
        for reason in getattr(explanation, "reasons", ())
    ]
