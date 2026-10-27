"""Tests for the shared staleness test harness (``core.harness``)."""


def test_build_fixture_runs_and_writes_its_products() -> None:
    """The fixture builds a runnable pipeline; running it succeeds and writes products."""


def test_would_run_reports_the_step_names() -> None:
    """would_run returns a set of the fixture's step names drawn from a preview."""


def test_perturbed_changes_a_file_then_restores_it() -> None:
    """Perturbed changes the file's bytes inside the block and restores them on exit."""


def test_perturbed_restores_even_when_the_block_raises() -> None:
    """Perturbed restores the original bytes even if the with-block raises."""
