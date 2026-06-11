"""
===================
Cluster CLI options
===================

Command line options for configuring the cluster environment in psimulate runs.

"""

from __future__ import annotations

import click

from vivarium.cluster_tools.core.cli_tools import CLIFunction
from vivarium.cluster_tools.core.cluster.validation import (
    AVAILABLE_HARDWARE,
    QUEUE_MAX_RUNTIME_HOURS,
    RUNTIME_FORMAT,
    VALID_PROJECTS,
    validate_hardware,
    validate_runtime_and_queue,
)

MAX_RUNTIME_DEFAULT = "24:00:00"
PEAK_MEMORY_DEFAULT = 3  # GB


def _validate_and_split_hardware(
    ctx: click.Context, param: click.core.Option, value: str | None
) -> list[str]:
    hardware: list[str] = value.split(",") if value else []
    try:
        validate_hardware(hardware)
    except ValueError as e:
        raise click.BadParameter(str(e))
    return hardware


with_project = click.option(
    "--project",
    "-P",
    type=click.Choice(VALID_PROJECTS),
    required=True,
    help="The cluster project under which to run the simulation.",
)


def with_queue_and_max_runtime(func: CLIFunction) -> CLIFunction:
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
        f"The currently-supported options are: {AVAILABLE_HARDWARE}. "
        "For details, refer to: https://docs.cluster.ihme.washington.edu/#hpc-execution-host-hardware-specifications"
    ),
    callback=_validate_and_split_hardware,
)


def _queue_and_runtime_callback(
    ctx: click.Context, param: click.core.Parameter, value: str
) -> str:
    try:
        if param.name == "queue" and "max_runtime" in ctx.params:
            runtime_string, queue = validate_runtime_and_queue(
                ctx.params["max_runtime"], value
            )
            ctx.params["max_runtime"], value = runtime_string, queue
        elif param.name == "max_runtime" and "queue" in ctx.params:
            runtime_string, queue = validate_runtime_and_queue(value, ctx.params["queue"])
            value, ctx.params["queue"] = runtime_string, queue
    except ValueError as e:
        raise click.BadParameter(str(e))
    return value


_with_queue = click.option(
    "--queue",
    "-q",
    type=click.Choice(sorted(QUEUE_MAX_RUNTIME_HOURS)),
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
        f"The runtime request ({RUNTIME_FORMAT}) of each individual simulation job. "
        "The maximum supported runtime is 3 days. Keep in mind that the "
        "session you are launching from must be able to live at least as long "
        "as the simulation jobs and that runtimes by node vary wildly."
    ),
    callback=_queue_and_runtime_callback,
)
