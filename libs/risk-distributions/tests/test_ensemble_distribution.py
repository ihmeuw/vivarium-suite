import copy

import numpy as np
import pandas as pd
import pytest
from conftest import assert_equal

from risk_distributions.formatting import Parameter, Parameters
from risk_distributions.risk_distributions import EnsembleDistribution

weights_base = {
    "betasr": 1 / 12,
    "exp": 1 / 12,
    "gamma": 1 / 12,
    "gumbel": 1 / 12,
    "invgamma": 1 / 12,
    "invweibull": 1 / 12,
    "llogis": 1 / 12,
    "lnorm": 1 / 12,
    "mgamma": 1 / 12,
    "mgumbel": 1 / 12,
    "norm": 1 / 12,
    "weibull": 1 / 12,
}

weights_df = pd.DataFrame({k: [v] for k, v in weights_base.items()})


@pytest.mark.parametrize(
    "weights",
    [
        weights_base,
        {k: [v] for k, v in weights_base.items()},
        pd.Series(weights_base),
        pd.Series(weights_base).reset_index(drop=True),
        weights_df,
        list(weights_base.values()),
        tuple(weights_base.values()),
        np.array(list(weights_base.values())),  # Column Vector
        np.array([list(weights_base.values())]),  # Row Vector
        np.array([list(weights_base.values())]).T,
    ],
)
def test_weight_formats(weights: Parameters) -> None:
    weights_original = copy.deepcopy(weights)
    dist = EnsembleDistribution(
        weights,
        mean=1,
        sd=1,
    )
    assert_equal(weights_original, weights)
    pd.testing.assert_frame_equal(dist.weights, pd.DataFrame(weights_df))
