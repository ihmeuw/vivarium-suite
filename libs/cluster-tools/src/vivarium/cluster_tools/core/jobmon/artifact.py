"""
========================
Parallel artifact builds
========================

Build a model's data artifacts for many locations in parallel: fan one
Jobmon task out per location into a single workflow, replacing the old
one-job-per-location cluster-submission loops.

"""

from __future__ import annotations

from pathlib import Path

from vivarium.cluster_tools.core.cluster.interface import NativeSpecification
from vivarium.cluster_tools.core.jobmon import client


def build_artifacts_in_parallel(
    *,
    workflow_name: str,
    build_commands: dict[str, str],
    native_specification: NativeSpecification,
    worker_logging_root: Path,
    env_prefix: str,
    max_attempts: int = 2,
    max_concurrently_running: int | None = None,
) -> tuple[str, str | None]:
    """Build location artifacts in parallel as a single Jobmon workflow.

    Each ``(name, command)`` in ``build_commands`` -- one per location --
    becomes an independent Jobmon task with **no upstream dependencies**, so
    the locations build concurrently (up to ``max_concurrently_running``).
    Every task shares ``native_specification`` for its compute resources and
    runs in the environment at ``env_prefix``. Raises if the workflow does
    not complete, naming how many location artifacts finished.

    Parameters
    ----------
    workflow_name
        Name (and ``workflow_args``) for the Jobmon workflow. Make it unique
        per invocation for a fresh build rather than a resume.
    build_commands
        Mapping of task name (e.g. ``"<location>_artifact"``) to the shell
        command that builds that location's artifact.
    native_specification
        SLURM resource request shared by every location's build task.
    worker_logging_root
        Directory Jobmon writes per-task worker logs under.
    env_prefix
        Absolute prefix of the conda env whose ``bin`` is prepended to
        ``PATH`` so each build command's interpreter resolves without ``conda``.
    max_attempts
        Times Jobmon retries each location's build before giving up.
    max_concurrently_running
        Cap on locations building at once. ``None`` lets Jobmon decide.

    Returns
    -------
        A ``(workflow_status, monitoring_url)`` tuple from
        :func:`~vivarium.cluster_tools.core.jobmon.client.bind_and_run_workflow`.

    Raises
    ------
    RuntimeError
        If the workflow finishes in any state other than complete.
    """
    tool = client.make_tool()
    template = client.make_task_template(
        tool,
        template_name=workflow_name,
        command_template="PATH={env_prefix}/bin:$PATH {command}",
        node_args=["command", "env_prefix"],
        task_args=[],
        op_args=[],
    )
    compute_resources = native_specification.to_jobmon_spec(worker_logging_root)
    tasks = [
        client.create_task(
            template,
            name=name,
            compute_resources=compute_resources,
            env_prefix=env_prefix,
            command=command,
        )
        for name, command in build_commands.items()
    ]

    workflow = client.make_workflow(
        tool,
        workflow_args=workflow_name,
        name=workflow_name,
        max_attempts=max_attempts,
        max_concurrently_running=max_concurrently_running,
    )
    client.add_tasks(workflow, tasks)

    wf_status, monitoring_url = client.bind_and_run_workflow(workflow, worker_logging_root)
    if wf_status != client.JOBMON_STATUS_DONE:
        completed = client.count_completed_tasks(workflow)
        raise RuntimeError(
            f"Artifact workflow {workflow_name!r} finished with status {wf_status!r}: "
            f"{completed}/{len(build_commands)} location artifacts built. "
            "See the Jobmon GUI for per-location failures."
        )
    return wf_status, monitoring_url
