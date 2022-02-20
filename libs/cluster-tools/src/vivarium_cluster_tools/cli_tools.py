"""
================
Shared CLI tools
================

"""
from pathlib import Path
from typing import Callable, List

import click

Decorator = Callable[[Callable], Callable]


def with_verbose_and_pdb(func: Callable) -> Callable:
    func = click.option("-v", "verbose", count=True, help="Configure logging verbosity.")(
        func
    )
    func = click.option(
        "--pdb",
        "with_debugger",
        is_flag=True,
        help="Drop into python debugger if an error occurs.",
    )(func)
    return func


def coerce_to_full_path(ctx: click.Context, param: str, value: str) -> Path:
    if value is not None:
        return Path(value).resolve()


def pass_shared_options(shared_options: List[Decorator]) -> Decorator:
    """Allows the user to supply a list of click options to apply to a command."""

    def _pass_shared_options(func: Callable) -> Callable:
        # add all the shared options to the command
        for option in shared_options:
            func = option(func)
        return func

    return _pass_shared_options
