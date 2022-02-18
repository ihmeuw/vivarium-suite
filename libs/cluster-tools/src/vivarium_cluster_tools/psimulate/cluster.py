"""
===================
Cluster Interaction
===================

Tools for interacting with the IHME cluster.

"""
import atexit
import os
import shutil
from pathlib import Path
from typing import Any, List, TextIO

from loguru import logger

from vivarium_cluster_tools.psimulate.environment import ENV_VARIABLES

# Cluster projects under the purview of Simulation Science
PROJECTS = [
    "proj_cost_effect",
    "proj_cost_effect_diarrhea",
    "proj_cost_effect_dcpn",
    "proj_cost_effect_conic",
    "proj_csu",
]
DEFAULT_PROJECT = "proj_cost_effect"

# Cluster specific parameters
ALL_Q_MAX_RUNTIME_HOURS = 3 * 24
LONG_Q_MAX_RUNTIME_HOURS = 16 * 24


def exit_if_on_submit_host() -> None:
    submit_host_marker = "-submit-"
    if submit_host_marker in ENV_VARIABLES.HOSTNAME.value:
        raise RuntimeError("This tool must not be run from a submit host.")


class NativeSpecification:
    def __init__(
        self,
        project: str,
        queue: str,
        peak_memory: int,
        max_runtime: str,
        job_name: str,
        **__,
    ):

        try:
            self.cluster_name = ENV_VARIABLES.CLUSTER_NAME.value
        except KeyError:
            raise RuntimeError("This tool must be run from an SGE/UGE cluster.")

        self.project = project
        self.peak_memory = peak_memory
        self.max_runtime = max_runtime
        self.job_name = job_name
        self.queue = self.get_queue(queue, max_runtime)
        self.threads = 1
        self.qsub_validation = "n"

        self.flag_map = {
            "project": "-P {value}",
            "peak_memory": "-l m_mem_free={value}G",
            "max_runtime": "-l h_rt={value}",
            "threads": "-l fthread={value}",
            "job_name": "-N {value}",
            "queue": "-q {value}",
            "qsub_validation": "-w {value}",
        }

        # base configuration resources
        self.allowed_resources = ["job_name", "queue", "max_runtime", "qsub_validation"]

        # IHME-specific configuration
        if self.cluster_name == "cluster":
            self.allowed_resources.extend(["project", "peak_memory", "threads"])

    def get_queue(self, queue: str, max_runtime: str) -> str:
        valid_queues = self.get_valid_queues(max_runtime)
        if queue is None:
            return valid_queues[0]
        else:
            if queue in valid_queues:
                return queue
            else:
                raise ValueError(
                    "Specified queue is not valid for jobs with the max runtime provided. "
                    "Likely you requested all.q for a job requiring long.q."
                )

    @staticmethod
    def get_valid_queues(max_runtime: str) -> List[str]:
        runtime_args = max_runtime.split(":")

        if len(runtime_args) != 3:
            raise ValueError("Invalid --max-runtime supplied. Format should be hh:mm:ss.")
        else:
            hours, minutes, seconds = runtime_args

        runtime_in_hours = int(hours) + float(minutes) / 60.0 + float(seconds) / 3600.0

        if runtime_in_hours <= ALL_Q_MAX_RUNTIME_HOURS:
            return ["all.q", "long.q"]
        elif runtime_in_hours <= LONG_Q_MAX_RUNTIME_HOURS:
            return ["long.q"]
        else:
            raise ValueError(
                f"Max runtime value too large. Must be less than {LONG_Q_MAX_RUNTIME_HOURS}h."
            )

    def __str__(self) -> str:
        return " ".join(
            [
                self.flag_map[resource].format(value=getattr(self, resource))
                for resource in self.allowed_resources
            ]
        )


def init_job_template(
    jt,  # drmaa.session.JobTemplate.  Not typing because the import is tricky.
    native_specification: NativeSpecification,
    cluster_logging_root: Path,
    worker_launch_script: TextIO,
):  # -> drmaa.session.JobTemplate
    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which("sh")
    jt.args = [worker_launch_script.name]
    jt.outputPath = f":{str(cluster_logging_root)}"
    jt.errorPath = f":{str(cluster_logging_root)}"
    jt.jobEnvironment = {
        "LC_ALL": "en_US.UTF-8",
        "LANG": "en_US.UTF-8",
    }
    jt.joinFiles = True
    jt.nativeSpecification = str(native_specification)
    return jt


def start_cluster(
    num_workers: int,
    worker_launch_script: TextIO,
    cluster_logging_root: Path,
    native_specification: NativeSpecification,
) -> None:
    drmaa = get_drmaa()
    s = drmaa.Session()
    s.initialize()
    jt = init_job_template(
        s.createJobTemplate(),
        native_specification,
        cluster_logging_root,
        worker_launch_script,
    )
    if num_workers:

        def kill_jobs():
            try:
                s.control(array_job_id, drmaa.JobControlAction.TERMINATE)
            # FIXME: Hack around issue where drmaa.errors sometimes doesn't
            #        exist.
            except Exception as e:
                if "There are no jobs registered" in str(e):
                    # This is the case where all our workers have already shut down
                    # on their own, which isn't actually an error.
                    pass
                elif "Discontinued delete" in str(e):
                    # sge has already cleaned up some of the jobs.
                    pass
                else:
                    raise

        job_ids = s.runBulkJobs(jt, 1, num_workers, 1)
        array_job_id = job_ids[0].split(".")[0]
        atexit.register(kill_jobs)


def get_drmaa() -> Any:
    try:
        import drmaa
    except (RuntimeError, OSError):
        if ENV_VARIABLES.CLUSTER_NAME.exists:
            ENV_VARIABLES.DRMAA_LIB_PATH.update("/opt/sge/lib/lx-amd64/libdrmaa.so")
            import drmaa
        else:
            drmaa = object()
    return drmaa


def check_user_sge_config() -> None:
    """Warn if a user has set their stdout and stderr output paths
    in a home directory config file. This overrides settings from py-drmaa."""

    sge_config = Path().home() / ".sge_request"

    if sge_config.exists():
        with sge_config.open("r") as f:
            for line in f:
                line = line.strip()
                if (("-o " in line) or ("-e" in line)) and not line.startswith("#"):
                    logger.warning(
                        "You may have settings in your .sge_request file "
                        "that could overwrite the log location set by this script. "
                        f"Your .sge_request file is here: {sge_config}.  Look for "
                        "-o and -e and comment those lines to receive logs side-by-side"
                        "with the worker logs."
                    )
