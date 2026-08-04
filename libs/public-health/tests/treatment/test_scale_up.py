from typing import Any

import pandas as pd
import pytest
from vivarium.config_tree import ConfigTree
from vivarium.engine import InteractiveContext
from vivarium.engine.framework.lookup import LookupTable

from tests.test_utilities import build_table_with_age
from vivarium.public_health.treatment import LinearScaleUp

START_VALUE = 0.1
END_VALUE = 0.9


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


@pytest.mark.parametrize(
    "value_config, data_sources, endpoints_are_scalar",
    [
        pytest.param({"start": START_VALUE, "end": END_VALUE}, {}, True, id="both_numeric"),
        pytest.param(
            {"start": "data", "end": "data"},
            {"start": START_VALUE, "end": END_VALUE},
            True,
            id="both_data",
        ),
        pytest.param(
            {"start": START_VALUE, "end": "data"},
            {"end": END_VALUE},
            True,
            id="numeric_and_data",
        ),
        pytest.param(
            {"start": "data", "end": "data"},
            {
                "start": build_table_with_age(START_VALUE),
                "end": build_table_with_age(END_VALUE),
            },
            False,
            id="both_data_from_dataframes",
        ),
    ],
)
def test_linear_scale_up_endpoints_build_distinct_lookup_tables(
    base_config: ConfigTree,
    value_config: dict[str, float | str],
    data_sources: dict[str, Any],
    endpoints_are_scalar: bool,
) -> None:
    """Build both endpoints, for each mix of numeric and data endpoints.

    Both endpoint tables were once named ``"endpoint"``, so they registered the
    same framework resource and setup raised a ``ResourceError`` (MIC-7305).
    """
    scale_up = LinearScaleUp("treatment.sqlns")
    base_config.update(
        {
            "intervention": {"scenario": "intervention"},
            scale_up.configuration_key: {
                "value": value_config,
                "data_sources": data_sources,
            },
        },
        source=__file__,
        layer="model_override",
    )

    InteractiveContext(components=[scale_up], configuration=base_config)

    start, end = scale_up.scale_up_start_value, scale_up.scale_up_end_value
    assert start.name == LookupTable.get_name(scale_up.name, "start")
    assert end.name == LookupTable.get_name(scale_up.name, "end")
    assert start.resource_id != end.resource_id

    # Scalar-backed tables broadcast over any index, so they can be read without
    # a population; DataFrame-backed endpoints are interpolated and would need a
    # population's age/sex columns. Reading them proves each endpoint resolves to
    # its own value rather than sharing or swapping the other's table.
    if endpoints_are_scalar:
        index = pd.Index(range(3))
        assert (start(index) == START_VALUE).all()
        assert (end(index) == END_VALUE).all()
