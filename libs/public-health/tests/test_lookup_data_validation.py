"""Guard the shared mocked-data helper against the engine's lookup validation.

The rest of the public-health suite runs with ``interpolation.validate=False`` for
speed (set in ``base_config_factory``), which skips the engine's per-lookup-table
completeness check on every sim build. These tests re-enable ``validate=True`` once
so that a regression in ``build_table_with_age`` -- or the engine silently ceasing
to validate lookup data -- is caught here rather than passing silently everywhere.

They drive validation through the public ``builder.lookup.build_table`` API, so they
do not depend on the internal ``check_data_complete`` name or signature.
"""

import pandas as pd
import pytest
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.engine import Builder

from tests.test_utilities import build_table_with_age


class _LookupValidationCanary(Component):
    """Build a lookup table from ``data`` at setup, triggering interpolation validation."""

    def __init__(self, data: pd.DataFrame) -> None:
        super().__init__()
        self._data = data

    def setup(self, builder: Builder) -> None:
        builder.lookup.build_table(self._data, "canary", ["value"])


def _build_with_validation(data: pd.DataFrame, base_config_factory, base_plugins) -> None:
    """Build a one-component sim that validates ``data`` as a lookup table."""
    config = base_config_factory()
    config.update({"interpolation": {"validate": True}})
    sim = InteractiveContext(
        components=[_LookupValidationCanary(data)],
        configuration=config,
        plugin_configuration=base_plugins,
        setup=False,
    )
    sim.setup()


@pytest.mark.parametrize(
    "parameter_columns",
    [{"year": (1990, 2020)}, {"age": (0, 125)}],
    ids=["age_and_year", "age_only"],
)
def test_build_table_with_age_passes_engine_validation(
    base_config_factory, base_plugins, parameter_columns
) -> None:
    """``build_table_with_age`` output passes the engine's interpolation completeness check."""
    data = build_table_with_age(1.0, parameter_columns=dict(parameter_columns))
    _build_with_validation(data, base_config_factory, base_plugins)


def test_engine_validation_is_actually_active(base_config_factory, base_plugins) -> None:
    """A deliberately incomplete table is rejected, proving ``validate=True`` really validates.

    The positive test would still pass if the engine stopped validating (valid data
    passes either way), so this guards that the validation path is live.
    """
    data = build_table_with_age(1.0, parameter_columns={"year": (1990, 2020)})
    incomplete = data.iloc[1:]  # drop a row -> a missing age/year/sex combination
    with pytest.raises(ValueError):
        _build_with_validation(incomplete, base_config_factory, base_plugins)
