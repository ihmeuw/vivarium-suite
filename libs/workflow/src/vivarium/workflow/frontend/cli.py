"""
=======
vwf CLI
=======

Run, preview, or force the steps of a pipeline definition from the command line.

Each subcommand loads a definition with
:func:`~vivarium.workflow.frontend.definition.load_pipeline`, checks that no artifact
has more than one producing step, and calls one verb of
:mod:`~vivarium.workflow.frontend.pipeline`. :doc:`/vwf` shows an example of each
subcommand and describes the exit status and how each error is reported. The preview
plan goes to standard output, as do pytask's progress table and failure reports.
``vwf``'s own messages and tracebacks go to standard error, and so does pytask's output
when pytask stops a preview's plan for a reason other than a definition error.

.. click:: vivarium.workflow.frontend.cli:vwf
   :prog: vwf
   :nested: full

"""

import os
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import NoReturn

import click
import pytask

from vivarium.workflow.backend.preview import StepPreview
from vivarium.workflow.frontend.definition import DefinitionError, load_pipeline
from vivarium.workflow.frontend.handoff import HandoffError
from vivarium.workflow.frontend.pipeline import Pipeline, PipelineError, StepFailed
from vivarium.workflow.frontend.pipeline import preview as preview_pipeline
from vivarium.workflow.frontend.pipeline import run as run_pipeline

_FRAMEWORK_MODULES = (
    "vivarium.workflow",
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
"""How ``vwf preview`` words the outcomes of steps that would not run."""

_EXIT_INTERRUPTED = 130
"""Exit status after an interrupt, as a shell reports a process stopped by SIGINT."""

_DEFINITION_EXIT_CODES = (pytask.ExitCode.COLLECTION_FAILED, pytask.ExitCode.DAG_FAILED)
"""pytask exit codes that mean the definition's steps cannot be collected or ordered."""


class _DefinitionProblem(click.ClickException):
    """An error in the definition or the pipeline it builds, reported with exit status 2."""

    exit_code = 2


@click.group()
def vwf() -> None:
    """Run, preview, or force the steps of a pipeline definition.

    DEFINITION is a Python file or module with a function that returns a Pipeline:
    path/to/file.py or package.module, optionally followed by :function (the default
    function is pipeline). A module must be installed or on PYTHONPATH.

    Exit status is 0 on success; 1 if a step failed or exited, or pytask stopped the
    build for another reason; 2 if the definition or the command is wrong and nothing
    ran; and 130 if the command was interrupted.
    """


@vwf.command()
@click.argument("definition")
@click.option(
    "--until",
    multiple=True,
    metavar="STEP",
    help="Run only STEP and the steps it depends on. Repeat to name more steps.",
)
def run(definition: str, until: tuple[str, ...]) -> None:
    """Run every stale step of DEFINITION."""
    try:
        pipeline = _load(definition)
        with _verb_errors(pipeline, until, "'--until'"):
            run_pipeline(pipeline, only=until or None)
    except KeyboardInterrupt:
        _interrupted(resumable=True)


@vwf.command()
@click.argument("definition")
@click.option(
    "--until",
    multiple=True,
    metavar="STEP",
    help="Plan only STEP and the steps it depends on. Repeat to name more steps.",
)
def preview(definition: str, until: tuple[str, ...]) -> None:
    """Show which steps of DEFINITION would run, and why.

    Nothing runs and no product is written.
    """
    try:
        pipeline = _load(definition)
        with _verb_errors(pipeline, until, "'--until'"):
            plan = _quiet_preview(pipeline, until)
        _print_plan(plan)
    except KeyboardInterrupt:
        _interrupted(resumable=False)


@vwf.command()
@click.argument("definition")
@click.argument("steps", nargs=-1, required=True, metavar="STEP...")
def force(definition: str, steps: tuple[str, ...]) -> None:
    """Run each STEP even if it is current.

    Every other stale step of DEFINITION runs too.
    """
    try:
        pipeline = _load(definition)
        with _verb_errors(pipeline, steps, "'STEP...'"):
            run_pipeline(pipeline, force=steps)
    except KeyboardInterrupt:
        _interrupted(resumable=True)


def _load(definition: str) -> Pipeline:
    """Load ``definition``, check its producers and root; report failures as click errors."""
    try:
        pipeline = load_pipeline(definition)
    except (DefinitionError, HandoffError) as error:
        raise _DefinitionProblem(str(error)) from None
    except (Exception, SystemExit) as error:
        click.echo(_user_traceback(error), err=True, nl=False)
        raise _DefinitionProblem(
            f"Could not load the definition {definition!r}; the traceback above shows why."
        ) from None
    _check_single_producers(pipeline)
    _check_root(pipeline.root)
    return pipeline


def _check_single_producers(pipeline: Pipeline) -> None:
    """Raise a click error naming each artifact that more than one step produces."""
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
        f"Artifact {_artifact_key(pipeline, nodes[identity])!r} is produced by more than "
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


def _artifact_key(pipeline: Pipeline, node: pytask.PNode) -> str:
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
    pipeline: Pipeline, requested: tuple[str, ...], param_hint: str
) -> Iterator[None]:
    """Report an error raised by a verb as a click error with vwf's exit status."""
    try:
        yield
    except StepFailed as error:
        raise click.ClickException(_failure_summary(error)) from None
    except PipelineError as error:
        raise _pipeline_error(pipeline, requested, param_hint, error) from None
    except OSError as error:
        raise click.ClickException(
            f"Could not read or write the pipeline's state under {pipeline.root}: {error}"
        ) from None
    except SystemExit as error:
        raise click.ClickException(
            f"A step exited with code {error.code!r} before the build finished."
        ) from None


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
    pipeline: Pipeline, requested: tuple[str, ...], param_hint: str, error: PipelineError
) -> click.ClickException:
    """Return the click error for a PipelineError that is not a step failure."""
    message = str(error)
    steps = {task.name for task in pipeline.tasks}
    if any(name not in steps for name in requested):
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


def _quiet_preview(pipeline: Pipeline, until: tuple[str, ...]) -> list[StepPreview]:
    """Return ``pipeline``'s plan; if pytask stops it, write pytask's output to stderr."""
    try:
        with pytask.console.capture() as captured:
            return preview_pipeline(pipeline, only=until or None)
    except PipelineError as error:
        if _stop_code(error) is not None:
            click.echo(captured.get(), err=True, nl=False)
        raise


def _interrupted(resumable: bool) -> NoReturn:
    """Report an interrupt and exit with status 130."""
    message = "Interrupted."
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
    """Return the line ``vwf preview`` prints for ``step``."""
    if step.would_run:
        reasons = "; ".join(str(reason) for reason in step.reasons)
        return f"run  {step.name}: {reasons}" if reasons else f"run  {step.name}"
    reason = _SKIP_REASONS.get(step.outcome, step.outcome.description.lower())
    return f"skip {step.name}: {reason}"


def _quoted(names: list[str]) -> str:
    """Join ``names``, each quoted."""
    return ", ".join(repr(name) for name in names)


def _user_traceback(error: BaseException) -> str:
    """Format ``error`` and its chain as Python does, without framework frames.

    If no frame of ``error`` itself is left, return its full traceback instead.
    """
    if all(_is_framework(frame) for frame, _ in traceback.walk_tb(error.__traceback__)):
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
    """Format ``error`` without its chain or its framework frames."""
    entries = traceback.extract_tb(error.__traceback__)
    if not entries:
        return traceback.format_exception_only(error)
    frames = (frame for frame, _ in traceback.walk_tb(error.__traceback__))
    kept = [entry for entry, frame in zip(entries, frames) if not _is_framework(frame)]
    hidden = len(entries) - len(kept)
    lines = ["Traceback (most recent call last):\n"]
    lines.extend(traceback.StackSummary.from_list(kept).format())
    if hidden:
        lines.append(f"  [{hidden} framework frame{'' if hidden == 1 else 's'} hidden]\n")
    lines.extend(traceback.format_exception_only(error))
    return lines


def _is_framework(frame: FrameType) -> bool:
    """Return whether ``frame`` runs code of a module in ``_FRAMEWORK_MODULES``."""
    module = frame.f_globals.get("__name__")
    if not isinstance(module, str):
        return False
    return f"{module}.".startswith(tuple(f"{name}." for name in _FRAMEWORK_MODULES))
