import pandas as pd
from vivarium.config_tree import ConfigTree

from tests.test_utilities import build_table_with_age
from vivarium.public_health.treatment import LinearScaleUp


def test_linear_scale_up_instantiation():
    scale_up = LinearScaleUp("treatment.sqlns")

    assert scale_up.treatment == "treatment.sqlns"


def _build_table_call_arg(call, key: str, position: int):
    """Return a positional-or-keyword argument from a ``builder.lookup.build_table`` call."""
    if key in call.kwargs:
        return call.kwargs[key]
    return call.args[position]


def test_linear_scale_up_endpoint_value_from_config_dataframe(mocker):
    """A config-supplied DataFrame is passed through as an endpoint's data source (no artifact load)."""
    exposure = build_table_with_age(0.4)
    scale_up = LinearScaleUp("treatment.sqlns")
    builder = mocker.Mock()
    builder.data.load = mocker.Mock()
    builder.configuration = ConfigTree(
        {scale_up.configuration_key: {"data_sources": {"start": exposure}}}
    )

    scale_up.get_endpoint_value_from_data(builder, "start")

    builder.data.load.assert_not_called()
    pd.testing.assert_frame_equal(
        _build_table_call_arg(builder.lookup.build_table.call_args, "data", 0), exposure
    )
