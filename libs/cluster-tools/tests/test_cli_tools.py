import datetime
import pdb
from bdb import BdbQuit
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from vivarium.cluster_tools.core import cli_tools

DEPRECATION_DATE_SIM_VERBOSITY = datetime.date(2026, 12, 4)


def test_resolve_sim_verbosity_uses_flag_by_default() -> None:
    assert cli_tools.resolve_sim_verbosity(True, None) == 1


def test_resolve_sim_verbosity_deprecated_value_warns() -> None:
    with pytest.warns(FutureWarning):
        assert cli_tools.resolve_sim_verbosity(False, "2") == 2


def test_resolve_sim_verbosity_conflict_raises() -> None:
    with pytest.raises(click.UsageError):
        cli_tools.resolve_sim_verbosity(True, "1")


@pytest.mark.parametrize(
    "args, expected",
    [
        ([], False),
        (["-s"], True),
        # Repeating the flag is accepted and means the same thing.
        (["-ss"], True),
    ],
)
def test_sim_verbosity_is_a_flag(args: list[str], expected: bool) -> None:
    @click.command()
    @cli_tools.with_sim_verbosity
    def dummy(sim_verbosity: bool, sim_verbosity_deprecated: str | None) -> None:
        click.echo(str(sim_verbosity))

    result = CliRunner().invoke(dummy, args)
    assert result.exit_code == 0
    assert result.output.strip() == str(expected)


def test_remove_deprecated_sim_verbosity_option() -> None:
    """Reminder to delete the deprecated ``--sim-verbosity`` value option.

    This fails once the deprecation window closes; when it does, remove the
    ``--sim-verbosity`` option from ``with_sim_verbosity``, drop the deprecated
    branch of ``resolve_sim_verbosity``, and delete this test.
    """
    # Check the deadline first so the reminder fires on every env (including
    # GitHub Actions runners), not just those with the [cluster] extra.
    assert datetime.date.today() < DEPRECATION_DATE_SIM_VERBOSITY, (
        "The deprecation window for psimulate's '--sim-verbosity' option has "
        "closed. Remove the option (and this test)."
    )
    # The real psimulate command pulls in jobmon (the [cluster] extra), which is
    # absent on local/CI envs. Where it is available, sanity check that the
    # deprecated option is in fact still present on the command to be removed.
    pytest.importorskip("jobmon")
    from vivarium.cluster_tools.psimulate.cli import psimulate

    assert "sim_verbosity_deprecated" in {
        param.name for param in psimulate.commands["run"].params
    }


@pytest.mark.parametrize(
    "args, verbose", [([], 0), (["-v"], 1), (["-vv"], 2), (["-v", "-v", "-v"], 3)]
)
def test_verbose_and_pdb_counts_v(args: list[str], verbose: int) -> None:
    @click.command()
    @cli_tools.with_verbose_and_pdb
    def dummy(verbose: int, with_debugger: bool) -> None:
        click.echo(f"{verbose} {with_debugger}")

    result = CliRunner().invoke(dummy, args)
    assert result.exit_code == 0
    assert result.output.strip() == f"{verbose} False"

    help_text = CliRunner().invoke(dummy, ["--help"]).output
    assert "-v" in help_text
    assert "Configure logging verbosity of main runner for a parallel" in help_text


def _fail() -> None:
    raise RuntimeError("boom")


def test_handle_exceptions_returns_the_result() -> None:
    logger = MagicMock()
    wrapped = cli_tools.handle_exceptions(lambda x: x + 1, logger, with_debugger=False)
    assert wrapped(1) == 2
    logger.exception.assert_not_called()


def test_handle_exceptions_logs_and_reraises() -> None:
    logger = MagicMock()
    with pytest.raises(RuntimeError, match="boom"):
        cli_tools.handle_exceptions(_fail, logger, with_debugger=False)()
    logger.exception.assert_called_once_with("Uncaught exception boom")


@pytest.mark.parametrize("error", [KeyboardInterrupt, BdbQuit])
def test_handle_exceptions_passes_interrupts_through(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    post_mortem = MagicMock()
    monkeypatch.setattr(pdb, "post_mortem", post_mortem)
    logger = MagicMock()

    def interrupt() -> None:
        raise error()

    with pytest.raises(error):
        cli_tools.handle_exceptions(interrupt, logger, with_debugger=True)()
    logger.exception.assert_not_called()
    post_mortem.assert_not_called()


def test_handle_exceptions_opens_the_debugger(monkeypatch: pytest.MonkeyPatch) -> None:
    post_mortem = MagicMock()
    monkeypatch.setattr(pdb, "post_mortem", post_mortem)
    logger = MagicMock()
    assert cli_tools.handle_exceptions(_fail, logger, with_debugger=True)() is None
    logger.exception.assert_called_once_with("Uncaught exception boom")
    post_mortem.assert_called_once_with()
