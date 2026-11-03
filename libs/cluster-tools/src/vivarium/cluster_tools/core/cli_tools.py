"""
================
Shared CLI tools
================

"""

import functools
from bdb import BdbQuit
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

# NOTE: The argument type hints for the cli wrappers are not precise; they should
# be type-hinted using Protocols. However, the functions being wrapped are never
# expected to be called in a type-hinted context (because they are used via CLI).
CLIFunction = Callable[..., None]
Decorator = Callable[[CLIFunction], CLIFunction]


def handle_exceptions(
    func: Callable[..., Any], logger: Any, with_debugger: bool
) -> Callable[..., Any]:
    """Wrap ``func`` to log an uncaught exception and re-raise it or open a debugger.

    ``BdbQuit`` and ``KeyboardInterrupt`` propagate without being logged.

    Parameters
    ----------
    func
        The function to wrap.
    logger
        The logger whose ``exception`` method records the uncaught exception.
    with_debugger
        Whether to open a post-mortem debugger instead of re-raising.

    Returns
    -------
        The wrapped function.
    """

    @functools.wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (BdbQuit, KeyboardInterrupt):
            raise
        except Exception as e:
            logger.exception("Uncaught exception {}".format(e))
            if with_debugger:
                import pdb
                import traceback

                traceback.print_exc()
                pdb.post_mortem()
            else:
                raise

    return wrapped


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


def with_slack_channel(func: CLIFunction) -> CLIFunction:
    """Decorator that adds the ``--slack-channel`` option for completion notifications."""
    return click.option(
        "--slack-channel",
        default=None,
        help="Slack channel name (e.g. 'my-channel') to post the completion "
        "notification to instead of direct-messaging the launching user. The "
        "Slack bot must already be a member of the channel to post there.",
    )(func)


def with_slack_tag(func: CLIFunction) -> CLIFunction:
    """Decorator that adds the ``--slack-tag`` option for completion notifications."""
    return click.option(
        "--slack-tag",
        default=None,
        help="Username to @-mention in the channel notification on success. "
        "Requires --slack-channel; ignored on failure, which always "
        "direct-messages the launching user.",
    )(func)


def with_slack_mute(func: CLIFunction) -> CLIFunction:
    """Decorator that adds the ``--no-slack`` flag to suppress the notification."""
    return click.option(
        "--no-slack",
        "mute_slack",
        is_flag=True,
        default=False,
        help="Suppress the Slack completion notification entirely.",
    )(func)


def validate_slack_options(
    slack_channel: str | None, slack_tag: str | None, mute_slack: bool = False
) -> None:
    """Validate the mutual constraints among the Slack notification options.

    Raises a ``UsageError`` if ``--no-slack`` is combined with a channel or tag,
    or if ``--slack-tag`` is used without ``--slack-channel``.
    """
    if mute_slack and (slack_channel is not None or slack_tag is not None):
        raise click.UsageError(
            "--no-slack cannot be combined with --slack-channel or --slack-tag."
        )
    if slack_tag is not None and slack_channel is None:
        raise click.UsageError("--slack-tag requires --slack-channel to be provided.")


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
