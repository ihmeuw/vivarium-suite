import io
import re

import click
import pytest
from click.testing import CliRunner
from loguru import logger

from vivarium.engine.framework.engine import SimulationContext
from vivarium.engine.framework.logging import add_logging_sink, get_log_level
from vivarium.engine.framework.logging import manager as logging_manager
from vivarium.engine.framework.logging.manager import LoggingManager
from vivarium.engine.interface.cli_tools import verbose_option
from vivarium.engine.interface.interactive import InteractiveContext


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


@pytest.fixture
def requested_verbosity(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the verbosity each constructed context asks to configure.

    Terminal logging is configured at most once per process, so a second context
    would otherwise be a no-op and the requested verbosity unobservable.
    """
    recorded: list[int] = []

    def record(verbosity: int, long_format: bool) -> None:
        recorded.append(verbosity)

    monkeypatch.setattr(logging_manager, "configure_logging_to_terminal", record)
    monkeypatch.setattr(
        LoggingManager, "_terminal_logging_not_configured", staticmethod(lambda: True)
    )
    return recorded


def test_interactive_context_is_quiet_by_default(requested_verbosity: list[int]) -> None:
    InteractiveContext(setup=False)
    assert requested_verbosity == [0]


@pytest.mark.parametrize("verbosity", [0, 1, 2])
def test_interactive_context_honors_explicit_verbosity(
    requested_verbosity: list[int], verbosity: int
) -> None:
    InteractiveContext(setup=False, logging_verbosity=verbosity)
    assert requested_verbosity == [verbosity]


def test_simulation_context_default_verbosity_is_unchanged(
    requested_verbosity: list[int],
) -> None:
    """Non-interactive runs still log info messages, including the per-step time."""
    SimulationContext()
    assert requested_verbosity == [1]
