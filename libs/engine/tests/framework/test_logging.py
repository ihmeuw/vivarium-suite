import datetime
import io
import re

import click
import pytest
from click.testing import CliRunner
from loguru import logger

from vivarium.engine.framework.logging import add_logging_sink, get_log_level
from vivarium.engine.interface.cli import simulate
from vivarium.engine.interface.cli_tools import verbose_option

DEPRECATION_DATE_QUIET_OPTION = datetime.date(2026, 12, 4)


@pytest.mark.parametrize(
    "verbosity, expected_level",
    [
        (-1, "WARNING"),
        (0, "WARNING"),
        (1, "INFO"),
        (2, "DEBUG"),
        (5, "DEBUG"),
    ],
)
def test_get_log_level_clamps(verbosity: int, expected_level: str) -> None:
    assert get_log_level(verbosity) == expected_level


@pytest.mark.parametrize(
    "args, expected_count",
    [
        ([], 0),
        (["-v"], 1),
        (["-vv"], 2),
        (["-vvv"], 3),
    ],
)
def test_verbose_option_counts(args: list[str], expected_count: int) -> None:
    @click.command()
    @verbose_option()
    def dummy(verbose: int) -> None:
        click.echo(str(verbose))

    result = CliRunner().invoke(dummy, args)
    assert result.exit_code == 0
    assert result.output.strip() == str(expected_count)


def test_log_format_includes_function() -> None:
    sink = io.StringIO()
    sink_id = add_logging_sink(
        sink, verbosity=1, long_format=False, colorize=False, serialize=False
    )
    try:
        logger.info("hello")
    finally:
        logger.remove(sink_id)
    # The short format is name:function:line; the emitting function is this test.
    assert ":test_log_format_includes_function:" in sink.getvalue()


def test_log_format_includes_elapsed() -> None:
    sink = io.StringIO()
    sink_id = add_logging_sink(
        sink, verbosity=1, long_format=False, colorize=False, serialize=False
    )
    try:
        logger.info("hello")
    finally:
        logger.remove(sink_id)
    # The shared format includes an elapsed-time column (H:MM:SS.ffffff).
    assert re.search(r"\| \d+:\d{2}:\d{2}\.\d+ \|", sink.getvalue())


def test_remove_deprecated_quiet_option() -> None:
    """Reminder to delete the deprecated ``simulate run --quiet/-q`` option.

    This fails once the deprecation window closes; when it does, remove the
    ``--quiet``/``-q`` option and its handling from ``cli.run`` and delete this
    test.
    """
    assert datetime.date.today() < DEPRECATION_DATE_QUIET_OPTION, (
        "The deprecation window for 'simulate run --quiet/-q' has closed. Remove "
        "the option (and this test)."
    )
    # Sanity check that the option is in fact still present to be removed.
    assert "quiet" in {param.name for param in simulate.commands["run"].params}
