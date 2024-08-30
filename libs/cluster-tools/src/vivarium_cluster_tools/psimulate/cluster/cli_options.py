"""
===================
Cluster CLI options
===================

Command line options for configuring the cluster environment in psimulate runs.

"""

from typing import List, Optional

import click

_RUNTIME_FORMAT = "hh:mm:ss"
MAX_RUNTIME_DEFAULT = "24:00:00"
PEAK_MEMORY_DEFAULT = 3  # GB

# https://docs.cluster.ihme.washington.edu/#hpc-execution-host-hardware-specifications
_AVAILABLE_HARDWARE = [
    "c6320",  # typical
    "r630",  # high capacity
    "c6420v1",  # batch 1
    "c6420v2",  # batch 2
    "r650",  # high capacity
    "r650v2",  # high capacity
    "r650xs",  # high speed
]


def _validate_and_split_hardware(
    ctx: click.Context, param: click.core.Option, value: Optional[str]
) -> List[Optional[str]]:
    hardware = value.split(",") if value else []
    bad_requests = set(hardware) - set(_AVAILABLE_HARDWARE)
    if bad_requests:
        raise click.BadParameter(
            f"Hardware request(s) {bad_requests} are not supported.\n"
            f"Supported hardware requests: {_AVAILABLE_HARDWARE}.\n"
            "Refer to https://docs.cluster.ihme.washington.edu/#hpc-execution-host-hardware-specifications"
        )
    return hardware


with_project = click.option(
    "--project",
    "-P",
    type=click.Choice(
        [
            "proj_simscience",
            "proj_simscience_prod",
            "proj_csu",
        ]
    ),
    required=True,
    help="The cluster project under which to run the simulation.",
)


def with_queue_and_max_runtime(func):
    """Provide a single decorator for both queue and max runtime
    since they are tightly coupled.

    """
    func = _with_queue(func)
    func = _with_max_runtime(func)
    return func


with_peak_memory = click.option(
    "--peak-memory",
    "-m",
    type=int,
    default=PEAK_MEMORY_DEFAULT,
    show_default=True,
    help=(
        "The memory request in GB of each individual simulation job. "
        "The simulations will be killed if they exceed this limit."
    ),
)


with_hardware = click.option(
    "--hardware",
    "-h",
    help=(
        "The (comma-separated) specific hardware to run the jobs on. This can be useful to request "
        "specifically fast nodes ('-h r650xs') vs high capacity nodes ('-h r630,r650,r650v2'). "
        "Note that the hardware changes on a roughly annual schedule. "
        f"The currently-supported options are: {_AVAILABLE_HARDWARE}. "
        "For details, refer to: https://docs.cluster.ihme.washington.edu/#hpc-execution-host-hardware-specifications"
    ),
    callback=_validate_and_split_hardware,
)


def _queue_and_runtime_callback(ctx: click.Context, param: str, value: str) -> str:
    if param.name == "queue" and "max_runtime" in ctx.params:
        runtime_string, queue = _validate_runtime_and_queue(ctx.params["max_runtime"], value)
        ctx.params["max_runtime"], value = runtime_string, queue
    elif param.name == "max_runtime" and "queue" in ctx.params:
        runtime_string, queue = _validate_runtime_and_queue(value, ctx.params["queue"])
        value, ctx.params["queue"] = runtime_string, queue
    else:
        pass
    return value


def _validate_runtime_and_queue(runtime_string: str, queue: str):
    try:
        hours, minutes, seconds = runtime_string.split(":")
    except ValueError:
        raise click.BadParameter(
            f"Invalid --max-runtime supplied. Format should be {_RUNTIME_FORMAT}."
        )
    total_hours = int(hours) + float(minutes) / 60.0 + float(seconds) / 3600.0

    # Sorted from shortest to longest.
    max_runtime_map = {
        "all.q": 3 * 24,
        "long.q": 16 * 24,
    }
    max_runtime = max(max_runtime_map.values())
    if max_runtime < total_hours:
        raise click.BadParameter(
            f"Invalid --max-runtime {runtime_string} supplied. "
            f"Max cluster runtime is {max_runtime}:00:00 with format {_RUNTIME_FORMAT}."
        )
    elif queue in max_runtime_map and max_runtime_map[queue] < total_hours:
        raise click.BadParameter(
            f"Invalid combination of --max-runtime {runtime_string} and "
            f"--queue {queue} specified. Max runtime for the {queue} is "
            f"{max_runtime_map[queue]}:00:00 with format {_RUNTIME_FORMAT}."
        )
    elif queue in max_runtime_map:
        # Things are peachy
        pass
    else:
        # We need to set a default based on the runtime.
        assert queue is None
        # First queue we qualify for.
        queue = [
            q
            for q, max_queue_runtime in max_runtime_map.items()
            if total_hours <= max_queue_runtime
        ][0]

    return runtime_string, queue


_with_queue = click.option(
    "--queue",
    "-q",
    type=click.Choice(["all.q", "long.q"]),
    default=None,
    help="The cluster queue to assign psimulate jobs to. Queue defaults to the "
    "appropriate queue based on max-runtime. long.q allows for much longer "
    "runtimes but there may be reasons to send jobs to that queue even "
    "if they don't have runtime constraints, such as node availability.",
    callback=_queue_and_runtime_callback,
)
_with_max_runtime = click.option(
    "--max-runtime",
    "-r",
    type=str,
    default=MAX_RUNTIME_DEFAULT,
    show_default=True,
    help=(
        f"The runtime request ({_RUNTIME_FORMAT}) of each individual simulation job. "
        "The maximum supported runtime is 3 days. Keep in mind that the "
        "session you are launching from must be able to live at least as long "
        "as the simulation jobs and that runtimes by node vary wildly."
    ),
    callback=_queue_and_runtime_callback,
)
