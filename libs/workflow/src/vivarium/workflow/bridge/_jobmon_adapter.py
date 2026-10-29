"""Bind and run a plan of pipeline steps as one Jobmon workflow.

This is the only module of the library that imports Jobmon, and it does so only inside
:func:`bind` and :func:`run`, so the rest of the bridge, the node runner and the test
harness import without the ``cluster`` extra. It is not meant to be called directly:
a build that runs through Jobmon calls :func:`check_environment`, then
:func:`bind`, then :func:`run`, and :class:`~vivarium.workflow.harness.FakeJobmon`
replaces those three functions in tests.

Each step becomes one Jobmon task. Steps of one family (see :func:`family`) share a task
template, so Jobmon's GUI shows one node per family.
"""

import importlib.metadata
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

TOOL_NAME = "vivarium.workflow.bridge"
"""Name of the Jobmon tool that every bridge workflow is created under."""

SUPPORTED_SERIES: Mapping[str, str] = {
    "jobmon_client": "3.7",
    "jobmon_core": "3.7",
    "jobmon_slurm": "1",
}
"""The release series of each Jobmon distribution that the bridge supports, by
distribution name. A version is in a series when its leading components equal the
series', so ``3.7.19`` is in ``3.7`` and ``3.18.0`` is not. These are the series that the
``cluster`` extra's ``jobmon_installer_ihme`` pin installs."""

MAX_NAME_LENGTH = 250
"""The most characters that :func:`jobmon_name` keeps."""

_TRAILING_ID = re.compile(r"\[[^\[\]]*\]$")
"""A bracketed part with no brackets inside it, at the end of a name."""

_REJECTED_CHARACTERS = re.compile(r"""[/\\'" ]""")
"""The characters that Jobmon does not accept in a task or task template name."""

_RELEASE = re.compile(r"\d+(?:\.\d+)*")
"""The numeric release components at the start of a version string."""


class JobmonEnvironmentError(RuntimeError):
    """Raised when the installed Jobmon or this host cannot run a bridge workflow."""


class JobmonBindError(RuntimeError):
    """Raised when Jobmon refuses to create or bind a bridge workflow."""


@dataclass(frozen=True)
class PlanEntry:
    """One stale step, ready to become a Jobmon task."""

    signature: str
    """The step's pytask task signature, which also names its recipe and result files."""
    name: str
    """The step's name."""
    command: str
    """The shell command that runs the step on a compute node."""
    upstreams: tuple[str, ...]
    """The signatures of the earlier entries that this step waits for."""
    resources: Mapping[str, Any]
    """The step's own compute resources, from
    :attr:`~vivarium.workflow.backend.task.Task.resources`. Jobmon merges them over the
    workflow's default resources key by key."""


@dataclass
class JobmonRun:
    """A plan bound as one Jobmon workflow, and its outcome once :func:`run` returns."""

    plan: tuple[PlanEntry, ...]
    """The entries that were bound, in plan order."""
    workflow_id: int | None
    """Jobmon's id for the workflow, known as soon as it is bound. The test harness's
    fake reports ``None``."""
    workflow: Any = None
    """The bound ``jobmon.client.workflow.Workflow``, or ``None`` from the fake."""
    tasks: dict[str, Any] = field(default_factory=dict)
    """The Jobmon task of each entry, by signature. The fake leaves it empty."""
    status: str | None = None
    """The workflow run's final status code, such as ``"D"`` when every task finished and
    ``"E"`` when one did not. ``None`` until :func:`run` returns."""
    task_statuses: dict[str, str | None] = field(default_factory=dict)
    """The final status code of each entry's task, by signature, such as ``"D"`` (done),
    ``"F"`` (failed) or ``"G"`` (never ran because an upstream did not finish). Empty
    until :func:`run` returns."""


def family(name: str) -> str:
    """Return a step name without its trailing ``[...]`` repetition id.

    Steps that differ only in that id, such as ``pafs[ETH]`` and ``pafs[NGA]``, belong to
    one family and share one Jobmon task template.

    Parameters
    ----------
    name
        The step's name.

    Returns
    -------
        ``name`` with one trailing bracketed part removed, or ``name`` unchanged if it does
        not end in one.
    """
    return _TRAILING_ID.sub("", name)


def jobmon_name(name: str) -> str:
    """Return a name that Jobmon accepts for a task or a task template.

    Each character that Jobmon rejects in a name (``/``, ``\\``, ``'``, ``"`` and the
    space) becomes ``_``, a name that starts with a digit gets a leading ``_``, and the
    result is cut to :data:`MAX_NAME_LENGTH` characters. Two names can give the same
    result, which changes only how Jobmon's GUI groups their tasks.

    Parameters
    ----------
    name
        A step name or a family name.

    Returns
    -------
        The name with those changes.
    """
    cleaned = _REJECTED_CHARACTERS.sub("_", name)
    if cleaned[:1].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[:MAX_NAME_LENGTH]


def check_environment(cluster: str) -> None:
    """Check that the installed Jobmon and this host can run a workflow on ``cluster``.

    The installed versions are read from distribution metadata, so nothing from Jobmon is
    imported. ``jobmon_client`` and ``jobmon_core`` must be installed and in their
    :data:`SUPPORTED_SERIES`. On the ``"slurm"`` cluster, ``jobmon_slurm`` must be too,
    and ``scontrol`` must be on ``PATH``, because Jobmon's Slurm plugin calls
    ``scontrol``, ``squeue`` and ``scancel`` from the host that runs the workflow.

    Parameters
    ----------
    cluster
        The Jobmon cluster name, such as ``"slurm"`` or ``"multiprocess"``.

    Raises
    ------
    JobmonEnvironmentError
        If a required distribution is missing or outside its series, with a message that
        names each installed version and says that ``pip install
        "vivarium-workflow[cluster]"`` installs the supported set; or, on Slurm, if
        ``scontrol`` is not on ``PATH``, with a message that names ``/opt/slurm/bin`` and
        says to run on a compute node.
    """
    required = [
        name for name in SUPPORTED_SERIES if name != "jobmon_slurm" or cluster == "slurm"
    ]
    versions = {name: _installed_version(name) for name in required}
    if not all(_in_series(versions[name], SUPPORTED_SERIES[name]) for name in required):
        for name in SUPPORTED_SERIES:
            if name not in versions:
                versions[name] = _installed_version(name)
        installed = ", ".join(
            f"{name} {'(not installed)' if version is None else version}"
            for name, version in versions.items()
        )
        supported = ", ".join(f"{name} {SUPPORTED_SERIES[name]}.x" for name in required)
        raise JobmonEnvironmentError(
            f"The Jobmon bridge cannot run on the {cluster!r} cluster with the Jobmon "
            f"distributions found here ({installed}). It needs {supported}. Run "
            'pip install "vivarium-workflow[cluster]" to install the supported set.'
        )
    if cluster == "slurm" and shutil.which("scontrol") is None:
        raise JobmonEnvironmentError(
            "The Jobmon bridge cannot run on the 'slurm' cluster from this host, because "
            "scontrol is not on PATH. Jobmon's Slurm plugin calls scontrol, squeue and "
            "scancel from the host that runs the workflow, so run the build on a compute "
            "node with /opt/slurm/bin on PATH."
        )


def bind(
    plan: Sequence[PlanEntry],
    *,
    cluster: str,
    default_resources: Mapping[str, Any],
    max_attempts: int,
    resource_scales: Mapping[str, float] | None,
    workflow_name: str,
) -> JobmonRun:
    """Create and bind one Jobmon workflow for ``plan`` without running it.

    The workflow is created under the tool :data:`TOOL_NAME`, named ``workflow_name``,
    with ``default_resources`` as its default compute resources on ``cluster``, and with
    a new random uuid as its ``workflow_args``, so each bind makes a new workflow and
    never resumes an earlier one. Each entry becomes one task, in plan order: it is
    named by :func:`jobmon_name` of the entry's name, comes from the task template of its
    :func:`family`, runs the entry's command unchanged, waits for the tasks of the
    entry's upstreams, and has the entry's resources as its compute resources, at most
    ``max_attempts`` attempts, and ``resource_scales`` when that is not ``None``. Call
    :func:`check_environment` first.

    Parameters
    ----------
    plan
        The entries, each after every entry it waits for.
    cluster
        The Jobmon cluster to run on.
    default_resources
        The compute resources that every task starts from.
    max_attempts
        How many times Jobmon tries each task.
    resource_scales
        How much Jobmon raises each named resource after a resource failure, as a
        fraction. ``None`` keeps Jobmon's default, which raises memory and runtime by half.
    workflow_name
        The workflow's name in Jobmon.

    Returns
    -------
        The bound run, with its workflow id set and no status yet.

    Raises
    ------
    KeyError
        If an entry names an upstream that is not an earlier entry of ``plan``. This is
        checked before Jobmon is called.
    JobmonBindError
        If a Jobmon call raises an ``Exception``, for example the ``ValueError`` that
        Jobmon raises when neither ``default_resources`` nor an entry's resources name a
        queue, or the ``InvalidRequest`` that it raises when its server rejects a
        request. The message is the type and message of Jobmon's error, as in
        ``"ValueError: <message>"``, and the error is chained from it. A
        ``KeyboardInterrupt`` or ``SystemExit`` from Jobmon passes through unchanged.
    """
    entries = tuple(plan)
    _check_plan_order(entries)
    # jobmon is in the cluster extra only, so it is missing where CI type-checks.
    from jobmon.client.api import Tool  # type: ignore[import-not-found, unused-ignore]

    # Leaving resource_scales out keeps Jobmon's own default.
    scales: dict[str, Any] = {}
    if resource_scales is not None:
        scales["resource_scales"] = dict(resource_scales)
    templates: dict[str, Any] = {}
    tasks: dict[str, Any] = {}
    # Jobmon's bind errors share no base class narrower than Exception.
    try:
        tool = Tool(name=TOOL_NAME)
        workflow = tool.create_workflow(
            name=workflow_name,
            workflow_args=str(uuid.uuid4()),
            default_cluster_name=cluster,
            default_compute_resources_set={cluster: dict(default_resources)},
        )
        for entry in entries:
            template_name = jobmon_name(family(entry.name))
            if template_name not in templates:
                templates[template_name] = tool.get_task_template(
                    template_name=template_name,
                    command_template="{command}",
                    node_args=["command"],
                    task_args=[],
                    op_args=[],
                )
            tasks[entry.signature] = templates[template_name].create_task(
                name=jobmon_name(entry.name),
                command=entry.command,
                upstream_tasks=[tasks[upstream] for upstream in entry.upstreams],
                compute_resources=dict(entry.resources),
                max_attempts=max_attempts,
                **scales,
            )
        workflow.add_tasks(list(tasks.values()))
        workflow.bind()
        workflow_id = workflow.workflow_id
    except Exception as error:
        raise JobmonBindError(f"{type(error).__name__}: {error}") from error
    return JobmonRun(plan=entries, workflow_id=workflow_id, workflow=workflow, tasks=tasks)


def run(jobmon_run: JobmonRun) -> None:
    """Run a bound workflow in the calling thread until it ends.

    Jobmon keeps running the tasks that do not wait on a failed one. When the workflow
    ends, the run's ``status`` is set to the workflow run's final status and its
    ``task_statuses`` to each task's final status.

    Parameters
    ----------
    jobmon_run
        A run that :func:`bind` returned.
    """
    jobmon_run.status = jobmon_run.workflow.run(fail_fast=False)
    jobmon_run.task_statuses = {
        signature: task.final_status for signature, task in jobmon_run.tasks.items()
    }


def _check_plan_order(plan: Sequence[PlanEntry]) -> None:
    """Raise KeyError if an entry of ``plan`` waits for one that is not an earlier entry."""
    earlier: set[str] = set()
    for entry in plan:
        for upstream in entry.upstreams:
            if upstream not in earlier:
                raise KeyError(
                    f"The plan entry {entry.name!r} waits for {upstream!r}, which is not an "
                    "earlier entry of the plan."
                )
        earlier.add(entry.signature)


def _installed_version(distribution: str) -> str | None:
    """Return the installed version of ``distribution``, or None if it is not installed."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _in_series(version: str | None, series: str) -> bool:
    """Check whether ``version``'s leading release components equal ``series``'."""
    release = None if version is None else _RELEASE.match(version)
    if release is None:
        return False
    wanted = [int(part) for part in series.split(".")]
    found = [int(part) for part in release.group().split(".")]
    return found[: len(wanted)] == wanted
