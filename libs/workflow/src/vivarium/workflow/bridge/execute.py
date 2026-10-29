"""Run the stale steps of a build as one Jobmon workflow.

:mod:`~vivarium.workflow.bridge.plugin` registers this module for a build that sets
``jobmon=True``. Its :func:`pytask_execute_build` hook takes the place of pytask's own
loop, so no step's body runs in the build's process. The hook plans the build with
pytask's own dry-run protocol, turns every stale step into a recipe with
:func:`~vivarium.workflow.bridge.payload.payload_for`, and runs the steps as one Jobmon
workflow in which each step waits for its nearest stale ancestors.

Before pytask runs a step locally, it decides whether the step is stale, checks that the
step's inputs exist, and creates the parent directory of each product. For a stale step
with no stale ancestor, planning does all three, so it can create product directories even
for a step that the bridge then refuses. pytask marks every step after a stale step to run
without checking its inputs. Unless such a step is deselected or persisted, it is stale
and is submitted, even when a local build would skip it because its stale ancestor wrote
the same content again, and a missing input of the step is found only when the step runs
on a node. The bridge creates the product directories of every stale step before it
submits anything, so a step whose product directory does not exist yet runs on a node as
it does locally. pytask's ``max_failures`` and ``stop_after_first_failure`` do not apply
to the steps that run through Jobmon.

Planning goes through pytask's per-task protocol, so pytask's table lists each stale step
as "would be executed". The bridge does not yet record the results of the steps that ran,
so a bridged build reports only the steps that it did not submit.
"""

import inspect
import json
import secrets
import shlex
import sys
from collections.abc import Mapping, MutableMapping, Set
from datetime import datetime
from pathlib import Path
from typing import Any

from pytask import (
    DirectoryNode,
    ExecutionError,
    ExecutionReport,
    PNode,
    PPathNode,
    PProvisionalNode,
    PTask,
    Session,
    TaskOutcome,
    console,
    hookimpl,
)

from vivarium.workflow.backend.build import TASK_KEY
from vivarium.workflow.backend.env import resolve_env_bin_path
from vivarium.workflow.backend.task import Task
from vivarium.workflow.bridge import _jobmon_adapter
from vivarium.workflow.bridge._jobmon_adapter import PlanEntry
from vivarium.workflow.bridge.payload import Payload, payload_for
from vivarium.workflow.bridge.runner import RESULT_SUFFIX, runner_command

RUNS_DIR = Path(".pytask", "jobmon")
"""The directory, relative to a build's root, that holds one directory per bridge run."""

PAYLOAD_SUFFIX = ".payload.json"
"""The suffix of a step's recipe file in a run directory. The name before it is the
step's pytask signature, as for the step's result file, whose suffix is
:data:`~vivarium.workflow.bridge.runner.RESULT_SUFFIX`."""


@hookimpl(tryfirst=True)
def pytask_execute_build(session: Session) -> bool:
    """Run the build's stale steps as one Jobmon workflow instead of in this process.

    The hook first plans the build with pytask's dry-run protocol, in topological order
    with the steps of each wave sorted by name, and appends the report of every step
    that is not stale to ``session.execution_reports``: unchanged, skipped (including
    steps that a pytask expression or marker expression deselects), persisted, or failed
    at plan time, such as a step whose input file is missing, together with the steps
    after it. ``session.config["dry_run"]`` is ``True`` only while planning and is
    ``False`` again afterwards, even if planning raises. With nothing stale, the hook
    stops there: it creates no run directory and binds no workflow. If pytask asks the
    build to stop during planning, after an interrupt or once plan-time failures reach
    ``max_failures`` (one with ``stop_after_first_failure``), planning ends with that
    step, the steps it has not reached get no report, and the hook stops there too: it
    creates no run directory and submits none of the stale steps it has already planned.

    Next it builds a recipe for every stale step, resolving each declared env once for
    the whole plan. If any stale step cannot run on a compute node, because
    :func:`~vivarium.workflow.bridge.payload.payload_for` refuses it (for example, its
    target cannot be named by import reference), the step was not built by
    :func:`~vivarium.workflow.backend.build.build`, or its arguments cannot be loaded on
    the controller, each such step is reported as FAIL with the reason as a plain
    message. No recipe, run directory or product is then written and nothing is
    submitted, although planning, like
    :func:`~vivarium.workflow.backend.preview.preview`, can already have created product
    directories.

    Otherwise it checks the Jobmon environment, creates a new run directory
    ``<root>/.pytask/jobmon/<YYYYmmdd-HHMMSS>-<6 hex digits>/``, writes each recipe there
    as ``<signature>.payload.json``, creates the parent directory of each stale step's
    products, and binds and runs one workflow. Its entries are the stale steps in plan
    order, named by step and carrying each step's
    :attr:`~vivarium.workflow.backend.task.Task.resources`. Each entry waits for the
    step's nearest stale ancestors, found by walking back through data nodes and through
    steps that are not stale. Each entry's command runs
    :func:`~vivarium.workflow.bridge.runner.runner_command` for its recipe with this
    Python environment's ``bin`` directories first on ``PATH``, and the runner writes
    the result to ``<signature>.result.json`` beside the recipe. The workflow is named
    after the root directory and takes its cluster, default resources, attempts and
    resource scales from the configuration keys that
    :mod:`~vivarium.workflow.bridge.plugin` describes.

    Parameters
    ----------
    session
        The build's pytask session.

    Returns
    -------
        ``True``, so pytask's own loop runs no step.

    Raises
    ------
    pytask.ExecutionError
        If the Jobmon environment check fails, or if Jobmon refuses to bind the
        workflow, which the adapter reports as
        :class:`~vivarium.workflow.bridge._jobmon_adapter.JobmonBindError`, for example
        because neither ``jobmon_resources`` nor a step's resources name a queue. The
        message is printed through pytask's console first, so the build fails with that
        message and no traceback. For a refused bind, the message names
        ``jobmon_cluster`` and the compute resources to check and quotes the adapter's
        message, which gives the type and message of Jobmon's error. The run directory,
        its recipes and the product directories have then already been created, but no
        step has run.
    """
    stale = _plan(session)
    # pytask's own loop also stops once max_failures is reached or a task is interrupted.
    if not stale or session.should_stop:
        return True
    payloads = _payloads(session, stale, env_paths={})
    if payloads is None:
        return True
    config = session.config
    cluster: str = config["jobmon_cluster"]
    # Call the adapter through its module, so that harness.FakeJobmon can replace it.
    try:
        _jobmon_adapter.check_environment(cluster)
    except _jobmon_adapter.JobmonEnvironmentError as error:
        raise _failure(str(error)) from error
    root = Path(config["root"])
    workdir = _new_run_directory(root)
    plan = _compile(session, stale, payloads, workdir)
    _prepare_products(session, stale)
    try:
        jobmon_run = _jobmon_adapter.bind(
            plan,
            cluster=cluster,
            default_resources=config["jobmon_resources"],
            max_attempts=config["jobmon_max_attempts"],
            resource_scales=config["jobmon_resource_scales"],
            workflow_name=root.name,
        )
    except _jobmon_adapter.JobmonBindError as error:
        raise _failure(
            f"Jobmon could not bind the workflow on the {cluster!r} cluster. Check "
            "jobmon_cluster and the compute resources in jobmon_resources and in each "
            f"step's resources. Jobmon reported: {error}"
        ) from error
    _jobmon_adapter.run(jobmon_run)
    # TODO(MIC-7529): record each step's result from <signature>.result.json in workdir.
    return True


def _plan(session: Session) -> list[PTask]:
    """Dry-run the tasks, append each report that is not stale, and return the stale tasks."""
    stale: list[PTask] = []
    session.config["dry_run"] = True
    try:
        for task in _topological_order(session):
            report = session.hook.pytask_execute_task_protocol(session=session, task=task)
            if report.outcome == TaskOutcome.WOULD_BE_EXECUTED:
                stale.append(task)
            else:
                session.execution_reports.append(report)
            if session.should_stop:
                break
    finally:
        session.config["dry_run"] = False
    return stale


def _topological_order(session: Session) -> list[PTask]:
    """Return the session's tasks in topological waves, each wave sorted by name."""
    tasks = {task.signature: task for task in session.tasks}
    waiting = {
        signature: session.dag.ancestors(signature) & tasks.keys() for signature in tasks
    }
    ordered: list[PTask] = []
    while waiting:
        wave = sorted(
            (tasks[signature] for signature, ancestors in waiting.items() if not ancestors),
            key=lambda task: task.name,
        )
        if not wave:
            raise ValueError("The tasks' dependencies form a cycle.")
        ordered.extend(wave)
        done = {task.signature for task in wave}
        waiting = {
            signature: ancestors - done
            for signature, ancestors in waiting.items()
            if signature not in done
        }
    return ordered


def _kwargs(task: PTask) -> dict[str, Any]:
    """Load the arguments that pytask would pass to the task's function."""
    parameters = inspect.signature(task.function).parameters
    kwargs = {name: _load(node, is_product=False) for name, node in task.depends_on.items()}
    for name, node in task.produces.items():
        if name in parameters:
            kwargs[name] = _load(node, is_product=True)
    return kwargs


def _load(node: object, *, is_product: bool) -> Any:
    """Load a single node as pytask does when it calls a task's function."""
    if not isinstance(node, (PNode, PProvisionalNode)):
        raise TypeError(f"Expected a single node, got {type(node).__name__}.")
    return node.load(is_product=is_product)


def _payloads(
    session: Session, stale: list[PTask], env_paths: MutableMapping[str, str]
) -> dict[str, Payload] | None:
    """Return each stale task's recipe, or report the refused ones as FAIL and return None."""
    payloads: dict[str, Payload] = {}
    refusals: list[tuple[PTask, str]] = []
    for pytask_task in stale:
        refused = f"Task '{pytask_task.name}': cannot run on a compute node"
        task = pytask_task.attributes.get(TASK_KEY)
        if not isinstance(task, Task):
            reason = (
                f"{refused} because it was not built by vivarium.workflow. Only the steps "
                "that vivarium.workflow.backend.build.build builds can run through Jobmon."
            )
            refusals.append((pytask_task, reason))
            continue
        try:
            kwargs = _kwargs(pytask_task)
        except Exception as error:
            reason = (
                f"{refused} because its arguments could not be loaded on the controller "
                f"({type(error).__name__}: {error})."
            )
            refusals.append((pytask_task, reason))
            continue
        try:
            payloads[pytask_task.signature] = payload_for(task, kwargs, env_paths=env_paths)
        except ValueError as error:
            refusals.append((pytask_task, str(error)))
    for pytask_task, reason in refusals:
        _reject(session, pytask_task, reason)
    return None if refusals else payloads


def _reject(session: Session, task: PTask, reason: str) -> None:
    """Report the task as FAIL with ``reason``, without a traceback or chained error."""
    error = ValueError(reason)
    report = ExecutionReport.from_task_and_exception(task, (ValueError, error, None))
    # Planning already logged the task's end, and the live table needs a start first.
    session.hook.pytask_execute_task_log_start(session=session, task=task)
    session.hook.pytask_execute_task_process_report(session=session, report=report)
    session.hook.pytask_execute_task_log_end(session=session, task=task, report=report)
    session.execution_reports.append(report)


def _failure(message: str) -> ExecutionError:
    """Print ``message`` in pytask's failure style and return an ExecutionError with it."""
    console.print(message, style="failed", markup=False, highlight=False, soft_wrap=True)
    return ExecutionError(message)


def _new_run_directory(root: Path) -> Path:
    """Create and return a new, uniquely named run directory under ``root / RUNS_DIR``."""
    runs = root / RUNS_DIR
    runs.mkdir(parents=True, exist_ok=True)
    directory = runs / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    directory.mkdir(exist_ok=False)
    return directory


def _prepare_products(session: Session, stale: list[PTask]) -> None:
    """Create the stale tasks' product directories, as pytask's task setup does."""
    for task in stale:
        for signature in session.dag.successors(task.signature):
            node = session.dag.nodes[signature]
            if isinstance(node, PPathNode):
                node.path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(node, DirectoryNode) and node.root_dir:
                node.root_dir.mkdir(parents=True, exist_ok=True)


def _stale_upstreams(session: Session, signature: str, stale: Set[str]) -> set[str]:
    """Return the nearest stale tasks upstream of ``signature``."""
    found: set[str] = set()
    seen: set[str] = set()
    pending = list(session.dag.predecessors(signature))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in stale:
            found.add(current)
        else:
            pending.extend(session.dag.predecessors(current))
    return found


def _node_command(payload_path: Path, result_path: Path) -> str:
    """Return a recipe's runner command, with this env's bin directories first on PATH."""
    # Jobmon's Slurm plugin puts the system bin directories ahead of the build's PATH, so
    # without this a command step with no env could find another python on the node.
    bin_path = shlex.quote(resolve_env_bin_path(sys.prefix))
    return f"PATH={bin_path}:$PATH {runner_command(payload_path, result_path)}"


def _compile(
    session: Session, stale: list[PTask], payloads: Mapping[str, Payload], workdir: Path
) -> list[PlanEntry]:
    """Write the recipes into ``workdir`` and return a plan entry per stale task, in order."""
    order = [task.signature for task in stale]
    stale_signatures = set(order)
    plan: list[PlanEntry] = []
    for task in stale:
        payload_path = workdir / f"{task.signature}{PAYLOAD_SUFFIX}"
        result_path = workdir / f"{task.signature}{RESULT_SUFFIX}"
        payload_path.write_text(json.dumps(payloads[task.signature]), encoding="utf-8")
        upstreams = _stale_upstreams(session, task.signature, stale_signatures)
        plan.append(
            PlanEntry(
                signature=task.signature,
                name=task.name,
                command=_node_command(payload_path, result_path),
                upstreams=tuple(signature for signature in order if signature in upstreams),
                resources=dict(task.attributes[TASK_KEY].resources),
            )
        )
    return plan
