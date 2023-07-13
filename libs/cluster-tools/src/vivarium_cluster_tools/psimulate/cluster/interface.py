"""
=================
Cluster Interface
=================

"""

import atexit
import os
import shutil
from pathlib import Path
from typing import NamedTuple, TextIO

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
    peak_memory: str
    max_runtime: str

    # Class constant
    NUM_THREADS: int = 1

    def to_cli_args(self):
        return (
            f"-J {self.job_name} "
            f"-A {self.project} "
            f"-p {self.queue} "
            f"--mem={self.peak_memory*1024} "
            f"-t {self.max_runtime} "
            f"-c {self.NUM_THREADS}"
        )


def submit_worker_jobs(
    num_workers: int,
    worker_launch_script: TextIO,
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
    array_job_id = job_ids[0].split(".")[0]

    def kill_jobs() -> None:
        try:
            s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
        # FIXME: Hack around issue where drmaa.errors sometimes doesn't
        #        exist.
        except Exception as e:
            if "already completing" in str(e) or "Invalid job" in str(e):
                # This is the case where all our workers have already shut down
                # on their own, which isn't actually an error.
                pass
            else:
                raise

    atexit.register(kill_jobs)
