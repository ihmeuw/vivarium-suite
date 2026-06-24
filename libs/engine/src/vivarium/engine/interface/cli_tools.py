"""
=========
CLI Tools
=========

Shared :mod:`click` building blocks for ``vivarium`` ecosystem command line
interfaces.  These live here so that every CLI in the suite exposes logging
verbosity the same way.

"""
from collections.abc import Callable

import click

# The argument type hints for the cli wrappers are not precise; the functions
# being wrapped are only ever invoked via the command line, never in a
# type-hinted context.
CLIFunction = Callable[..., None]
Decorator = Callable[[CLIFunction], CLIFunction]

VERBOSE_HELP = "Increase logging verbosity. Use -v for INFO and -vv for DEBUG output."


def verbose_option(help: str = VERBOSE_HELP) -> Decorator:
    """Return a Click decorator adding a ``-v`` count option named ``verbose``.

    The resulting ``verbose`` integer is the canonical verbosity count used
    across the suite: 0 (WARNING), 1 (INFO), 2 or more (DEBUG). Pass it to
    :func:`vivarium.engine.framework.logging.get_log_level` or
    :func:`~vivarium.engine.framework.logging.configure_logging_to_terminal`.

    Parameters
    ----------
    help
        Help text for the option, overridable for command-specific phrasing.
    """

    def decorator(func: CLIFunction) -> CLIFunction:
        return click.option("-v", "verbose", count=True, help=help)(func)

    return decorator
