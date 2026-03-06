"""
=================
Cluster Interface
=================

"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES


def validate_cluster_environment() -> None:
    if "slurm" not in ENV_VARIABLES.HOSTNAME.value:
        raise RuntimeError("This tool must be run from the IHME cluster.")

    submit_host_marker = "slogin"
    if submit_host_marker in ENV_VARIABLES.HOSTNAME.value:
        raise RuntimeError("This tool must not be run from a submit host.")


class NativeSpecification(NamedTuple):
    job_name: str
    project: str
    queue: str
    peak_memory: float  # Memory in GB
    max_runtime: str
    hardware: list[str]

    # Class constant
    NUM_THREADS: int = 1

    def to_jobmon_spec(self, cluster_logging_root: Path) -> dict[str, Any]:
        """Build the Jobmon compute resources dict from this NativeSpecification.

        Parameters
        ----------
        cluster_logging_root
            Root directory for cluster logs.

        Returns
        -------
            Dictionary of compute resources for Jobmon.

        Notes
        -----
        * ``memory`` is passed in **GB** because the Jobmon SLURM plugin performs
          its own GB → MB conversion internally.
        * ``constraints`` is a pipe-separated string of SLURM feature names
          (e.g. ``"r650|r650v2"``), included only when hardware is requested.
        * ``standard_output`` and ``standard_error`` route SLURM stdout/stderr
          to the cluster logs directory. The Jobmon SLURM plugin appends the
          task name and SLURM job ID to these paths automatically.
        """
        resources: dict[str, Any] = {
            "queue": self.queue,
            "project": self.project,
            "memory": self.peak_memory,  # GB – Jobmon converts to MB
            "runtime": self._runtime_to_seconds(self.max_runtime),
            "cores": self.NUM_THREADS,
            "standard_output": str(cluster_logging_root),
            "standard_error": str(cluster_logging_root),
        }
        if self.hardware:
            resources["constraints"] = "|".join(self.hardware)
        return resources

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
        parts = runtime_str.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + int(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + int(s)
        else:
            return int(parts[0])
