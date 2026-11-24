"""
====================
Pipeline definitions
====================

Run, preview, force or show the steps of a Python pipeline definition for the ``dagger``
command, which calls these functions when ``-c`` names a definition.

Each function loads a definition with
:func:`~vivarium.dagger.frontend.definition.load_pipeline`, freezes the pipeline it
returns once into a snapshot of its steps, checks on that snapshot that no output has
more than one producing step and that the pipeline's state directory can be used, and
runs or previews that snapshot as the verbs of :mod:`~vivarium.dagger.frontend.execution`
do, or prints its steps, so it uses exactly the steps it checked.
A run or a force goes through the Jobmon bridge unless ``local`` is set; Jobmon is then
checked before anything is written and imported only when the bridge runs the workflow,
so a preview and a local run never import it. :doc:`/python_pipelines` shows an example
of each command and describes the exit status and how each error is reported. The
preview plan and the steps that ``dagger show`` prints go to standard output, as do
pytask's progress table and failure reports.
The command's own messages and tracebacks go to standard error, and so does pytask's
output when pytask stops a preview's plan for a reason other than a definition error.

"""

import os
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn

import click
import pytask

from vivarium.dagger.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.dagger.backend.nodes import RunDirectoryNode
from vivarium.dagger.backend.preview import StepPreview
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge import _jobmon_adapter
from vivarium.dagger.bridge.plugin import DEFAULT_CLUSTER
from vivarium.dagger.frontend.builder import _PipelineSnapshot, freeze
from vivarium.dagger.frontend.definition import DefinitionError, load_pipeline
from vivarium.dagger.frontend.execution import (
    PipelineError,
    RunInterrupted,
    RunRefused,
    StepExited,
    StepFailed,
    _preview,
    _resolve,
    _run,
)
from vivarium.dagger.frontend.python_step import PythonStepRun, target_reference
from vivarium.dagger.frontend.root_inputs import LocationInputError

_FRAMEWORK_MODULES = (
    "vivarium.dagger",
    "click",
    "pytask",
    "_pytask",
    "pluggy",
    "importlib",
    "_frozen_importlib",
    "_frozen_importlib_external",
)
"""Modules, with their submodules, whose frames a definition-time traceback hides."""

_CAUSE_HEADER = "\nThe above exception was the direct cause of the following exception:\n\n"
"""What Python prints between an exception and the one it caused."""

_CONTEXT_HEADER = "\nDuring handling of the above exception, another exception occurred:\n\n"
"""What Python prints between an exception and one raised while handling it."""

_SKIP_REASONS = {
    pytask.TaskOutcome.SKIP_UNCHANGED: "unchanged",
    pytask.TaskOutcome.SKIP: "not selected",
}
"""How ``dagger preview`` words the outcomes of steps that would not run."""

_SHOWN_NAMES: dict[str, str | None] = {
    "__notebook__": "notebook",
    "__executed__": "executed",
    "__spec__": "spec",
    "__branches__": "branches",
    "__artifact__": "artifact",
    "__output__": None,
    "__run__": None,
}
"""How ``dagger show`` names dagger's reserved inputs and outputs; ``None`` for no name."""

_SHOW_LABEL_WIDTH = 6
"""The width of the longest line label of ``dagger show``, ``output``."""

_EXIT_INTERRUPTED = 130
"""Exit status after an interrupt, as a shell reports a process stopped by SIGINT."""

_DEFINITION_EXIT_CODES = (pytask.ExitCode.COLLECTION_FAILED, pytask.ExitCode.DAG_FAILED)
"""pytask exit codes that mean the definition's steps cannot be collected or ordered."""


class _DefinitionProblem(click.ClickException):
    """An error in the definition or the pipeline it builds, reported with exit status 2."""

    exit_code = 2


def run_definition(
    definition: str,
    until: tuple[str, ...],
    *,
    local: bool = False,
    project: str | None = None,
    queue: str | None = None,
    max_attempts: int | None = None,
) -> None:
    """Run every stale step of ``definition``, or only ``until`` and the steps they need.

    Errors end the command with a message on standard error and an exit status. It is 2
    if the definition or the command is wrong, or, for a run through Jobmon, if Jobmon
    is not installed or cannot run on this host; that is checked after the definition
    is loaded and before anything is written, and the message says how to install
    Jobmon or to pass ``--local``. It is 1 if a step failed or exited, another run holds
    the pipeline's run lock (the message names the lock file and its holder), or the
    build stopped for another reason. It is 130 if the run was interrupted; the message
    then names the Jobmon workflow id when the workflow was bound and says that running
    the same command again continues the run.

    Parameters
    ----------
    definition
        The pipeline definition, as ``dagger``'s ``-c`` gives it.
    until
        Names of the steps to run with the steps they depend on; empty runs every
        stale step.
    local
        Run the steps in this process. Otherwise they run through the Jobmon bridge on
        the bridge's default cluster.
    project
        The project of every step that does not set its own, for a run through Jobmon.
        It overrides the definition's ``Pipeline.resources``.
    queue
        The queue of every step that does not set its own, for a run through Jobmon,
        overriding the definition's ``Pipeline.resources`` in the same way.
    max_attempts
        How many times Jobmon tries each step. ``None`` keeps the bridge's default.
    """
    try:
        pipeline = _load(definition)
        build_kwargs = _bridge_options(local, project, queue, max_attempts)
        with _verb_errors(pipeline, until, "'--until'"):
            _run(pipeline, only=until or None, **build_kwargs)
    except KeyboardInterrupt:
        _interrupted(resumable=True)


def preview_definition(definition: str, until: tuple[str, ...]) -> None:
    """Print which steps of ``definition`` would run, and why, without running any."""
    try:
        pipeline = _load(definition)
        with _verb_errors(pipeline, until, "'--until'"):
            plan = _quiet_preview(pipeline, until)
        _print_plan(plan)
    except KeyboardInterrupt:
        _interrupted(resumable=False)


def show_definition(definition: str, steps: tuple[str, ...]) -> None:
    """Print each step of ``definition``, or only ``steps``, as a block of labeled lines.

    The definition is loaded, frozen and checked as for :func:`preview_definition`, with
    the same errors, and nothing runs or is written. So, as for a preview, a pipeline
    root that cannot be written exits with status 2, although ``show`` writes nothing
    there. The steps are printed in the order the definition adds them, one block each,
    with a blank line between blocks::

        Step '<name>' (<kind> step, env <env>)
          input  <name>: '<key>' at <path>
          output <name>: '<key>' at <path>
          runs   <command>

    ``<kind>`` is ``python``, ``notebook``, ``command`` or ``simulation``; a task added
    with :meth:`~vivarium.dagger.frontend.builder.Pipeline.add_task` reads ``(task added
    with add_task)``. The env part is left out for a step without an env. A file that the
    pipeline's registry knows, an output or a root input, is shown by its key and
    absolute path, and a run directory that it knows as ``run directory '<key>' at
    <path>``. Any other path node, such as a notebook's source, a simulation's spec or an
    output of a task added with ``add_task``, is shown by its path as the task holds it,
    and a node that is not a path node by its name. dagger's own reserved input and
    output names are shown without their underscores, and the one output of a python
    step or a simulation step's run directory has no name. A step whose command is
    a string shows it on its ``runs`` line, each further line of the command on its own
    line below, indented to the same column; a python step shows ``calls
    <module>:<qualname>``, the import reference of its function.

    Parameters
    ----------
    definition
        The pipeline definition, as for :func:`preview_definition`.
    steps
        The names of the steps to print; all steps when empty. A name that is not a step
        of the definition exits with status 2 and names the valid steps, as for
        :func:`force_definition`.
    """
    try:
        pipeline = _load(definition)
        selected: set[str] | None = None
        if steps:
            with _verb_errors(pipeline, steps, "'STEP...'"):
                selected = _resolve(pipeline, steps)
        blocks = [
            _step_block(pipeline, task)
            for task in pipeline.tasks
            if selected is None or task.name in selected
        ]
        if blocks:
            click.echo("\n\n".join(blocks))
    except KeyboardInterrupt:
        _interrupted(resumable=False)


def force_definition(
    definition: str,
    steps: tuple[str, ...],
    *,
    local: bool = False,
    project: str | None = None,
    queue: str | None = None,
    max_attempts: int | None = None,
) -> None:
    """Run each of ``steps`` even if it is current, with every other stale step.

    ``definition``, ``local``, ``project``, ``queue`` and ``max_attempts`` and the
    errors are as for :func:`run_definition`. ``steps`` names the steps to run even if
    they are current.
    """
    try:
        pipeline = _load(definition)
        build_kwargs = _bridge_options(local, project, queue, max_attempts)
        with _verb_errors(pipeline, steps, "'STEP...'"):
            _run(pipeline, force=steps, **build_kwargs)
    except KeyboardInterrupt:
        _interrupted(resumable=True)


def _load(definition: str) -> _PipelineSnapshot:
    """Load, freeze and check ``definition``; report failures as click errors."""
    try:
        loaded = load_pipeline(definition)
    except (DefinitionError, LocationInputError) as error:
        raise _DefinitionProblem(str(error)) from None
    except (Exception, SystemExit) as error:
        click.echo(_user_traceback(error), err=True, nl=False)
        raise _DefinitionProblem(
            f"Could not load the definition {definition!r}; the traceback above shows why."
        ) from None
    pipeline = freeze(loaded)
    # A Pipeline refuses a second producer of a file path when the step is added. This
    # check still catches two added tasks that share an output that is not a path node,
    # by its node name, since add_task does not compare those.
    _check_single_producers(pipeline)
    _check_root(pipeline.root)
    return pipeline


def _bridge_options(
    local: bool, project: str | None, queue: str | None, max_attempts: int | None
) -> dict[str, Any]:
    """Return the build keyword arguments for a run; exit 2 if Jobmon cannot run here."""
    if local:
        return {}
    try:
        _jobmon_adapter.check_environment(DEFAULT_CLUSTER)
    except _jobmon_adapter.JobmonEnvironmentError as error:
        raise _DefinitionProblem(
            f"{error} Or pass --local to run the steps in this process."
        ) from None
    resources = {
        key: value
        for key, value in (("project", project), ("queue", queue))
        if value is not None
    }
    options: dict[str, Any] = {"jobmon": True, "jobmon_resources": resources}
    if max_attempts is not None:
        options["jobmon_max_attempts"] = max_attempts
    return options


def _check_single_producers(pipeline: _PipelineSnapshot) -> None:
    """Raise a click error naming each output that more than one step produces."""
    producers: dict[Path | str, list[str]] = {}
    nodes: dict[Path | str, pytask.PNode] = {}
    for task in pipeline.tasks:
        claimed: dict[Path | str, pytask.PNode] = {}
        for node in task.outputs.values():
            identity = _output_identity(node)
            if identity is not None:
                claimed.setdefault(identity, node)
        for identity, node in claimed.items():
            producers.setdefault(identity, []).append(task.name)
            nodes.setdefault(identity, node)
    lines = [
        f"Output {_output_key(pipeline, nodes[identity])!r} is produced by more than "
        f"one step: {_quoted(steps)}."
        for identity, steps in producers.items()
        if len(steps) > 1
    ]
    if lines:
        raise _DefinitionProblem("\n".join(lines))


def _output_identity(node: pytask.PNode) -> Path | str | None:
    """Return the resolved file of ``node``, else its name; ``None`` if it has neither."""
    path = _local_path(node)
    if path is not None:
        return path.resolve()
    return node.name or None


def _local_path(node: pytask.PNode) -> Path | None:
    """Return ``node``'s path if it is a PathNode on a :class:`pathlib.Path`."""
    if isinstance(node, pytask.PathNode) and isinstance(node.path, Path):
        return node.path
    return None


def _output_key(pipeline: _PipelineSnapshot, node: pytask.PNode) -> str:
    """Return the registry key of ``node``'s file, else the node's name, else its path."""
    path = _local_path(node)
    if path is None:
        return node.name
    if pipeline.registry is not None:
        key = pipeline.registry.key_for(path)
        if key is not None:
            return key
    return node.name or str(path)


def _check_root(root: Path) -> None:
    """Raise a click error unless ``root`` is or can become a writable directory."""
    # lexists, so that a dangling symlink, which mkdir fails on, counts as existing.
    existing = next(path for path in (root, *root.parents) if os.path.lexists(path))
    if not os.path.isdir(existing):
        reason = f"{existing} is not a directory"
    elif not os.access(existing, os.W_OK | os.X_OK):
        reason = f"{existing} is not writable"
    else:
        return
    raise _DefinitionProblem(f"Cannot use the pipeline root {root}: {reason}")


@contextmanager
def _verb_errors(
    pipeline: _PipelineSnapshot, requested: tuple[str, ...], param_hint: str
) -> Iterator[None]:
    """Report an error raised by a verb as a click error with the command's exit status.

    A :class:`~vivarium.dagger.frontend.execution.StepExited` is reported as ``Step
    '<name>' exited with code <code> before the build finished.``, with the code as
    ``repr`` gives it, and exits with status 1. A
    :class:`~vivarium.dagger.frontend.execution.RunRefused` is reported with its own
    message, which names the lock file and its holder, and exits with status 1. A
    :class:`~vivarium.dagger.frontend.execution.RunInterrupted` is reported by
    :func:`_interrupted` with its workflow id and exits with status 130. A
    :class:`SystemExit` raised because standard output was closed, as when a reader such
    as ``head`` stops reading, ends the command with status 1 and no message.
    """
    try:
        yield
    except RunRefused as error:
        raise click.ClickException(str(error)) from None
    except RunInterrupted as error:
        _interrupted(resumable=True, workflow_id=error.workflow_id)
    except StepFailed as error:
        raise click.ClickException(_failure_summary(error)) from None
    except PipelineError as error:
        raise _pipeline_error(pipeline, requested, param_hint, error) from None
    except OSError as error:
        raise click.ClickException(
            f"Could not read or write the pipeline's state under {pipeline.root}: {error}"
        ) from None
    except StepExited as error:
        raise click.ClickException(str(error)) from None
    except SystemExit as error:
        # rich's console raises SystemExit(1) while handling a BrokenPipeError.
        if isinstance(error.__context__, BrokenPipeError):
            _stdout_closed()
        # A step's own exit arrives as StepExited, so this one came from pytask itself.
        raise click.ClickException(
            f"The build exited with code {error.code!r} before it finished."
        ) from None


def _stdout_closed() -> NoReturn:
    """Exit with status 1 and no message because standard output was closed."""
    # As Python's docs advise for SIGPIPE, point standard output at devnull so that
    # flushing it at exit does not fail again.
    with suppress(OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    raise click.exceptions.Exit(1)


def _failure_summary(error: StepFailed) -> str:
    """Return a line per failed step, then the steps a failure kept from running."""
    blocked = [
        step
        for step, outcome in error.outcomes.items()
        if outcome == pytask.TaskOutcome.SKIP_PREVIOUS_FAILED
    ]
    lines = [str(error)]
    if blocked:
        lines.append(f"Not run because an upstream step failed: {_quoted(blocked)}.")
    return "\n".join(lines)


def _pipeline_error(
    pipeline: _PipelineSnapshot,
    requested: tuple[str, ...],
    param_hint: str,
    error: PipelineError,
) -> click.ClickException:
    """Return the click error for a PipelineError that is not a step failure."""
    message = str(error)
    steps = {task.name for task in pipeline.tasks}
    # The verbs report a shared step name before an unknown one, so with a shared name
    # the error is about the definition even when a requested step is also unknown.
    unique = len(steps) == len(pipeline.tasks)
    if unique and any(name not in steps for name in requested):
        return click.BadParameter(message, param_hint=param_hint)
    code = _stop_code(error)
    if code is not None:
        return click.ClickException(
            f"pytask stopped the build with exit code {code.name}; its output above "
            "shows why."
        )
    return _DefinitionProblem(message)


def _stop_code(error: PipelineError) -> pytask.ExitCode | None:
    """Return ``error``'s exit code unless it is ``None`` or a collection or DAG failure."""
    if error.exit_code in _DEFINITION_EXIT_CODES:
        return None
    return error.exit_code


def _quiet_preview(pipeline: _PipelineSnapshot, until: tuple[str, ...]) -> list[StepPreview]:
    """Return ``pipeline``'s plan; if pytask stops it, write pytask's output to stderr."""
    try:
        with pytask.console.capture() as captured:
            return _preview(pipeline, only=until or None)
    except PipelineError as error:
        if _stop_code(error) is not None:
            click.echo(captured.get(), err=True, nl=False)
        raise


def _interrupted(resumable: bool, workflow_id: int | None = None) -> NoReturn:
    """Report an interrupt, naming any Jobmon workflow, and exit with status 130."""
    message = "Interrupted."
    if workflow_id is not None:
        message += (
            f" The Jobmon workflow {workflow_id} was not cancelled; its jobs that already "
            "started may still be running."
        )
    if resumable:
        message += (
            " Steps that finished before the interrupt are recorded; run the same command "
            "again to continue."
        )
    click.echo(message, err=True)
    raise click.exceptions.Exit(_EXIT_INTERRUPTED)


def _print_plan(plan: list[StepPreview]) -> None:
    """Print one line per step of ``plan``, then how many of the steps would run."""
    for step in plan:
        click.echo(_plan_line(step))
    count = sum(1 for step in plan if step.would_run)
    noun = "step" if len(plan) == 1 else "steps"
    click.echo(f"{count} of {len(plan)} {noun} would run.")


def _plan_line(step: StepPreview) -> str:
    """Return the line ``dagger preview`` prints for ``step``."""
    if step.would_run:
        reasons = "; ".join(str(reason) for reason in step.reasons)
        return f"run  {step.name}: {reasons}" if reasons else f"run  {step.name}"
    reason = _SKIP_REASONS.get(step.outcome, step.outcome.description.lower())
    return f"skip {step.name}: {reason}"


def _step_block(pipeline: _PipelineSnapshot, task: Task) -> str:
    """Return the block of lines ``dagger show`` prints for ``task``."""
    kind = pipeline.kinds.get(task.name, "task")
    header = "task added with add_task" if kind == "task" else f"{kind} step"
    if task.env:
        header += f", env {task.env}"
    lines = [f"Step {task.name!r} ({header})"]
    for direction, nodes in (("input", task.inputs), ("output", task.outputs)):
        for name, node in nodes.items():
            if name in (CODE_ID_KEY, COMMAND_KEY):
                continue
            shown = _SHOWN_NAMES.get(name, name)
            label = _file_label(pipeline, node)
            text = label if shown is None else f"{shown}: {label}"
            lines.append(_show_line(direction, text))
    if isinstance(task.run, str):
        lines.append(_show_line("runs", task.run))
    else:
        target = task.run.target if isinstance(task.run, PythonStepRun) else task.run
        lines.append(_show_line("calls", _reference(target)))
    return "\n".join(lines)


def _show_line(label: str, text: str) -> str:
    """Return ``text`` after ``label``, with any further lines of ``text`` aligned under it."""
    indent = " " * (_SHOW_LABEL_WIDTH + 3)
    return f"  {label:<{_SHOW_LABEL_WIDTH}} " + text.replace("\n", "\n" + indent)


def _file_label(pipeline: _PipelineSnapshot, node: pytask.PNode) -> str:
    """Return how ``dagger show`` names ``node``: by key and path, by path, or by name."""
    path = _local_path(node)
    if path is None:
        return node.name
    key = None if pipeline.registry is None else pipeline.registry.key_for(path)
    if key is None:
        return str(path)
    if isinstance(node, RunDirectoryNode):
        return f"run directory {key!r} at {path}"
    return f"{key!r} at {path}"


def _reference(target: Callable[..., Any]) -> str:
    """Return ``target``'s import reference, or its repr if it has none."""
    try:
        return target_reference(target)
    except ValueError:
        return repr(target)


def _quoted(names: list[str]) -> str:
    """Join ``names``, each quoted."""
    return ", ".join(repr(name) for name in names)


def _user_traceback(error: BaseException) -> str:
    """Format ``error`` and its chain as Python does, without framework frames.

    If no frame of ``error`` itself is left, return its full traceback instead. A
    :class:`SyntaxError` with no frame left, as when the definition file itself does not
    parse, is the exception: it is formatted as
    :func:`traceback.format_exception_only` formats it, which shows the file, the line
    number, the source line with a caret, and the error.
    """
    if all(_is_framework(frame) for frame, _ in traceback.walk_tb(error.__traceback__)):
        if isinstance(error, SyntaxError):
            return "".join(traceback.format_exception_only(error))
        return "".join(traceback.format_exception(error))
    blocks: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None:
        seen.add(id(current))
        header = ""
        older: BaseException | None = None
        if current.__cause__ is not None and id(current.__cause__) not in seen:
            header, older = _CAUSE_HEADER, current.__cause__
        elif (
            current.__context__ is not None
            and not current.__suppress_context__
            and id(current.__context__) not in seen
        ):
            header, older = _CONTEXT_HEADER, current.__context__
        blocks.append(header + "".join(_trimmed(current)))
        current = older
    return "".join(reversed(blocks))


def _trimmed(error: BaseException) -> list[str]:
    """Format ``error`` without its chain or its framework frames.

    An exception group's sub-exceptions follow it, each formatted the same way, nested
    groups included, laid out as Python lays out an exception group.
    """
    if isinstance(error, BaseExceptionGroup):
        return _group_lines(error, level=1)
    frames = _kept_frames(error)
    header = ["Traceback (most recent call last):\n"] if frames else []
    return header + frames + traceback.format_exception_only(error)


def _group_lines(group: BaseExceptionGroup[BaseException], level: int) -> list[str]:
    """Format ``group``, ``level`` groups deep, and its sub-exceptions as Python does."""
    indent = "  " * level
    lines: list[str] = []
    frames = _kept_frames(group)
    if frames:
        # Python marks the first line of an outermost group with '+'.
        margin = "+" if level == 1 else "|"
        lines.append(f"{indent}{margin} Exception Group Traceback (most recent call last):\n")
    lines.extend(_indented(frames + traceback.format_exception_only(group), f"{indent}| "))
    for number, sub_error in enumerate(group.exceptions, start=1):
        corner = "+-" if number == 1 else "  "
        lines.append(f"{indent}{corner}+---------------- {number} ----------------\n")
        if isinstance(sub_error, BaseExceptionGroup):
            lines.extend(_group_lines(sub_error, level + 1))
        else:
            lines.extend(_indented(_trimmed(sub_error), f"{indent}  | "))
    # A nested group that comes last draws the closing line for both groups.
    if not isinstance(group.exceptions[-1], BaseExceptionGroup):
        lines.append(f"{indent}  +------------------------------------\n")
    return lines


def _kept_frames(error: BaseException) -> list[str]:
    """Format the frames of ``error``'s traceback that are not framework frames."""
    entries = traceback.extract_tb(error.__traceback__)
    frames = (frame for frame, _ in traceback.walk_tb(error.__traceback__))
    kept = [entry for entry, frame in zip(entries, frames) if not _is_framework(frame)]
    hidden = len(entries) - len(kept)
    lines = list(traceback.StackSummary.from_list(kept).format())
    if hidden:
        lines.append(f"  [{hidden} framework frame{'' if hidden == 1 else 's'} hidden]\n")
    return lines


def _indented(chunks: list[str], prefix: str) -> list[str]:
    """Return each line of ``chunks`` with ``prefix`` before it."""
    return [prefix + line for chunk in chunks for line in chunk.splitlines(keepends=True)]


def _is_framework(frame: FrameType) -> bool:
    """Return whether ``frame`` runs code of a module in ``_FRAMEWORK_MODULES``."""
    module = frame.f_globals.get("__name__")
    if not isinstance(module, str):
        return False
    return f"{module}.".startswith(tuple(f"{name}." for name in _FRAMEWORK_MODULES))
