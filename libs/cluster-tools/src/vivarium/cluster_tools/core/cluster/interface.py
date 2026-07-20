"""
=================
Cluster Interface
=================

"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

from loguru import logger


class NativeSpecification(NamedTuple):
    job_name: str
    project: str
    queue: str
    peak_memory: float  # Memory in GB
    max_runtime: str
    hardware: list[str]
    cores: int = 1
    """Number of CPU cores to request from SLURM. Default is 1."""
    requires_archive_node: bool = False
    """Whether the task must land on a node tagged with the SLURM ``archive`` feature."""

    def to_jobmon_spec(self, worker_logging_root: Path) -> dict[str, Any]:
        """Build the Jobmon compute resources dict from this NativeSpecification.

        Parameters
        ----------
        worker_logging_root
            Root directory for worker logs.

        Returns
        -------
            Dictionary of compute resources for Jobmon.

        Notes
        -----
        * ``memory`` is passed in **GB** because the Jobmon SLURM plugin performs
          its own GB → MB conversion internally.
        * ``constraints`` is a SLURM ``--constraint`` expression built from
          ``hardware`` and ``requires_archive_node``. The hardware group is
          always parenthesized and pipe-joined (OR); ``archive`` is AND-joined
          when required. Examples: ``"(r650)"``, ``"(r650|r650v2)"``,
          ``"(r650)&archive"``, ``"(r650|r650v2)&archive"``, ``"archive"``.
          The key is omitted when neither is set.

        * ``standard_output`` and ``standard_error`` route SLURM stdout/stderr
          to the cluster logs directory. The Jobmon SLURM plugin appends the
          task name and SLURM job ID to these paths automatically.
        """
        resources: dict[str, Any] = {
            "queue": self.queue,
            "project": self.project,
            "memory": self.peak_memory,  # GB – Jobmon converts to MB
            "runtime": self._runtime_to_seconds(self.max_runtime),
            "cores": self.cores,
            # Jobmon names the files here "<task_name>.o<job_id>"/".e<job_id>"; job_id matches ``squeue --me`` and the GUI.
            "stdout": str(worker_logging_root),
            "stderr": str(worker_logging_root),
        }
        constraint = self._build_constraint()
        if constraint is not None:
            resources["constraints"] = constraint
        return resources

    def _build_constraint(self) -> str | None:
        """Build the SLURM ``--constraint`` expression, or ``None`` if unconstrained."""
        parts: list[str] = []
        if self.hardware:
            parts.append(f"({'|'.join(self.hardware)})")
        if self.requires_archive_node:
            parts.append("archive")
        if not parts:
            return None
        return "&".join(parts)

    @staticmethod
    def _runtime_to_seconds(runtime_str: str) -> int:
        """Convert HH:MM:SS runtime string to seconds.

        Parameters
        ----------
        runtime_str
            Runtime in HH:MM:SS format.

        Returns
        -------
            Runtime in seconds.
        """
        return _parse_slurm_time(runtime_str)


# Buffer in seconds to subtract from the remaining SLURM time so the jobmon
# workflow shuts down cleanly before SLURM kills the runner node.
_SLURM_TIMEOUT_BUFFER_SECONDS = 120


def get_workflow_timeout_seconds() -> int | None:
    """Get jobmon workflow's timeout in seconds.

    The result includes a small buffer so that the workflow can shut down
    gracefully before SLURM terminates the runner node.

    Returns
    -------
        Remaining time in seconds (with buffer subtracted), or ``None`` if
        there is no SLURM allocation (in which case Jobmon's built-in default
        timeout is used).

    Raises
    ------
    RuntimeError
        If the remaining time is less than the safety
        buffer, or the remaining time cannot be determined from ``squeue``.
    """
    job_id = os.environ.get("SLURM_JOB_ID")
    if job_id is None:
        logger.info(
            "SLURM_JOB_ID is unset. The workflow is likely being run "
            "from a SLURM-capable host without an explicit resource allocation "
            "e.g. Jenkins. Deferring to Jobmon's default timeout."
        )
        return None

    try:
        # squeue -h -j <job_id> -o %L gives the remaining time as D-HH:MM:SS
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%L"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        remaining_str = result.stdout.strip()
    except Exception as e:
        raise RuntimeError(
            f"Could not determine remaining SLURM time for job {job_id}: {e}"
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"squeue failed for SLURM job {job_id} "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )

    stderr_output = result.stderr.strip()
    if stderr_output:
        logger.warning(f"squeue stderr for job {job_id}: {stderr_output}")

    if not remaining_str:
        raise RuntimeError(
            f"squeue returned no output for SLURM job {job_id}. "
            "Cannot determine remaining allocation time."
        )

    remaining_seconds = _parse_slurm_time(remaining_str)
    workflow_seconds = remaining_seconds - _SLURM_TIMEOUT_BUFFER_SECONDS
    if workflow_seconds <= 0:
        raise RuntimeError(
            f"SLURM allocation has {remaining_str} ({remaining_seconds}s) remaining, "
            f"which is less than the {_SLURM_TIMEOUT_BUFFER_SECONDS}s safety buffer. "
            "Not enough time to run a workflow."
        )
    logger.info(
        f"Detected SLURM allocation with {remaining_str} ({remaining_seconds}s) remaining. "
        f"Setting workflow timeout to {workflow_seconds}s ({_SLURM_TIMEOUT_BUFFER_SECONDS}s buffer)."
    )
    return workflow_seconds


def _parse_slurm_time(time_str: str) -> int:
    """Parse a SLURM time string into seconds.

    Handles the formats returned by ``squeue -o %L``:
    ``SS``, ``MM:SS``, ``HH:MM:SS``, ``D-HH:MM:SS``.

    Parameters
    ----------
    time_str
        A SLURM time string.

    Returns
    -------
        Total seconds represented by the time string.

    Raises
    ------
    ValueError
        If ``time_str`` does not match a recognized SLURM time format.
    """
    # When a day prefix is present, squeue always uses D-HH:MM:SS.
    # Without a day prefix the format is HH:MM:SS, MM:SS, or SS.
    if not re.fullmatch(r"(\d+-\d+:\d+:\d+|\d+(:\d+){0,2})", time_str):
        raise ValueError(
            f"Unrecognized SLURM time format: '{time_str}'. "
            "Expected D-HH:MM:SS, HH:MM:SS, MM:SS, or SS."
        )

    if "-" in time_str:
        days, hms = time_str.split("-", 1)
    else:
        days = "0"
        hms = time_str

    hms_parts = hms.split(":")
    if len(hms_parts) == 3:
        h, m, s = hms_parts
    elif len(hms_parts) == 2:
        m, s = hms_parts
        h = "0"
    else:
        s = hms_parts[0]
        h = "0"
        m = "0"
    return int(days) * 86400 + int(h) * 3600 + int(m) * 60 + int(s)
