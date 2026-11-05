"""Run the stale steps of a build as one Jobmon workflow.

:mod:`~vivarium.dagger.bridge.plugin` registers this module for a build that sets
``jobmon=True``. Its :func:`pytask_execute_build` hook takes the place of pytask's own
loop, so no step's body runs in the build's process. The hook plans the build with
pytask's own dry-run protocol, turns every stale step into a recipe with
:func:`~vivarium.dagger.bridge.payload.payload_for`, and runs the steps as one Jobmon
workflow in which each step waits for its nearest stale ancestors.

Before pytask runs a step locally, it decides whether the step is stale, checks that the
step's inputs exist, and creates the parent directory of each product. For a stale step
with no stale ancestor, planning does all three, so it can create product directories even
for a step that the bridge then refuses or, with ``force``, fails for a missing input.
pytask marks every step after a stale step to run without checking its inputs. Unless
such a step is deselected or persisted, it is stale, and the bridge submits it even when a
local build would skip it because its stale ancestor wrote the same content again. With
``force``, planning checks the inputs of no step.
Before it writes anything, the bridge therefore checks the inputs of every stale step
itself: each input with a file path must exist unless a stale step produces it. A stale
step with a missing input is reported as FAIL and every stale step after it as
SKIP_PREVIOUS_FAILED, as a local build reports them, and the bridge submits none of these
steps. It creates the product directories of every step that it submits before it submits
anything, so a step whose product directory does not exist yet runs on a node as it does
locally. pytask's ``max_failures`` and ``stop_after_first_failure`` apply to planning
and to this input check, so a failure that makes pytask stop the build ends it there and
nothing is submitted. They do not apply to the steps that run through Jobmon.

Planning goes through pytask's per-task protocol, so pytask's table lists each stale step
as "would be executed", and lists it again when its result is recorded. The summary counts
each step once.

After the workflow ends, the hook records each step's result from the file that the node
runner wrote. Recording runs pytask's own per-task hooks in the build's process, so pytask
checks and hashes each step's products and writes its lockfile entry exactly as a local
build does, and a later build skips a step whose inputs and code are unchanged. A step
that failed on its node is reported as FAIL with :class:`RemoteTaskFailed`. Nothing is
recorded while the workflow runs, so pytask's table shows no step finishing until the
whole workflow ends.

The hook then settles every submitted step that still has no report, so each step of the
plan ends with exactly one report. A step after a failed step is reported as
SKIP_PREVIOUS_FAILED, and any other step without a result is reported as FAIL with a
:class:`RemoteTaskFailed` that gives the step's Jobmon status and the workflow's id and
status. Settlement runs no step's body. SIGTERM while the bridge binds or runs the
Jobmon workflow becomes :class:`BridgeInterrupted`: the hook then stops waiting for the
workflow, records the results already written, settles the rest and returns, so pytask
still writes its lockfile. The hook does not cancel the workflow, so its jobs that are
already on compute nodes can still be running. Each run also keeps a state file (see
:mod:`~vivarium.dagger.bridge.state`) that gives its Jobmon workflow id even when the
build is killed. To continue a stopped run, build again: the next build plans from the
lockfile, so it runs every step that was not recorded as successful and every step after
one, and skips any other recorded step whose inputs and code identity are unchanged.
With the default code identity no step counts as unchanged, so every step runs again.
:mod:`~vivarium.dagger.bridge.plugin` says what this means for the build's time limit.

The hook does all of this while it holds the run lock on the build's root
(:func:`~vivarium.dagger.backend.lock.run_lock`), so two builds with ``jobmon=True`` never
plan, run or record on one root at once. A build that finds the lock held stops before it
plans, fails with a message that names the run that holds the lock, and leaves that run's
lockfile and journal as they are.
"""

import inspect
import json
import os
import secrets
import shlex
import signal
import sys
import threading
from collections.abc import Iterator, Mapping, MutableMapping, Sequence, Set
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, cast

from pytask import (
    DirectoryNode,
    ExecutionError,
    ExecutionReport,
    NodeNotFoundError,
    PNode,
    PPathNode,
    PProvisionalNode,
    PTask,
    Session,
    SkippedAncestorFailed,
    TaskOutcome,
    console,
    get_marks,
    hookimpl,
)

from vivarium.dagger.backend.build import TASK_KEY
from vivarium.dagger.backend.env import resolve_env_bin_path
from vivarium.dagger.backend.lock import RUN_LOCK_NAME, RUN_REFUSED_KEY, RunLocked, run_lock
from vivarium.dagger.backend.task import Task
from vivarium.dagger.bridge import _jobmon_adapter
from vivarium.dagger.bridge._jobmon_adapter import JobmonRun, PlanEntry
from vivarium.dagger.bridge.payload import Payload, payload_for
from vivarium.dagger.bridge.runner import (
    RESULT_SUFFIX,
    RunResult,
    capture_paths,
    read_result,
    read_tail,
    runner_command,
)
from vivarium.dagger.bridge.state import (
    RUN_STATE_KEY,
    RUNS_DIR,
    RunState,
    latest_run_state,
    write_run_state,
)

INTERRUPTED_EXIT_CODE = 143
"""The exit code of :class:`BridgeInterrupted`, which is 128 plus SIGTERM's number, as a
shell reports a process that SIGTERM ended."""

PAYLOAD_SUFFIX = ".payload.json"
"""The suffix of a step's recipe file in a run directory. The name before it is the
step's pytask signature, as for the step's result file, whose suffix is
:data:`~vivarium.dagger.bridge.runner.RESULT_SUFFIX`."""

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


class BridgeInterrupted(SystemExit):
    """Raised on SIGTERM while the bridge binds or runs the Jobmon workflow.

    The bridge raises it in the build's main thread. It subclasses :class:`SystemExit`,
    not :class:`Exception`, so that it passes through Jobmon's handlers, which catch
    ``Exception``, and out of Jobmon's event loop. The bridge catches it itself, records
    and settles the run, and returns, so it does not end the build. Its exit code is
    :data:`INTERRUPTED_EXIT_CODE`.
    """


@hookimpl(tryfirst=True)
def pytask_execute_build(session: Session) -> bool:
    """Run the build's stale steps as one Jobmon workflow instead of in this process.

    Before anything else, the hook takes the run lock with
    :func:`~vivarium.dagger.backend.lock.run_lock` on ``<root>/run.lock``
    (:data:`~vivarium.dagger.backend.lock.RUN_LOCK_NAME` in the build's root, beside
    ``pytask.lock``), and it holds the lock until it returns or raises, so the lock covers
    every step described below, from planning to the final state file. Right after it
    takes the lock, it reads pytask's lockfile and its journal again into
    ``session.config["lockfile_state"]``, so that it plans from what an earlier build
    recorded up to the moment that build released the lock, and not from what pytask read
    when this build started. The lock is released however the hook ends, also when a
    ``KeyboardInterrupt`` or a ``SystemExit`` passes through it. pytask writes its
    lockfile from that state in its ``pytask_unconfigure`` hook, a few milliseconds after
    this hook has released the lock.

    If another process holds the lock, the hook stops at once: it plans nothing, appends
    no report, binds nothing, creates no run directory and writes no state file. It sets
    ``session.config["lockfile_state"]`` to ``None``, so that pytask does not rewrite
    ``pytask.lock`` or delete its journal, both of which belong to the run that holds the
    lock. It leaves the :class:`~vivarium.dagger.backend.lock.RunLocked` in
    ``session.config`` under :data:`~vivarium.dagger.backend.lock.RUN_REFUSED_KEY`, prints
    the exception's message, which names the lock file and the holder's user, pid, host
    and start time, through pytask's console, and raises (see Raises). The build then
    ends with ``ExitCode.FAILED`` and no execution reports.

    With the lock held, the hook first plans the build with pytask's dry-run protocol, in
    topological order with the steps of each wave sorted by name, and appends the report
    of every step that is not stale to ``session.execution_reports``: unchanged, skipped
    (including steps that a pytask expression or marker expression deselects), persisted,
    or failed at plan time, such as a step whose input file is missing, together with the
    steps after it. ``session.config["dry_run"]`` is ``True`` only while planning and is
    ``False`` again afterwards, even if planning raises. With nothing stale, the hook
    stops there: it creates no run directory and binds no workflow. If pytask asks the
    build to stop during planning, after an interrupt or once plan-time failures reach
    ``max_failures`` (one with ``stop_after_first_failure``), planning ends with that
    step, the steps it has not reached get no report, and the hook stops there too: it
    creates no run directory and submits none of the stale steps it has already planned.

    Next it checks the inputs of every stale step, because planning checks no input of a
    step after a stale step and, with ``force``, no input of any step. Each input with a
    file path must exist unless a stale step produces it. A stale step with a missing
    input is reported as FAIL, with a plain message that names the step and the path of
    each missing input and has no traceback or chained error, and every stale step after
    it is reported as SKIP_PREVIOUS_FAILED. A step that planning already reported keeps
    that report. The hook writes nothing for the steps it reports here and does not submit
    them, although with ``force`` planning can already have created the product directory
    of such a step that has no stale ancestor. If a failure here makes pytask ask the
    build to stop (``stop_after_first_failure``, or ``max_failures`` reached), the check
    ends with that step, the steps it has not reached get no report, and the hook submits
    nothing, as when planning stops. Otherwise the other stale steps, including the stale
    ancestors of a failed step, are the steps to submit. If there are none, the hook stops
    there, as with nothing stale.

    Next it builds a recipe for every step to submit, resolving each declared env once for
    the whole plan. If any of these steps cannot run on a compute node, because
    :func:`~vivarium.dagger.bridge.payload.payload_for` refuses it (for example, its
    target cannot be named by import reference), the step was not built by
    :func:`~vivarium.dagger.backend.build.build`, or its arguments cannot be loaded on
    the controller, each such step is reported as FAIL with the reason as a plain
    message. No recipe, run directory or product is then written and nothing is
    submitted, although planning, like
    :func:`~vivarium.dagger.backend.preview.preview`, can already have created product
    directories.

    Otherwise it checks the Jobmon environment and works out how long Jobmon may run the
    workflow. That is ``jobmon_timeout`` when the build sets it, which must then be a
    positive whole number of seconds. Without it, inside a Slurm allocation (with
    ``SLURM_JOB_ID`` set), it is the allocation's remaining time less a safety buffer, from
    :func:`vivarium.cluster_tools.core.cluster.interface.get_workflow_timeout_seconds`, as
    for the ``dagger`` runner; outside one, it is Jobmon's own default. Next, if the
    latest earlier run under the root whose Jobmon workflow was bound
    (:func:`~vivarium.dagger.bridge.state.latest_run_state` with ``bound_only=True``) did
    not finish (its state is not ``"finished"``), or finished after Jobmon stopped it
    with an error (its state's ``error`` is set), the hook prints a warning through
    pytask's console that names that run's workflow id and run directory, gives that
    error if there is one, and says that its jobs on compute nodes may still be running
    and writing the same products. A run interrupted before its workflow was bound (its
    state has no workflow id) launched no jobs, so it is passed over and the run before
    it is checked. If a state file cannot be read, the warning names the file instead.
    Either way the build goes on.

    It then creates a new run directory
    ``<root>/.pytask/jobmon/<YYYYmmdd-HHMMSS>-<6 hex digits>/``, writes each recipe there
    as ``<signature>.payload.json``, creates the parent directory of the products of each
    step to submit, and binds and runs one workflow. Its entries are the steps to submit
    in plan order, named by step and carrying each step's
    :attr:`~vivarium.dagger.backend.task.Task.resources`. Each entry waits for the
    step's nearest stale ancestors, found by walking back through data nodes and through
    steps that are not stale. Each entry's command runs
    :func:`~vivarium.dagger.bridge.runner.runner_command` for its recipe with this
    Python environment's ``bin`` directories first on ``PATH``, and the runner writes
    the result to ``<signature>.result.json`` beside the recipe. The workflow is named
    after the root directory and takes its cluster, default resources, attempts and
    resource scales from the configuration keys that
    :mod:`~vivarium.dagger.bridge.plugin` describes, and the run gets the timeout worked
    out above. As soon as the workflow is bound, the hook writes the run's state file
    (:func:`~vivarium.dagger.bridge.state.write_run_state`) with the status
    ``"running"``, the workflow id, and every submitted step as pending, so a build
    killed from then on leaves the workflow id behind.

    While the hook binds and runs the workflow in the main thread, the first SIGTERM that
    the process receives raises :class:`BridgeInterrupted`, and any later SIGTERM is
    ignored until the run has stopped. Afterwards SIGTERM's earlier handler is in effect
    again. If Jobmon put its own handler in front of the bridge's meanwhile, which it does
    when it sets up its logging and which calls the bridge's handler in turn, the bridge's
    handler is switched off instead, so that a later SIGTERM gets the earlier handling. Off
    the main thread, the hook changes no signal handling. A :class:`BridgeInterrupted`, or
    a ``KeyboardInterrupt`` from Jobmon (which Jobmon raises when a user confirms the stop
    at its prompt), stops the run; the hook catches it and goes on to record and settle the
    run, so pytask still writes its lockfile.

    When the run has stopped, the hook records the result of each submitted step whose
    result file is in the run directory, in plan order, and appends its report to
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
    and its :class:`RemoteTaskFailed` names the file. A step that has a result but that
    pytask has marked in this way, because a step before it in plan order failed, also
    when that step failed only at recording, is reported as SKIP_PREVIOUS_FAILED with no
    lockfile entry, as a local build reports it, although its body ran on its node. Every
    result is recorded, also after ``max_failures`` steps have failed.

    Then the hook settles each submitted step that has no result file, in plan order,
    without calling pytask's execute protocol or running any step's body. A step that
    pytask has marked as skipped because an ancestor failed is reported as
    SKIP_PREVIOUS_FAILED. Any other step is reported as FAIL with a
    :class:`RemoteTaskFailed` whose ``exc_type``, ``exc_message`` and ``node_traceback``
    are ``None``. Its message names the step and first says why it has no result: that
    Jobmon stopped the workflow with an error, giving the error's type and message; that
    the build was interrupted; that the build was interrupted before the Jobmon workflow
    was bound; or that the workflow ended without a result for the step, as when the
    runner was killed on its node. It then says that the step was not recorded and will
    run again in the next build, and gives the step's Jobmon task status, the workflow id
    and the workflow's status, each as ``unknown`` when Jobmon gave none. When no workflow
    was bound, it says instead that the step has no Jobmon status. Its ``stdout`` and
    ``stderr`` are the tails of the output that the step's runner wrote to its capture
    files (:func:`~vivarium.dagger.bridge.runner.capture_paths`), or empty when there are
    none, and non-empty tails are also in the report's sections as captured output of the
    call. pytask then marks every step after such a FAIL as skipped, so those steps are
    reported as SKIP_PREVIOUS_FAILED and not as further FAILs. Every submitted step thus
    ends with exactly one report, also when ``max_failures`` or
    ``stop_after_first_failure`` is set. A SIGTERM that arrives while the hook records or
    settles gets the earlier handling, by default ending the process, and pytask's lockfile
    journal then keeps the steps recorded so far.

    Last, the hook writes the run's state file again, with the status ``"interrupted"``
    after an interrupt and ``"finished"`` otherwise, Jobmon's workflow status, the error
    that stopped Jobmon if there was one, and the names of the submitted steps not recorded
    as successful. It also leaves that state in ``session.config`` under
    :data:`~vivarium.dagger.bridge.state.RUN_STATE_KEY`. A build that submits nothing
    writes no state file and leaves no state there.

    Parameters
    ----------
    session
        The build's pytask session.

    Returns
    -------
        ``True``, so pytask's own loop runs no step and pytask then writes its lockfile.

    Raises
    ------
    pytask.ExecutionError
        If the Jobmon environment check fails; if ``jobmon_timeout`` is set to anything
        but a positive whole number of seconds, with a message that names the key and the
        value; or if ``jobmon_timeout`` is not set and the Slurm allocation's remaining
        time cannot be read or is shorter than the safety buffer, with a message that
        gives that error and says to set ``jobmon_timeout``. In these cases no run
        directory has been created. Also if Jobmon refuses to bind the workflow, which the
        adapter reports as
        :class:`~vivarium.dagger.bridge._jobmon_adapter.JobmonBindError`, for example
        because neither ``jobmon_resources`` nor a step's resources name a queue. The
        message then names ``jobmon_cluster`` and the compute resources to check and
        quotes the adapter's message, which gives the type and message of Jobmon's error.
        The run directory, its recipes and the product directories have then already been
        created, but no state file, and no step has run. Each message is printed through
        pytask's console first, so the build fails with that message and no traceback.
        Also, before any of these checks, if another process holds the run lock on the
        build's root, with the message of the
        :class:`~vivarium.dagger.backend.lock.RunLocked`. Nothing has then been planned,
        created or written, and pytask's lockfile and journal are left as they are.
    OSError
        If the run lock file cannot be created or opened for reading and writing, or if
        ``flock`` fails on it for a reason other than a held lock, such as ``ENOLCK``, as
        :func:`~vivarium.dagger.backend.lock.run_lock` raises it. pytask's lockfile state
        is detached first, as for a refusal, so ``pytask.lock`` and its journal are left
        as they are. pytask then fails the build with a traceback.
    """
    held = False
    try:
        with run_lock(Path(session.config["root"]) / RUN_LOCK_NAME):
            held = True
            _reload_lockfile_state(session)
            return _execute(session)
    # Nothing inside the block takes a run lock, so a RunLocked here is the refusal.
    except RunLocked as error:
        raise _refuse(session, error) from error
    except BaseException:
        # Another run may hold the lock, so pytask_unconfigure must not flush the state
        # that pytask read at parse time over that run's pytask.lock and journal.
        if not held:
            session.config["lockfile_state"] = None
        raise


def _execute(session: Session) -> bool:
    """Plan, run, record and settle the build as the hook describes; return True."""
    stale = _plan(session)
    # pytask's own loop also stops once max_failures is reached or a task is interrupted.
    if not stale or session.should_stop:
        return True
    to_submit = _check_inputs(session, stale)
    if not to_submit or session.should_stop:
        return True
    payloads = _payloads(session, to_submit, env_paths={})
    if payloads is None:
        return True
    config = session.config
    cluster: str = config["jobmon_cluster"]
    # Call the adapter through its module, so that harness.FakeJobmon can replace it.
    try:
        _jobmon_adapter.check_environment(cluster)
    except _jobmon_adapter.JobmonEnvironmentError as error:
        raise _failure(str(error)) from error
    timeout = _workflow_timeout(config)
    root = Path(config["root"])
    _warn_if_last_run_unfinished(root)
    workdir = _new_run_directory(root)
    plan = _compile(session, to_submit, payloads, workdir)
    _prepare_products(session, to_submit)
    jobmon_run, interrupted = _bind_and_run(session, plan, to_submit, workdir, timeout)
    leftovers = _record_available(session, to_submit, workdir)
    cause = _cause(jobmon_run, interrupted=interrupted)
    _settle(session, leftovers, jobmon_run, cause, workdir=workdir)
    final = RunState(
        workdir=workdir,
        status="interrupted" if interrupted else "finished",
        workflow_id=None if jobmon_run is None else jobmon_run.workflow_id,
        workflow_status=None if jobmon_run is None else jobmon_run.status,
        error=_run_error(jobmon_run),
        pending=_not_succeeded(session, to_submit),
    )
    write_run_state(final)
    session.config[RUN_STATE_KEY] = final
    return True


def _reload_lockfile_state(session: Session) -> None:
    """Replace pytask's lockfile state with a fresh read of the files, unless it is None."""
    state = session.config.get("lockfile_state")
    if state is None:
        return
    # pytask read the files when it parsed its configuration, before this build held the
    # lock, so an earlier build may have recorded steps since. The state's own class is
    # used because pytask does not export it.
    session.config["lockfile_state"] = type(state).from_path(state.path, state.root)


def _refuse(session: Session, error: RunLocked) -> ExecutionError:
    """Detach the lockfile state, keep ``error`` in the config, and print it.

    Return a new ExecutionError for the caller to raise.
    """
    # Without this, pytask_unconfigure would rewrite the holder's pytask.lock from this
    # build's stale state and delete the holder's journal.
    session.config["lockfile_state"] = None
    session.config[RUN_REFUSED_KEY] = error
    return _failure(str(error))


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


def _missing_inputs(session: Session, task: PTask, stale: Set[str]) -> list[str]:
    """Return the sorted paths of the task's missing inputs that no stale task produces."""
    missing: list[str] = []
    for signature in session.dag.predecessors(task.signature):
        node = session.dag.nodes[signature]
        produced = not stale.isdisjoint(session.dag.predecessors(signature))
        if isinstance(node, PPathNode) and not produced and not node.path.exists():
            missing.append(str(node.path))
    return sorted(missing)


def _check_inputs(session: Session, stale: list[PTask]) -> list[PTask]:
    """Report the stale tasks that miss an input and those after them; return the others.

    End at a failure after which pytask asks the build to stop, as planning does.
    """
    stale_signatures = {task.signature for task in stale}
    failed_upstream: dict[str, list[str]] = {}
    remaining: list[PTask] = []
    for task in stale:
        failed_names = failed_upstream.get(task.signature)
        if failed_names:
            reason = "\n".join(f"Previous task {name!r} failed." for name in failed_names)
            # pytask's skipping plugin makes this SKIP_PREVIOUS_FAILED, as in a local build.
            _report_exception(session, task, SkippedAncestorFailed(reason))
            continue
        missing = _missing_inputs(session, task, stale_signatures)
        if not missing:
            remaining.append(task)
            continue
        paths = "".join(f"\n    {path}" for path in missing)
        message = (
            f"Task '{task.name}': these inputs do not exist, and no step that runs before "
            f"it writes them:{paths}"
        )
        _report_exception(session, task, NodeNotFoundError(message))
        if session.should_stop:
            break
        for signature in session.dag.descendants(task.signature) & stale_signatures:
            failed_upstream.setdefault(signature, []).append(task.name)
    return remaining


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
                f"{refused} because it was not built by vivarium.dagger. Only the steps "
                "that vivarium.dagger.backend.build.build builds can run through Jobmon."
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
    _report_exception(session, task, ValueError(reason))


def _report_exception(session: Session, task: PTask, exception: Exception) -> None:
    """Report ``task`` with ``exception`` as pytask's protocol does, with no traceback."""
    exc_info = (type(exception), exception, None)
    report = ExecutionReport.from_task_and_exception(task, exc_info)
    # Planning already logged the task's end, and the live table needs a start first.
    session.hook.pytask_execute_task_log_start(session=session, task=task)
    session.hook.pytask_execute_task_process_report(session=session, report=report)
    session.hook.pytask_execute_task_log_end(session=session, task=task, report=report)
    session.execution_reports.append(report)


def _failure(message: str) -> ExecutionError:
    """Print ``message`` in pytask's failure style and return an ExecutionError with it."""
    console.print(message, style="failed", markup=False, highlight=False, soft_wrap=True)
    return ExecutionError(message)


def _workflow_timeout(config: Mapping[str, Any]) -> int | None:
    """Return Jobmon's timeout from ``jobmon_timeout`` or the Slurm allocation, or None."""
    value: object = config.get("jobmon_timeout")
    if value is None:
        # Imported here, so that loading the bridge does not import cluster-tools.
        from vivarium.cluster_tools.core.cluster.interface import get_workflow_timeout_seconds

        try:
            return get_workflow_timeout_seconds()
        # ValueError: squeue gave a remaining time that cluster-tools cannot parse, such as
        # UNLIMITED or NOT_SET.
        except (RuntimeError, ValueError) as error:
            raise _failure(
                "The bridge could not work out how long Jobmon may run the workflow from "
                f"the remaining time of this Slurm allocation ({error}). Set "
                "jobmon_timeout to the number of seconds that Jobmon may run the workflow."
            ) from error
    # bool is a subclass of int, but True is not a number of seconds.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise _failure(
        "jobmon_timeout must be a positive whole number of seconds, but the build set it "
        f"to {value!r}."
    )


def _warn_if_last_run_unfinished(root: Path) -> None:
    """Warn naming the latest run under ``root`` if it did not finish or Jobmon errored."""
    try:
        state = latest_run_state(root, bound_only=True)
    except (OSError, ValueError) as error:
        _warn(
            "The bridge could not read the state file of the latest earlier run under "
            f"{root}, so it cannot tell whether that run finished "
            f"({_describe(error)}). The build goes on."
        )
        return
    if state is None or (state.status == "finished" and state.error is None):
        return
    # After a Jobmon error, such as its timeout, the submitted jobs can keep running.
    if state.status == "finished":
        what = "stopped with an error"
    else:
        what = f"did not finish: its state is {state.status!r}"
    if state.error is not None:
        what = f"{what} ({state.error})"
    _warn(
        f"The latest earlier run under {root}, Jobmon workflow {state.workflow_id} in "
        f"{state.workdir}, {what}. Its jobs on compute nodes may still be running and "
        "writing the same products as this build."
    )


def _warn(message: str) -> None:
    """Print ``message`` in pytask's warning style."""
    console.print(message, style="warning", markup=False, highlight=False, soft_wrap=True)


def _describe(error: BaseException) -> str:
    """Return ``error`` as ``"<type name>: <message>"``."""
    return f"{type(error).__name__}: {error}"


def _bind_and_run(
    session: Session,
    plan: Sequence[PlanEntry],
    submitted: Sequence[PTask],
    workdir: Path,
    timeout: int | None,
) -> tuple[JobmonRun | None, bool]:
    """Bind and run ``plan``; return the run and whether the build was interrupted.

    The run is None if the build was interrupted before the workflow was bound.
    """
    config = session.config
    cluster: str = config["jobmon_cluster"]
    jobmon_run: JobmonRun | None = None
    bind_error: _jobmon_adapter.JobmonBindError | None = None
    interrupted = False
    try:
        with _sigterm_raises():
            try:
                jobmon_run = _jobmon_adapter.bind(
                    plan,
                    cluster=cluster,
                    default_resources=config["jobmon_resources"],
                    max_attempts=config["jobmon_max_attempts"],
                    resource_scales=config["jobmon_resource_scales"],
                    workflow_name=Path(config["root"]).name,
                )
            except _jobmon_adapter.JobmonBindError as error:
                bind_error = error
            else:
                running = RunState(
                    workdir=workdir,
                    status="running",
                    workflow_id=jobmon_run.workflow_id,
                    workflow_status=None,
                    error=None,
                    pending=tuple(task.name for task in submitted),
                )
                write_run_state(running)
                _jobmon_adapter.run(jobmon_run, seconds_until_timeout=timeout)
    except (BridgeInterrupted, KeyboardInterrupt):
        interrupted = True
    # Raised outside the SIGTERM block, so that a SIGTERM cannot turn it into an interrupt.
    if bind_error is not None:
        raise _failure(
            f"Jobmon could not bind the workflow on the {cluster!r} cluster. Check "
            "jobmon_cluster and the compute resources in jobmon_resources and in each "
            f"step's resources. Jobmon reported: {bind_error}"
        ) from bind_error
    return jobmon_run, interrupted


def _cause(jobmon_run: JobmonRun | None, *, interrupted: bool) -> str:
    """Return why the run's steps without a result have none, as a clause."""
    if interrupted and jobmon_run is None:
        return "the build was interrupted before the Jobmon workflow was bound"
    if interrupted:
        return "the build was interrupted"
    if jobmon_run is not None and jobmon_run.error is not None:
        return f"Jobmon stopped the workflow with an error, {_describe(jobmon_run.error)}"
    return (
        "the workflow ended without a result for the step, as when the step's runner is "
        "killed on its node"
    )


def _run_error(jobmon_run: JobmonRun | None) -> str | None:
    """Return the error that stopped the Jobmon run as ``"<type>: <message>"``, or None."""
    if jobmon_run is None or jobmon_run.error is None:
        return None
    return _describe(jobmon_run.error)


def _not_succeeded(session: Session, submitted: Sequence[PTask]) -> tuple[str, ...]:
    """Return the names of the submitted tasks without a SUCCESS report, in plan order."""
    succeeded = {
        report.task.signature
        for report in session.execution_reports
        if report.outcome == TaskOutcome.SUCCESS
    }
    return tuple(task.name for task in submitted if task.signature not in succeeded)


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
    """Record the stale tasks that have a result file; return the others in order.

    A task with a result that pytask has marked ``skip_ancestor_failed`` is reported as
    SKIP_PREVIOUS_FAILED instead of being recorded.
    """
    listed = set(os.listdir(workdir))
    unfinished: list[PTask] = []
    for task in stale:
        name = f"{task.signature}{RESULT_SUFFIX}"
        if name not in listed:
            unfinished.append(task)
            continue
        # A step before this one failed, possibly only at recording, so a local build
        # would not have run this one and it gets no lockfile entry.
        reason = _ancestor_failure(task)
        if reason is not None:
            _report_exception(session, task, SkippedAncestorFailed(reason))
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


@contextmanager
def _sigterm_raises() -> Iterator[None]:
    """Turn the first SIGTERM into ``BridgeInterrupted`` in the main thread while the block runs."""
    # signal.signal raises ValueError off the main thread.
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    active = True
    raised = False

    def handler(signum: int, frame: FrameType | None) -> None:
        """Raise BridgeInterrupted once, then ignore SIGTERM; when off, pass it on."""
        nonlocal raised
        if not active:
            _forward_sigterm(previous, signum, frame)
            return
        if raised:
            return
        raised = True
        raise BridgeInterrupted(INTERRUPTED_EXIT_CODE)

    try:
        signal.signal(signal.SIGTERM, handler)
        yield
    finally:
        active = False
        # Jobmon's logging setup can put a handler in front of this one that calls it and
        # stays installed. Switched off, this one then passes SIGTERM to ``previous``.
        if signal.getsignal(signal.SIGTERM) is handler:
            signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


def _forward_sigterm(previous: object, signum: int, frame: FrameType | None) -> None:
    """Handle SIGTERM as ``previous``, the handler from before the bridge's, would."""
    if callable(previous):
        previous(signum, frame)
    elif previous != signal.SIG_IGN:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)


def _settle(
    session: Session,
    leftovers: Sequence[PTask],
    jobmon_run: JobmonRun | None,
    cause: str,
    *,
    workdir: Path,
) -> None:
    """Report each leftover in plan order without running it: a skip if marked, else a FAIL.

    ``workdir`` is the run directory, which holds the capture files of each leftover's
    runner.
    """
    ending = "" if cause.endswith((".", "!", "?")) else "."
    for task in leftovers:
        reason = _ancestor_failure(task)
        if reason is not None:
            _report_exception(session, task, SkippedAncestorFailed(reason))
            continue
        message = (
            f"Step '{task.name}' has no result because {cause}{ending} It was not recorded "
            f"and will run again in the next build. {_jobmon_statuses(task, jobmon_run)}"
        )
        capture = capture_paths(workdir / f"{task.signature}{RESULT_SUFFIX}")
        stdout, stderr = _tail(capture[0]), _tail(capture[1])
        if stdout:
            task.report_sections.append(("call", "stdout", stdout))
        if stderr:
            task.report_sections.append(("call", "stderr", stderr))
        error = RemoteTaskFailed(message, stdout=stdout, stderr=stderr)
        _report_exception(session, task, error)


def _jobmon_statuses(task: PTask, jobmon_run: JobmonRun | None) -> str:
    """Return the sentences that give a settled step's Jobmon task and workflow status."""
    if jobmon_run is None:
        return "It has no Jobmon status, because no Jobmon workflow was bound."
    task_status = jobmon_run.task_statuses.get(task.signature) or "unknown"
    workflow = (
        "The Jobmon workflow"
        if jobmon_run.workflow_id is None
        else f"Jobmon workflow {jobmon_run.workflow_id}"
    )
    return (
        f"Its Jobmon task status is '{task_status}'. {workflow} has the workflow status "
        f"'{jobmon_run.status or 'unknown'}'."
    )


def _ancestor_failure(task: PTask) -> str | None:
    """Return the reasons of the task's ``skip_ancestor_failed`` marks, or None if none."""
    marks = get_marks(task, "skip_ancestor_failed")
    if not marks:
        return None
    # The same default as pytask's own parser of the mark.
    return "\n".join(
        str(mark.kwargs.get("reason", mark.args[0] if mark.args else "No reason provided."))
        for mark in marks
    )


def _tail(path: Path) -> str:
    """Return the tail of ``path`` that a node result keeps, or an empty string on error."""
    try:
        return read_tail(path)
    except OSError:
        return ""
