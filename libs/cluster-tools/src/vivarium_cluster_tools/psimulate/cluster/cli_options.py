"""
===================
Cluster CLI options
===================

Command line options for configuring the cluster environment in psimulate runs.

"""

import click

_RUNTIME_FORMAT = "hh:mm:ss"

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
    default="proj_simscience_prod",
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
    default=3,
    help=(
        "The estimated maximum memory usage in GB of an individual simulate job. "
        "The simulations will be run with this as a limit."
    ),
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
    default="24:00:00",
    help=(
        f"The estimated maximum runtime ({_RUNTIME_FORMAT}) of the simulation jobs. "
        "By default, the cluster will terminate jobs after 24h regardless of "
        "queue. The maximum supported runtime is 3 days. Keep in mind that the "
        "session you are launching from must be able to live at least as long "
        "as the simulation jobs, and that runtimes by node vary wildly."
    ),
    callback=_queue_and_runtime_callback,
)
