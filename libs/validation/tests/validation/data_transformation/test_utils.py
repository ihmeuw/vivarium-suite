"""Tests for :mod:`vivarium.validation.data_transformation.utils`."""
from vivarium.validation.data_transformation.utils import VIVARIUM_COLUMNS


def test_vivarium_columns_matches_upstream() -> None:
    """Verify the local column list still matches vivarium-inputs.

    ``VIVARIUM_COLUMNS`` is duplicated locally so the non-GBD schema path works
    without the artifactory-only ``gbd`` extra. This guards the copy against
    upstream drift; it only runs where ``vivarium-inputs`` is installed, which
    the package-level conftest already gates on.
    """
    from vivarium_inputs.globals import VIVARIUM_COLUMNS as upstream

    assert VIVARIUM_COLUMNS == list(upstream)
