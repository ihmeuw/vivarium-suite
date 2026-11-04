import pdb
from bdb import BdbQuit
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from vivarium.cluster_tools.core import cli_tools


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
