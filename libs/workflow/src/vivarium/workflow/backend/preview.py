"""Turn pytask's dry-run into a structured, in-memory plan.

The staleness engine is pytask's, so this module is thin: it runs a dry-run and
reads one ``(name, outcome, reasons)`` entry per step off the session, so the
wrapper can render the plan in step-name terms and feed the same plan to the
visualization overlay and the id-to-step translation, instead of parsing
pytask's printed table.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytask

from vivarium.workflow.backend.build import build, failure_message
from vivarium.workflow.backend.task import Task


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
    ``'<step>::__command__'`` for a command, a step name, or ``''`` for ``forced``."""
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


def preview(tasks: Iterable[Task], root: Path, **kwargs: Any) -> list[StepPreview]:
    """Return a structured plan for ``tasks`` without running anything.

    Runs pytask with execution off (``dry_run=True, explain=True``) and reads one
    entry per step off the session's execution reports. A preview reads
    ``pytask.lock`` and the ``.pytask`` store but writes no declared product.

    Parameters
    ----------
    tasks
        The compiled task set.
    root
        Directory holding pytask's config, state, and lockfile (see
        :func:`~vivarium.workflow.backend.build.build`).
    kwargs
        Extra keyword arguments forwarded to
        :func:`~vivarium.workflow.backend.build.build`.

    Returns
    -------
        One :class:`StepPreview` per step, in the session's report order.

    Raises
    ------
    RuntimeError
        If the build does not complete (a collection or DAG failure), since the
        session's reports are then empty or partial and would misreport as an
        all-skip plan. The message carries pytask's reason, as
        :func:`~vivarium.workflow.backend.build.failure_message` gives it.
    ValueError
        For any of the reasons :func:`~vivarium.workflow.backend.build.build` raises it,
        including a declared env that does not resolve.
    """
    session = build(tasks, root, dry_run=True, explain=True, **kwargs)
    if session.exit_code != pytask.ExitCode.OK:
        raise RuntimeError(f"preview could not build the plan: {failure_message(session)}")
    return [_step_preview(report) for report in session.execution_reports]


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
