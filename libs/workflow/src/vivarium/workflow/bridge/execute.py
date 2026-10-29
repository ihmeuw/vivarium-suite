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
as "would be executed", and lists it again when its result is recorded. The summary counts
each step once.

After the workflow ends, the hook records each step's result from the file that the node
runner wrote. Recording runs pytask's own per-task hooks in the build's process, so pytask
checks and hashes each step's products and writes its lockfile entry exactly as a local
build does, and a later build skips a step whose inputs and code are unchanged. A step
that failed on its node is reported as FAIL with :class:`RemoteTaskFailed`. Nothing is
recorded while the workflow runs, so pytask's table shows no step finishing until the
whole workflow ends, and a build killed before then records no step
(:mod:`~vivarium.workflow.bridge.plugin` says what that means for the build's time limit).
"""

import inspect
import json
import os
import secrets
import shlex
import sys
from collections.abc import Mapping, MutableMapping, Sequence, Set
from datetime import datetime
from pathlib import Path
from typing import Any, cast

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
from vivarium.workflow.bridge.runner import (
    RESULT_SUFFIX,
    RunResult,
    read_result,
    runner_command,
)

RUNS_DIR = Path(".pytask", "jobmon")
"""The directory, relative to a build's root, that holds one directory per bridge run."""

PAYLOAD_SUFFIX = ".payload.json"
"""The suffix of a step's recipe file in a run directory. The name before it is the
step's pytask signature, as for the step's result file, whose suffix is
:data:`~vivarium.workflow.bridge.runner.RESULT_SUFFIX`."""

_RESULT_FIELDS: Mapping[str, tuple[type[object], ...]] = {
    "success": (bool,),
    "exc": (str, type(None)),
    "exc_type": (str, type(None)),
    "exc_message": (str, type(None)),
    "stdout": (str,),
    "stderr": (str,),
}
"""The types that recording accepts for each field of a result that it reads."""


class RemoteTaskFailed(Exception):
    """The error in the FAIL report of a step that failed on its compute node.

    The bridge builds it from the step's result file, so it carries what the node runner
    wrote there. Its message names the step and holds the node's traceback when the node
    sent one. When the step's result file could not be read, the message names the file
    and the error instead, and :attr:`exc_type`, :attr:`exc_message` and
    :attr:`node_traceback` are ``None``. The report has no traceback of its own, because
    no frame in the build's process raised the error. When the node sent output, it is
    also in the report's sections, where pytask shows it as "Captured stdout during
    call" and "Captured stderr during call".

    Code that describes a failure without importing the bridge reads the
    :attr:`exc_type` and :attr:`exc_message` attributes, so their names and meaning stay
    fixed.

    Parameters
    ----------
    message
        The message that pytask shows in the step's failure report.
    exc_type
        Kept as :attr:`exc_type`.
    exc_message
        Kept as :attr:`exc_message`.
    node_traceback
        Kept as :attr:`node_traceback`.
    stdout
        Kept as :attr:`stdout`.
    stderr
        Kept as :attr:`stderr`.
    """

    exc_type: str | None
    """The class of the exception that the step raised on its node, as
    ``"module.QualName"``, or only the name for a built-in exception, as in the result's
    ``exc_type``. ``None`` when no exception from the node is known."""
    exc_message: str | None
    """The text of the exception that the step raised on its node, or ``None`` when no
    exception from the node is known."""
    node_traceback: str | None
    """The formatted traceback from the node, which starts in the step's own code, or
    ``None`` when the node sent none."""
    stdout: str
    """The tail of what the step wrote to standard output on its node."""
    stderr: str
    """The tail of what the step wrote to standard error on its node."""

    def __init__(
        self,
        message: str,
        *,
        exc_type: str | None = None,
        exc_message: str | None = None,
        node_traceback: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exc_type = exc_type
        self.exc_message = exc_message
        self.node_traceback = node_traceback
        self.stdout = stdout
        self.stderr = stderr


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

    When the workflow ends, the hook records the result of each stale step whose result
    file is in the run directory, in plan order, and appends its report to
    ``session.execution_reports``. Recording runs, in this process, the per-task hooks
    that a local build runs around a step's body, except setup and the call itself: it
    logs the step's start, checks that each of its products exists, hashes its
    dependencies and products into the lockfile, and logs its outcome. The output that
    the step wrote on its node is added to the report's sections as captured output of
    the call. A step whose node reports success but which did not write every product is
    a FAIL with pytask's own message naming the missing products, as in a local build. A
    step that failed on its node is a FAIL whose exception is :class:`RemoteTaskFailed`,
    with no lockfile entry, and pytask marks every step after it as skipped because an
    ancestor failed. A step whose result file cannot be read is a FAIL in the same way,
    and its :class:`RemoteTaskFailed` names the file. Every result is recorded, also
    after ``max_failures`` steps have failed. A stale step with no result file, such as
    one that Jobmon did not run because a step before it failed, gets no report.

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
        step has run. Also if Jobmon's ``Workflow.run`` raised, or stopped with, an
        ``Exception`` before the workflow ended, which the adapter keeps as the run's
        :attr:`~vivarium.workflow.bridge._jobmon_adapter.JobmonRun.error`. The results
        already in the run directory are recorded first. The message, printed the same
        way, says that the steps without a result were not recorded and gives the type
        and message of Jobmon's error.
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
    # TODO(MIC-7530): settle the steps that this returns, so every planned step gets a report.
    _record_available(session, stale, workdir)
    if jobmon_run.error is not None:
        run_error = jobmon_run.error
        raise _failure(
            "Jobmon stopped the workflow with an error before it ended, so the steps "
            "without a result were not recorded and will run again in the next build. "
            f"Jobmon raised {type(run_error).__name__}: {run_error}"
        ) from run_error
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


def _record_available(session: Session, stale: Sequence[PTask], workdir: Path) -> list[PTask]:
    """Record the stale tasks that have a result file; return the others in order."""
    listed = set(os.listdir(workdir))
    unfinished: list[PTask] = []
    for task in stale:
        name = f"{task.signature}{RESULT_SUFFIX}"
        if name not in listed:
            unfinished.append(task)
            continue
        path = workdir / name
        try:
            result = _read_checked(path)
        except (OSError, ValueError) as error:
            _record_unreadable(session, task, path, error)
        else:
            _record(session, task, result)
    return unfinished


def _read_checked(path: Path) -> RunResult:
    """Read the result at ``path`` and check each field that recording reads."""
    result: object = read_result(path)
    if not isinstance(result, dict):
        raise ValueError("the file does not hold a JSON object")
    for key, accepted in _RESULT_FIELDS.items():
        if key not in result or not isinstance(result[key], accepted):
            raise ValueError(f"the result's {key!r} field is missing or of the wrong type")
    return cast(RunResult, result)


def _record_unreadable(session: Session, task: PTask, path: Path, error: Exception) -> None:
    """Report ``task`` as FAIL because its result file at ``path`` could not be read."""
    message = (
        f"Step '{task.name}' could not be recorded, because its result file {path} could "
        f"not be read ({type(error).__name__}: {error})."
    )
    session.hook.pytask_execute_task_log_start(session=session, task=task)
    _finish(session, task, _remote_failure(task, RemoteTaskFailed(message)))


def _record(session: Session, task: PTask, result: RunResult) -> None:
    """Report ``task`` from its node result through pytask's per-task hooks."""
    __tracebackhide__ = True
    if result["stdout"]:
        task.report_sections.append(("call", "stdout", result["stdout"]))
    if result["stderr"]:
        task.report_sections.append(("call", "stderr", result["stderr"]))
    session.hook.pytask_execute_task_log_start(session=session, task=task)
    if result["success"]:
        # pytask's tracebacks start at the first frame that is neither hidden nor pytask's,
        # so teardown is called from this hidden frame and not from a helper.
        try:
            session.hook.pytask_execute_task_teardown(session=session, task=task)
        except Exception:
            report = ExecutionReport.from_task_and_exception(task, sys.exc_info())
        else:
            report = ExecutionReport.from_task(task)
    else:
        message = f"Step '{task.name}' failed on its compute node."
        if result["exc"] is not None:
            message = f"{message}\n\n{result['exc']}"
        error = RemoteTaskFailed(
            message,
            exc_type=result["exc_type"],
            exc_message=result["exc_message"],
            node_traceback=result["exc"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )
        report = _remote_failure(task, error)
    _finish(session, task, report)


def _remote_failure(task: PTask, error: RemoteTaskFailed) -> ExecutionReport:
    """Return a FAIL report of ``task`` that holds ``error`` with no traceback."""
    return ExecutionReport.from_task_and_exception(task, (RemoteTaskFailed, error, None))


def _finish(session: Session, task: PTask, report: ExecutionReport) -> None:
    """Process and log ``report`` as pytask does after a task's teardown, and keep it."""
    session.hook.pytask_execute_task_process_report(session=session, report=report)
    session.hook.pytask_execute_task_log_end(session=session, task=task, report=report)
    session.execution_reports.append(report)
