"""Run or preview a whole pipeline by step name.

A :class:`Pipeline` holds a pipeline's compiled tasks, the directory for its pytask
state, and the artifact registry its steps were wired through. A pipeline definition
returns one, and the verbs here take one. :func:`run` builds the pipeline, or only the
named steps and their ancestors, and can force named steps to run even when they are
current. :func:`preview` returns the plan without running anything, with each reason
naming, where it can, an artifact key or a step instead of a file path or a pytask node
name. :func:`run` describes a failed command or notebook step without its generated
command and names a step that calls ``sys.exit``; both verbs report two steps that
share a name with what to change.

With ``jobmon=True``, :func:`run` runs the stale steps through the Jobmon bridge (see
:mod:`~vivarium.dagger.bridge.plugin`). It then gives every step its compute resources
in the layers that :mod:`~vivarium.dagger.constants` lists, refuses a pipeline in which
some step would have no project before anything is written, and reports a run that
another run's lock refused as :class:`RunRefused` and a run that was interrupted while
Jobmon ran it as :class:`RunInterrupted`.
"""

import difflib
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any

from _pytask.lockfile import LockfileError, LockfileState
from pytask import ExitCode, PathNode, Session, TaskOutcome

from vivarium.dagger.backend.build import (
    COMMAND_KEY,
    TaskExited,
    build,
    check_bridge_registered,
    describe_exit,
    failure_message,
    outcomes,
)
from vivarium.dagger.backend.lock import RUN_LOCK_NAME, RUN_REFUSED_KEY, RunLocked, run_lock
from vivarium.dagger.backend.preview import PlanFailed, StepPreview
from vivarium.dagger.backend.preview import preview as backend_preview
from vivarium.dagger.backend.task import Task, check_unique_task_names
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge.plugin import LOCAL_ONLY_MODES
from vivarium.dagger.bridge.state import RUN_STATE_KEY, RunState
from vivarium.dagger.constants import DEFAULT_RESOURCES
from vivarium.dagger.frontend.notebook_step import describe_notebook_failure
from vivarium.dagger.frontend.simulation_step import describe_simulation_failure

# TODO(MIC-7541): once iterate() fans a step out over coordinates, map each task name
# back to its step and coordinates; until then every task name is a step name.

_INTERNAL_PACKAGES = ("_pytask", "pluggy")
"""Top-level packages whose leading frames a failed step's traceback drops."""


@dataclass(frozen=True)
class Pipeline:
    """A compiled pipeline, the directory that holds its pytask state, and its registry.

    Raises
    ------
    TypeError
        If ``root`` is not a :class:`pathlib.Path`, for example a string, or
        ``resources`` is not a mapping.
    ValueError
        If ``root`` is a relative path, which would move the pipeline's state with the
        current directory.
    """

    tasks: Sequence[Task]
    """The compiled tasks. Each task's name is a step name."""
    root: Path
    """Absolute directory for pytask's configuration, state database, and lockfile, as
    :func:`~vivarium.dagger.backend.build.build` uses it."""
    registry: ArtifactRegistry | None = None
    """The registry the steps were wired through. With it, :func:`preview` names files by
    artifact key; without it, by path."""
    resources: Mapping[str, Any] = field(default_factory=dict)
    """The definition's default compute resources for a run through Jobmon, such as
    ``{"project": "proj_simscience", "memory": 4}``. They override
    :data:`~vivarium.dagger.constants.DEFAULT_RESOURCES` key by key, and the ``dagger``
    command's options and each step's own ``resources=`` override them in turn. A run in
    this process ignores them. The default is an empty mapping."""

    def __post_init__(self) -> None:
        """Reject a root that is not an absolute path and resources that are not a mapping."""
        if not isinstance(self.root, Path):
            raise TypeError(
                f"Pipeline root must be a pathlib.Path, got {type(self.root).__name__} "
                f"{self.root!r}."
            )
        if not self.root.is_absolute():
            raise ValueError(
                f"Pipeline root must be absolute, got the relative path {str(self.root)!r}."
            )
        if not isinstance(self.resources, Mapping):
            raise TypeError(
                "Pipeline resources must be a mapping, got "
                f"{type(self.resources).__name__} {self.resources!r}."
            )


class PipelineError(Exception):
    """A verb named an unknown step or the pipeline cannot be built.

    A failed step raises the subclass :class:`StepFailed`.

    Parameters
    ----------
    message
        What went wrong.
    exit_code
        pytask's exit code when pytask ran and did not finish the build.
    """

    exit_code: ExitCode | None
    """pytask's exit code when pytask ran and did not finish the build, which is
    ``FAILED`` for :class:`StepFailed`. It is ``None`` when the verb rejected the pipeline
    before pytask ran, as for an unknown step, tasks the backend rejects, or a
    ``pytask.lock`` that ``force`` cannot read."""

    def __init__(self, message: str, *, exit_code: ExitCode | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class StepFailed(PipelineError):
    """One or more steps failed during :func:`run`.

    The message has one entry per failed step, ``Step '<name>' failed: <description>``,
    each starting on a new line. A step's description is the one ``descriptions`` gives
    for it; otherwise it is its error's type and message, which takes more than one line
    when the message does. An error that carries ``exc_type`` and ``exc_message``
    attributes, as a failure reported from another process does, is then described by
    those attributes instead of its own type and message.

    Parameters
    ----------
    failures
        Each failed step's exception, keyed by step name, in pytask's report order.
    outcomes
        Every step's outcome from the run, keyed by step name, in pytask's report order.
    descriptions
        Text that replaces the error's type and message in the line of each step it
        names, keyed by step name. :func:`run` gives one for every failed step.
    """

    failures: dict[str, BaseException]
    """Each failed step's exception, keyed by step name, in pytask's report order."""
    outcomes: dict[str, TaskOutcome]
    """Every step's outcome from the run, keyed by step name, in pytask's report order."""

    def __init__(
        self,
        failures: dict[str, BaseException],
        outcomes: dict[str, TaskOutcome],
        *,
        descriptions: Mapping[str, str] | None = None,
    ) -> None:
        self.failures = failures
        self.outcomes = outcomes
        given: Mapping[str, str] = {} if descriptions is None else descriptions
        lines = (
            f"Step {step!r} failed: {given[step] if step in given else _describe(error)}"
            for step, error in failures.items()
        )
        super().__init__("\n".join(lines), exit_code=ExitCode.FAILED)


class RunRefused(PipelineError):
    """Another run holds the run lock on the pipeline's root, so :func:`run` ran nothing.

    Only a run through Jobmon takes the lock. The message is that of the
    :class:`~vivarium.dagger.backend.lock.RunLocked` that refused the run, which names
    the lock file and the holder's user, pid, host and start time and says what to do;
    the error is chained from it. Its ``exit_code`` is ``ExitCode.FAILED``.
    """


class RunInterrupted(PipelineError):
    """A run through Jobmon was interrupted while Jobmon ran it.

    The bridge turns SIGTERM, or a ``KeyboardInterrupt`` from Jobmon, into an orderly
    stop: it records the steps whose results were already written and reports every
    other submitted step as failed or skipped. Running the pipeline again continues the
    run from what was recorded. The bridge does not cancel the Jobmon workflow, so its
    jobs that had started on compute nodes can still be running.

    The message names the Jobmon workflow id, or says that the run was interrupted
    before its workflow was bound, and names the run directory. Its ``exit_code`` is
    pytask's exit code for the build.

    Parameters
    ----------
    message
        What happened.
    workflow_id
        Kept as :attr:`workflow_id`.
    run_directory
        Kept as :attr:`run_directory`.
    outcomes
        Kept as :attr:`outcomes`.
    exit_code
        pytask's exit code for the build.
    """

    workflow_id: int | None
    """The Jobmon workflow id of the interrupted run, or ``None`` if the run was
    interrupted before its workflow was bound."""
    run_directory: Path
    """The bridge's directory for the run, which holds its recipes, results and state
    file (see :mod:`~vivarium.dagger.bridge.state`)."""
    outcomes: dict[str, TaskOutcome]
    """Every step's outcome from the run, keyed by step name, in pytask's report order."""

    def __init__(
        self,
        message: str,
        *,
        workflow_id: int | None,
        run_directory: Path,
        outcomes: dict[str, TaskOutcome],
        exit_code: ExitCode | None = None,
    ) -> None:
        super().__init__(message, exit_code=exit_code)
        self.workflow_id = workflow_id
        self.run_directory = run_directory
        self.outcomes = outcomes


class StepExited(SystemExit):
    """A step raised :class:`SystemExit`, as ``sys.exit`` does, and stopped the build.

    It is a ``SystemExit`` with the step's exit code, so unless it is caught it ends the
    process with that code, as the step's own ``SystemExit`` would have.

    Parameters
    ----------
    step
        The name of the step that exited.
    code
        The step's exit code, as its ``SystemExit`` held it.
    """

    step: str
    """The name of the step that exited."""

    def __init__(self, step: str, code: str | int | None) -> None:
        super().__init__(code)
        self.step = step


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
        :func:`~vivarium.dagger.backend.build.build`, and from there to
        ``pytask.build``. ``jobmon=True`` runs the stale steps through the Jobmon bridge,
        unless one of the bridge's
        :data:`~vivarium.dagger.bridge.plugin.LOCAL_ONLY_MODES`, such as ``dry_run``, is
        also set. Such a run passes ``jobmon_resources`` to the build as
        :data:`~vivarium.dagger.constants.DEFAULT_RESOURCES`, overridden key by key by
        ``pipeline.resources`` and then by the ``jobmon_resources`` given here, if any.
        The bridge then lets each step's own
        :attr:`~vivarium.dagger.backend.task.Task.resources` override that key by key.
        The other bridge keys, such as ``jobmon_max_attempts``, pass through unchanged.

    Returns
    -------
        Each step's outcome, keyed by step name.

    Raises
    ------
    RunRefused
        If the run goes through Jobmon and another process holds the run lock on
        ``pipeline.root``. Nothing is planned or run. With ``force``, the forced steps'
        entries in ``pytask.lock`` are removed only while this call holds the lock, so a
        run refused before that leaves ``pytask.lock`` as it was. The build takes the
        lock again; if another run takes it in between, the build is refused and the
        forced entries stay removed, as for a rejected build.
    RunInterrupted
        If the run goes through Jobmon and is interrupted while Jobmon runs it (the
        bridge leaves a run state whose status is ``"interrupted"``). It is raised
        instead of :class:`StepFailed`, although the steps that had not finished are
        reported as failed.
    PipelineError
        If two steps share a name, ``jobmon=True`` is given but the bridge's pytask
        plugin is not registered, ``only`` or ``force`` names a step that is not in the
        pipeline, the run goes through Jobmon and some step would have no project,
        ``force`` is given and ``pytask.lock`` is not a lockfile pytask accepts, the
        backend rejects the tasks (for example, a declared env does not resolve), or
        pytask stops before any step fails. A shared name, a missing bridge plugin, an
        unknown step and a step without a project are reported in that order, before
        anything is built or written. A step has no project when neither
        ``jobmon_resources`` as described under ``build_kwargs`` nor the step's own
        resources give a non-empty ``"project"``. Every step of the pipeline is checked,
        not only the steps that would run, and the message names each step without a
        project and the three ways to set one: the ``dagger`` command's ``-P/--project``
        (``jobmon_resources={"project": ...}`` when calling this function),
        ``Pipeline(resources={"project": ...})``, and the step's own ``resources=``. A
        shared name gives one line per name, in sorted order, such as ``Two steps are
        named 'population'; give each a different name= to tell them apart.`` (with the
        count as a digit, as in ``3 steps are named``, for more than two), and is
        chained from the backend's ``ValueError``. An unknown step is reported with the
        valid step names and the closest match. A rejection by the backend is chained from its
        ``ValueError``, and an invalid lockfile from pytask's lockfile error.
        When pytask stops, the message is
        :func:`~vivarium.dagger.backend.build.failure_message`'s; for a cause pytask
        does not record, such as an error in a pytask plugin, that is only the exit
        code's name, and pytask's printed output shows the cause. The error's
        ``exit_code`` is then pytask's exit code; otherwise it is ``None``.
    StepFailed
        If one or more steps fail. Each failure's traceback starts at its first frame
        outside pytask and pluggy that is not marked with ``__tracebackhide__``, which
        for a step body is the first frame of the step's own code; when no frame
        qualifies, the full traceback is kept. ``failures`` holds each step's own
        exception, and the first failure in report order is the cause. Each step's entry
        in the message describes its failure in step terms. A notebook step is
        described by
        :func:`~vivarium.dagger.frontend.notebook_step.describe_notebook_failure` when
        that returns a description, and a simulation step by
        :func:`~vivarium.dagger.frontend.simulation_step.describe_simulation_failure`
        when that returns one, which gives the check's message or says that the
        step's command failed before writing one, with where psimulate's logs are and
        whether the previous complete run was kept. Any other step whose ``run`` is a
        shell command and that failed with :class:`subprocess.CalledProcessError`, in this
        process or on a compute node, is described as ``<exit>; its output is in pytask's
        report for the step.``, where ``<exit>`` is
        :func:`~vivarium.dagger.backend.build.describe_exit` of the return code, so the
        generated command stays out of the message; it is still in the cause and in
        pytask's report. Every other failure, including a ``CalledProcessError`` that a
        python step's own code raised, is described by its type and message, as
        :class:`StepFailed` describes it by default.
    StepExited
        If a step raises ``SystemExit``, for example by calling ``sys.exit``. It names
        the step, keeps the step's exit code, and is chained from the backend's
        :class:`~vivarium.dagger.backend.build.TaskExited`, which is chained from the
        step's own ``SystemExit``. A ``KeyboardInterrupt`` raised in a step is
        re-raised as itself. Either is raised instead of :class:`StepFailed`.
    """
    _check_unique_steps(pipeline)
    try:
        check_bridge_registered(build_kwargs)
    except ValueError as error:
        raise PipelineError(str(error)) from error
    selected = None if only is None else _resolve(pipeline, only)
    forced: set[str] = set() if force is None else _resolve(pipeline, force)
    if selected is not None:
        selected |= forced
    bridged = _through_bridge(build_kwargs)
    if bridged:
        build_kwargs = {
            **build_kwargs,
            "jobmon_resources": _bridge_resources(
                pipeline, build_kwargs.get("jobmon_resources")
            ),
        }
        _check_projects(pipeline, build_kwargs["jobmon_resources"])
        _reset_under_lock(pipeline.root, forced)
    else:
        _reset(pipeline.root, forced)
    try:
        session = build(pipeline.tasks, pipeline.root, selected=selected, **build_kwargs)
    except ValueError as error:
        raise PipelineError(str(error)) from error
    except TaskExited as error:
        raise StepExited(error.task, error.code) from error
    result = outcomes(session)
    if bridged:
        _raise_for_bridge_stop(session, result)
    failures = _failures(session)
    for failure in failures.values():
        if isinstance(failure, KeyboardInterrupt):
            raise failure
    if failures:
        tasks = {task.name: task for task in pipeline.tasks}
        descriptions = {
            step: _step_description(tasks[step], error) for step, error in failures.items()
        }
        raise StepFailed(failures, result, descriptions=descriptions) from next(
            iter(failures.values())
        )
    if session.exit_code != ExitCode.OK:
        raise PipelineError(failure_message(session), exit_code=session.exit_code)
    return result


def preview(
    pipeline: Pipeline, *, only: str | Iterable[str] | None = None
) -> list[StepPreview]:
    """Return the plan for ``pipeline``, with each reason's node named in pipeline terms.

    Like the backend :func:`~vivarium.dagger.backend.preview.preview`, this runs
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
        One :class:`~vivarium.dagger.backend.preview.StepPreview` per step, in
        pytask's report order.

    Raises
    ------
    PipelineError
        If two steps share a name, ``only`` names a step that is not in the pipeline,
        the backend rejects the tasks, or pytask cannot collect or plan them, as for
        :func:`run`. When the
        backend raises :class:`~vivarium.dagger.backend.preview.PlanFailed`, the
        error's ``exit_code`` is that error's; otherwise it is ``None``.
    """
    _check_unique_steps(pipeline)
    selected = None if only is None else _resolve(pipeline, only)
    try:
        plan = backend_preview(pipeline.tasks, pipeline.root, selected=selected)
    except (ValueError, RuntimeError) as error:
        exit_code = error.exit_code if isinstance(error, PlanFailed) else None
        raise PipelineError(str(error), exit_code=exit_code) from error
    labels = _labels(pipeline)
    for step in plan:
        step.reasons = [
            replace(reason, node=labels.get(reason.node, reason.node))
            for reason in step.reasons
        ]
    return plan


def _check_unique_steps(pipeline: Pipeline) -> None:
    """Raise PipelineError, chained from the backend's ValueError, if steps share a name."""
    try:
        check_unique_task_names(pipeline.tasks)
    except ValueError as error:
        counts = Counter(task.name for task in pipeline.tasks)
        lines = [
            f"{'Two' if count == 2 else count} steps are named {name!r}; give each a "
            "different name= to tell them apart."
            for name, count in sorted(counts.items())
            if count > 1
        ]
        raise PipelineError("\n".join(lines)) from error


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


def _through_bridge(build_kwargs: Mapping[str, Any]) -> bool:
    """Return whether a build with ``build_kwargs`` runs its steps through the bridge."""
    if not build_kwargs.get("jobmon"):
        return False
    return not any(build_kwargs.get(mode) for mode in LOCAL_ONLY_MODES)


def _bridge_resources(pipeline: Pipeline, given: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the default resources, then ``pipeline.resources``, then ``given``, merged."""
    merged = {**DEFAULT_RESOURCES, **pipeline.resources}
    if given is not None:
        merged.update(given)
    return merged


def _check_projects(pipeline: Pipeline, resources: Mapping[str, Any]) -> None:
    """Raise PipelineError naming each step with no project in ``resources`` or its own."""
    # The bridge merges each step's own resources over jobmon_resources key by key.
    missing = [
        task.name
        for task in pipeline.tasks
        if not {**resources, **task.resources}.get("project")
    ]
    if not missing:
        return
    noun = "step" if len(missing) == 1 else "steps"
    raise PipelineError(
        "A run through Jobmon needs a project for every step, but none is set for the "
        f"{noun} {', '.join(repr(name) for name in missing)}. Set one with the dagger "
        "command's -P/--project (jobmon_resources={'project': ...} when calling run()), "
        "with Pipeline(resources={'project': ...}) in the definition, or with the step's "
        "own resources={'project': ...}."
    )


def _reset_under_lock(root: Path, steps: set[str]) -> None:
    """Reset ``steps`` while holding ``root``'s run lock; do nothing if ``steps`` is empty."""
    if not steps:
        return
    # run_lock needs the lock file's directory; the build would create it anyway.
    root.mkdir(parents=True, exist_ok=True)
    try:
        with run_lock(root / RUN_LOCK_NAME):
            _reset(root, steps)
    except RunLocked as error:
        raise RunRefused(str(error), exit_code=ExitCode.FAILED) from error


def _raise_for_bridge_stop(session: Session, result: dict[str, TaskOutcome]) -> None:
    """Raise RunRefused or RunInterrupted if the bridge refused the run or it was interrupted."""
    refused = session.config.get(RUN_REFUSED_KEY)
    if isinstance(refused, RunLocked):
        raise RunRefused(str(refused), exit_code=ExitCode.FAILED) from refused
    state = session.config.get(RUN_STATE_KEY)
    if not isinstance(state, RunState) or state.status != "interrupted":
        return
    if state.workflow_id is None:
        stopped = "before its workflow was bound"
    else:
        stopped = f"while Jobmon ran its workflow {state.workflow_id}"
    raise RunInterrupted(
        f"The run through Jobmon was interrupted {stopped}. Its run directory is "
        f"{state.workdir}. Run the pipeline again to continue.",
        workflow_id=state.workflow_id,
        run_directory=state.workdir,
        outcomes=result,
        exit_code=session.exit_code,
    )


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


def _step_description(task: Task, error: BaseException) -> str:
    """Return the text after ``Step '<name>' failed:`` for ``task``'s ``error``."""
    if isinstance(task.run, str):
        notebook = describe_notebook_failure(task, error)
        if notebook is not None:
            return notebook
        simulation = describe_simulation_failure(task, error)
        if simulation is not None:
            return simulation
        returncode = _command_returncode(error)
        if returncode is not None:
            return (
                f"{describe_exit(returncode)}; its output is in pytask's report for the step."
            )
    return _describe(error)


def _command_returncode(error: BaseException) -> int | None:
    """Return a failed command's return code, here or as a compute node reported it."""
    if isinstance(error, subprocess.CalledProcessError):
        return error.returncode
    if getattr(error, "exc_type", None) != "subprocess.CalledProcessError":
        return None
    # A node reports CalledProcessError's own text, which ends with the status or signal.
    message = str(getattr(error, "exc_message", None) or "")
    for pattern, sign in (
        (r"returned non-zero exit status (\d+)\.$", 1),
        (r"died with <Signals\.\w+: (\d+)>\.$", -1),
        (r"died with unknown signal (\d+)\.$", -1),
    ):
        found = re.search(pattern, message)
        if found:
            return sign * int(found.group(1))
    return None


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
