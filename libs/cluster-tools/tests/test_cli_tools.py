import datetime

import click
import pytest
from click.testing import CliRunner

from vivarium.cluster_tools.core import cli_tools

DEPRECATION_DATE_SIM_VERBOSITY = datetime.date(2026, 12, 4)


def test_resolve_sim_verbosity_uses_count_by_default() -> None:
    assert cli_tools.resolve_sim_verbosity(2, None) == 2


def test_resolve_sim_verbosity_deprecated_value_warns() -> None:
    with pytest.warns(FutureWarning):
        assert cli_tools.resolve_sim_verbosity(0, "2") == 2


def test_resolve_sim_verbosity_conflict_raises() -> None:
    with pytest.raises(click.UsageError):
        cli_tools.resolve_sim_verbosity(2, "1")


@pytest.mark.parametrize(
    "args, expected",
    [
        ([], 0),
        (["-s"], 1),
        (["-ss"], 2),
    ],
)
def test_sim_verbosity_is_a_count(args: list[str], expected: int) -> None:
    @click.command()
    @cli_tools.with_sim_verbosity
    def dummy(sim_verbosity: int, sim_verbosity_deprecated: str | None) -> None:
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
