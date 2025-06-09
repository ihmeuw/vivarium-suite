"""
================
Shared CLI tools
================

"""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import click

# NOTE: The argument type hints for the cli wrappers are not precise; they should
# be type-hinted using Protocols. However, the functions being wrapped are never
# expected to be called in a type-hinted context (because they are used via CLI).
CLIFunction = Callable[..., None]
Decorator = Callable[[CLIFunction], CLIFunction]


def with_verbose_and_pdb(func: CLIFunction) -> CLIFunction:
    func = click.option(
        "-v",
        "verbose",
        count=True,
        help="Configure logging verbosity of main runner for a parallel simulation.",
    )(func)
    func = click.option(
        "--pdb",
        "with_debugger",
        is_flag=True,
        help="Drop into python debugger if an error occurs.",
    )(func)
    return func


def with_sim_verbosity(func: CLIFunction) -> CLIFunction:
    func = click.option(
        "--sim-verbosity",
        "-s",
        type=click.Choice(
            [
                "0",
                "1",
                "2",
            ],
        ),
        required=False,
        default="0",
        show_default=True,
        help="Logging verbosity level of each individual simulation.",
    )(func)
    return func


def coerce_to_full_path(
    ctx: click.Context, param: click.Parameter | None, value: str | None
) -> Path | None:
    if value is not None:
        return Path(value).resolve()
    return None


def pass_shared_options(shared_options: list[Decorator]) -> Decorator:
    """Allows the user to supply a list of click options to apply to a command."""

    def _pass_shared_options(func: CLIFunction) -> CLIFunction:
        # add all the shared options to the command
        for option in shared_options:
            func = option(func)
        return func

    return _pass_shared_options


class MinutesOrNone(click.ParamType):
    """Click param type to allow user to set time in minutes or None."""

    name = "minutesornone"

    def convert(
        self, value: str, param: click.Parameter | None, ctx: click.Context | None
    ) -> float | None:
        """Converts the value to float seconds from minutes.

        If conversion fails, calls the `fail` method from `click.ParamType`.
        """
        try:
            if value.lower() == "none":
                return None
            # Convert minutes to seconds
            return float(value) * 60.0
        except ValueError:
            self.fail(f"{value!r} is not a valid float or 'none'", param, ctx)


MINUTES_OR_NONE = MinutesOrNone()
