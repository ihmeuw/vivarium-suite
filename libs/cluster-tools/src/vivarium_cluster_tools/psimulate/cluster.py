"""
===================
Cluster Interaction
===================

Tools for interacting with the IHME cluster.

"""
import atexit
import math
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Tuple

from loguru import logger

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

DEFAULT_JOBS_PER_REDIS_INSTANCE = 1000


class EnvVariable:
    """Convenience wrapper around an environment variable."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> str:
        return os.environ[self.name]

    @property
    def exists(self) -> bool:
        return self.name in os.environ

    def update(self, value: str) -> None:
        os.environ[self.name] = value


class __EnvVariables(NamedTuple):
    CLUSTER_NAME: EnvVariable
    HOSTNAME: EnvVariable
    JOB_NAME: EnvVariable
    JOB_ID: EnvVariable
    TASK_ID: EnvVariable
    VIVARIUM_LOGGING_DIRECTORY: EnvVariable
    RQ_WORKER_ID: EnvVariable
    RQ_JOB_ID: EnvVariable
    DRMAA_LIB_PATH: EnvVariable
    PYTHONPATH: EnvVariable


ENV_VARIABLES = __EnvVariables(
    CLUSTER_NAME=EnvVariable("SGE_CLUSTER_NAME"),
    HOSTNAME=EnvVariable("HOSTNAME"),
    JOB_NAME=EnvVariable("JOB_NAME"),
    JOB_ID=EnvVariable("JOB_ID"),
    TASK_ID=EnvVariable("TASK_ID"),
    VIVARIUM_LOGGING_DIRECTORY=EnvVariable("VIVARIUM_LOGGING_DIRECTORY"),
    RQ_WORKER_ID=EnvVariable("RQ_WORKER_ID"),
    RQ_JOB_ID=EnvVariable("RQ_JOB_ID"),
    DRMAA_LIB_PATH=EnvVariable("DRMAA_LIB_PATH"),
    PYTHONPATH=EnvVariable("PYTHONPATH"),
)


def exit_if_on_submit_host():
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

    def __str__(self):
        return " ".join(
            [
                self.flag_map[resource].format(value=getattr(self, resource))
                for resource in self.allowed_resources
            ]
        )


def init_job_template(
    jt,
    native_specification: NativeSpecification,
    sge_log_directory: Path,
    worker_log_directory: Path,
    worker_settings_file: Path,
):
    launcher = tempfile.NamedTemporaryFile(
        mode="w",
        dir=".",
        prefix="vivarium_cluster_tools_launcher_",
        suffix=".sh",
        delete=False,
    )
    atexit.register(lambda: os.remove(launcher.name))
    output_dir = str(worker_settings_file.resolve().parent)
    launcher.write(
        f"""
    export {ENV_VARIABLES.VIVARIUM_LOGGING_DIRECTORY.name}={worker_log_directory}
    export {ENV_VARIABLES.PYTHONPATH.name}={output_dir}:${ENV_VARIABLES.PYTHONPATH.name}

    {shutil.which('rq')} worker -c {worker_settings_file.stem} \
        --name ${{{ENV_VARIABLES.JOB_ID.name}}}.${{{ENV_VARIABLES.TASK_ID.name}}} \
        --burst \
        -w "vivarium_cluster_tools.psimulate.distributed_worker.ResilientWorker" \
        --exception-handler "vivarium_cluster_tools.psimulate.distributed_worker.retry_handler" vivarium

    """
    )
    launcher.close()

    jt.workingDirectory = os.getcwd()
    jt.remoteCommand = shutil.which("sh")
    jt.args = [launcher.name]
    jt.outputPath = f":{sge_log_directory}"
    jt.errorPath = f":{sge_log_directory}"
    jt.jobEnvironment = {
        "LC_ALL": "en_US.UTF-8",
        "LANG": "en_US.UTF-8",
    }
    jt.joinFiles = True
    jt.nativeSpecification = str(native_specification)
    return jt


def get_random_free_port() -> int:
    # NOTE: this implementation is vulnerable to rare race conditions where some other process gets the same
    # port after we free our socket but before we use the port number we got. Should be so rare in practice
    # that it doesn't matter.
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def launch_redis(port: int, redis_logging_root: Path) -> subprocess.Popen:
    log = (redis_logging_root / f"redis.p{port}.log").open("a")
    log.write(f">>>>>>>> Starting log for redis-server on port {port}\n")
    log.flush()
    try:
        # inline config for redis server.
        redis_process = subprocess.Popen(
            [
                "redis-server",
                "--port",
                f"{port}",
                "--timeout",
                "2",
                "--loglevel",
                "debug",
                # "--daemonize", "yes",
                "--protected-mode",
                "no",
            ],
            stdout=log,
            stderr=log,
        )
    except FileNotFoundError:
        raise OSError(
            "In order for redis to launch you need both the redis client and the python bindings. "
            "You seem to be missing the redis client.  Do 'conda install redis' and try again. If "
            "failures continue you may need to download redis yourself, make it and add it to PATH."
        )
    atexit.register(redis_process.kill)
    return redis_process


def launch_redis_processes(
    num_processes: int,
    num_jobs: int,
    redis_logging_root: Path,
) -> Tuple[str, List[Tuple[str, int]]]:
    if num_processes == -1:
        num_processes = int(math.ceil(num_jobs / DEFAULT_JOBS_PER_REDIS_INSTANCE))

    hostname = socket.getfqdn()
    redis_ports = []
    for i in range(num_processes):
        port = get_random_free_port()
        logger.info(f"Starting Redis Broker at {hostname}:{port}")
        launch_redis(port, redis_logging_root)
        redis_ports.append((hostname, port))

    redis_urls = [f"redis://{hostname}:{port}" for hostname, port in redis_ports]
    worker_config = (
        f"import random\nredis_urls = {redis_urls}\nREDIS_URL = random.choice(redis_urls)\n\n"
    )
    return worker_config, redis_ports


def start_cluster(
    num_workers: int,
    sge_log_directory: Path,
    worker_log_directory: Path,
    worker_settings_file: Path,
    native_specification: NativeSpecification,
):
    drmaa = get_drmaa()
    s = drmaa.Session()
    s.initialize()
    jt = init_job_template(
        s.createJobTemplate(),
        native_specification,
        sge_log_directory,
        worker_log_directory,
        worker_settings_file,
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


def get_drmaa():
    try:
        import drmaa
    except (RuntimeError, OSError):
        if ENV_VARIABLES.CLUSTER_NAME.exists:
            ENV_VARIABLES.DRMAA_LIB_PATH.update("/opt/sge/lib/lx-amd64/libdrmaa.so")
            import drmaa
        else:
            drmaa = object()
    return drmaa


def check_user_sge_config():
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
