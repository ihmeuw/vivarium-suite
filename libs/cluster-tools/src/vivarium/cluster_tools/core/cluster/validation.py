"""
==================
Cluster Validation
==================

Constants and validation helpers for cluster resources (projects, queues,
runtimes, and hardware).

"""

from __future__ import annotations

import re

from vivarium.cluster_tools.psimulate.environment import ENV_VARIABLES

VALID_PROJECTS = frozenset(
    {
        "proj_simscience",
        "proj_simscience_prod",
        "proj_csu",
    }
)

VALID_QUEUES = frozenset({"all.q", "long.q"})
# Sorted from shortest to longest allowed runtime.
QUEUE_MAX_RUNTIME_HOURS: dict[str, int] = {
    "all.q": 3 * 24,
    "long.q": 16 * 24,
}

RUNTIME_FORMAT = "hh:mm:ss"
_RUNTIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
# https://docs.cluster.ihme.washington.edu/#hpc-execution-host-hardware-specifications
AVAILABLE_HARDWARE = [
    "c6320",  # typical
    "r630",  # high capacity
    "c6420v1",  # batch 1
    "c6420v2",  # batch 2
    "r650",  # high capacity
    "r650v2",  # high capacity
    "r650xs",  # high speed
]


def validate_project(project: str) -> str:
    """Validate that `project` is a recognised cluster project.

    Returns the project unchanged on success.

    Raises
    ------
    ValueError
        If *project* is not in ``VALID_PROJECTS``.
    """
    if project not in VALID_PROJECTS:
        raise ValueError(
            f"Invalid project '{project}'. " f"Must be one of: {sorted(VALID_PROJECTS)}."
        )
    return project


def validate_runtime_and_queue(runtime: str, queue: str | None) -> tuple[str, str]:
    """Validate and reconcile a runtime string with a queue.

    * Validates runtime format (``hh:mm:ss``).
    * If *queue* is ``None``, selects the shortest queue that can
      accommodate *runtime*.
    * If *queue* is given, checks that *runtime* does not exceed the
      queue's maximum.

    Raises
    ------
    ValueError
        On bad format, runtime exceeding all queues, or runtime
        exceeding the selected queue.
    Returns
    -------
        The validated and reconciled ``(runtime, queue)`` tuple.
    """
    if not _RUNTIME_RE.match(runtime):
        raise ValueError(
            f"Invalid runtime '{runtime}'. " f"Expected format {RUNTIME_FORMAT}."
        )

    hours, minutes, seconds = runtime.split(":")
    total_hours = int(hours) + float(minutes) / 60.0 + float(seconds) / 3600.0

    if queue is not None:
        if queue not in QUEUE_MAX_RUNTIME_HOURS:
            raise ValueError(
                f"Invalid queue '{queue}'. "
                f"Must be one of: {sorted(QUEUE_MAX_RUNTIME_HOURS)}."
            )
        if total_hours > QUEUE_MAX_RUNTIME_HOURS[queue]:
            raise ValueError(
                f"Runtime '{runtime}' exceeds the maximum for queue "
                f"'{queue}' ({QUEUE_MAX_RUNTIME_HOURS[queue]}:00:00)."
            )
        return runtime, queue

    # No queue specified — pick a queue that fits.
    for q, max_q_hours in QUEUE_MAX_RUNTIME_HOURS.items():
        if total_hours <= max_q_hours:
            return runtime, q

    raise ValueError(f"No queue can accommodate runtime '{runtime}'.")


def validate_hardware(hardware: list[str]) -> list[str]:
    """Validate that every element of *hardware* is a known hardware type.

    Returns *hardware* unchanged on success.

    Raises
    ------
    ValueError
        If any entry is not in ``AVAILABLE_HARDWARE``.
    """
    bad = set(hardware) - set(AVAILABLE_HARDWARE)
    if bad:
        raise ValueError(
            f"Unsupported hardware: {sorted(bad)}. " f"Supported: {AVAILABLE_HARDWARE}."
        )
    return hardware


def validate_cluster_environment() -> None:
    if "slurm" not in ENV_VARIABLES.HOSTNAME.value:
        raise RuntimeError("This tool must be run from the IHME cluster.")

    submit_host_marker = "slogin"
    if submit_host_marker in ENV_VARIABLES.HOSTNAME.value:
        raise RuntimeError("This tool must not be run from a submit host.")
