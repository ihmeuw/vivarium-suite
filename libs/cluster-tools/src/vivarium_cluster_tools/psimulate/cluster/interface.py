"""
=================
Cluster Interface
=================

"""
from __future__ import annotations

import atexit
import os
import shutil
from pathlib import Path
from tempfile import _TemporaryFileWrapper
from typing import NamedTuple

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES
from vivarium_cluster_tools.utilities import get_drmaa


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
    peak_memory: int  # Memory in GB
    max_runtime: str
    hardware: list[str]

    # Class constant
    NUM_THREADS: int = 1

    def to_cli_args(self) -> str:
        return (
            f"-J {self.job_name} "
            f"-A {self.project} "
            f"-p {self.queue} "
            f"--mem={self.peak_memory*1024} "
            f"-t {self.max_runtime} "
            f"-c {self.NUM_THREADS} "
            f"{'-C ' + '|'.join(self.hardware) if self.hardware else ''}"
        ).strip()


def submit_worker_jobs(
    num_workers: int,
    worker_launch_script: _TemporaryFileWrapper[str],
    cluster_logging_root: Path,
    native_specification: NativeSpecification,
) -> None:
    drmaa = get_drmaa()
    s = drmaa.Session()
    s.initialize()
    jt = s.createJobTemplate()
    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which("sh")
    jt.args = [worker_launch_script.name]
    jt.outputPath = f":{str(cluster_logging_root / '%A.%a.log')}"
    jt.errorPath = f":{str(cluster_logging_root / '%A.%a.log')}"
    jt.jobEnvironment = {
        "LC_ALL": "en_US.UTF-8",
        "LANG": "en_US.UTF-8",
    }
    jt.joinFiles = True
    jt.nativeSpecification = native_specification.to_cli_args()

    job_ids = s.runBulkJobs(jt, 1, num_workers, 1)
    array_job_id = job_ids[0].split("_")[0]

    def kill_jobs() -> None:
        try:
            s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
        except Exception as e:
            # Check if the job already finished - if so, this error is expected
            try:
                status = s.jobStatus(array_job_id)
                if status in (drmaa.JobState.DONE, drmaa.JobState.FAILED):
                    return  # Job already finished, nothing to do
            except Exception:
                # If we can't get status, fall back to string matching
                pass

            # FIXME: Hack around issue where drmaa.errors sometimes doesn't
            #        exist.
            error_msg = str(e)
            # These errors occur when workers have already shut down on their own,
            # which isn't actually an error. "Unspecified error" is slurm-drmaa's
            # poor translation of ESLURM_ALREADY_DONE (errno 2021).
            expected_errors = [
                "already completing",
                "Invalid job",
            ]
            if not any(err in error_msg for err in expected_errors):
                raise

    atexit.register(kill_jobs)
