import copy

import numpy as np
import pandas as pd
import pytest
from conftest import assert_equal

from risk_distributions.formatting import Parameter, Parameters
from risk_distributions.risk_distributions import EnsembleDistribution

weights_base = {
    "betasr": 1,
    "exp": 2,
    "gamma": 3,
    "gumbel": 5,
    "invgamma": 7,
    "invweibull": 11,
    "llogis": 13,
    "lnorm": 17,
    "mgamma": 19,
    "mgumbel": 23,
    "norm": 29,
    "weibull": 31,
}

weights_base_missing = copy.deepcopy(weights_base)
del weights_base_missing["exp"]


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    weights = copy.deepcopy(weights)
    total = sum(weights.values())
    for k in weights:
        weights[k] = weights[k] / total
    return weights


@pytest.fixture
def expected_weights() -> pd.DataFrame:
    return pd.DataFrame({k: [v] for k, v in normalize_weights(weights_base).items()})


@pytest.fixture
def expected_weights_missing() -> pd.DataFrame:
    data = pd.DataFrame({k: [v] for k, v in normalize_weights(weights_base_missing).items()})
    data["exp"] = 0.0
    return data


@pytest.mark.parametrize(
    "weights",
    [
        weights_base,
        normalize_weights(weights_base),
        {k: [v] for k, v in weights_base.items()},
        pd.Series(weights_base),
        pd.Series(weights_base).reset_index(drop=True),
        pd.DataFrame({k: [v] for k, v in weights_base.items()}),
        list(weights_base.values()),
        tuple(weights_base.values()),
        np.array(list(weights_base.values())),  # Column Vector
        np.array([list(weights_base.values())]),  # Row Vector
        np.array([list(weights_base.values())]).T,
    ],
)
def test_weight_formats(weights: Parameters, expected_weights: pd.DataFrame) -> None:
    weights_original = copy.deepcopy(weights)
    dist = EnsembleDistribution(
        weights,
        mean=1,
        sd=1,
    )
    assert_equal(weights_original, weights)
    pd.testing.assert_frame_equal(dist.weights, expected_weights)


@pytest.mark.parametrize(
    "weights",
    [
        weights_base_missing,
        normalize_weights(weights_base_missing),
        {k: [v] for k, v in weights_base_missing.items()},
        pd.Series(weights_base_missing),
        pd.DataFrame({k: [v] for k, v in weights_base_missing.items()}),
    ],
)
def test_missing_weights(weights: Parameters, expected_weights_missing: pd.DataFrame) -> None:
    weights_original = copy.deepcopy(weights)
    dist = EnsembleDistribution(
        weights,
        mean=1,
        sd=1,
    )
    assert_equal(weights_original, weights)
    pd.testing.assert_frame_equal(dist.weights, expected_weights_missing)


@pytest.mark.parametrize(
    "weights",
    [
        pd.Series(weights_base_missing).reset_index(drop=True),
        list(weights_base_missing.values()),
        tuple(weights_base_missing.values()),
        np.array(list(weights_base_missing.values())),  # Column Vector
        np.array([list(weights_base_missing.values())]),  # Row Vector
        np.array([list(weights_base_missing.values())]).T,
    ],
)
def test_missing_weights_invalid(weights: Parameters) -> None:
    with pytest.raises(ValueError):
        EnsembleDistribution(
            weights,
            mean=1,
            sd=1,
        )
