import numpy as np
import pandas as pd
import pytest

from risk_distributions import risk_distributions

distributions = [
    risk_distributions.Exponential,
    risk_distributions.Gamma,
    risk_distributions.Gumbel,
    risk_distributions.InverseGamma,
    risk_distributions.InverseWeibull,
    risk_distributions.LogLogistic,
    risk_distributions.LogNormal,
    risk_distributions.Normal,
    risk_distributions.Weibull,
    risk_distributions.Beta,
]

# FIXME: The following distributions break without updates to the processing functions
# risk_distributions.MirroredGumbel, risk_distributions.MirroredGamma,


@pytest.fixture
def test_data():
    test_mean = np.linspace(1, 50, num=100)
    test_sd = np.linspace(1, 10, num=100)
    test_q = np.linspace(0.001, 0.999, num=100)
    return test_mean, test_sd, test_q


@pytest.mark.parametrize("distribution", distributions)
def test_cdf(test_data, distribution):
    mean, sd, test_q = test_data
    test_distribution = distribution(mean=mean, sd=sd)
    x_min, x_max = test_distribution.parameters.x_min, test_distribution.parameters.x_max

    test_x = test_distribution.ppf(test_q)

    #  ppf can generate the value outside of the range(x_min, x_max) which will make nan if we use it in cdf.
    #  thus we only test the value within our range(x_min, x_max)
    computable = (test_x >= x_min) & (test_x <= x_max)
    assert np.allclose(test_q[computable], test_distribution.cdf(test_x)[computable])


@pytest.mark.skip(reason="outdated api usage")
def test_mismatched_mean_sd():
    mean = [5, 4, 2]
    sd = 1.1
    with pytest.raises(ValueError) as error:
        risk_distributions.Normal(mean=mean, sd=sd)

    message = error.value.args[0]

    assert "must be sequences" in message


@pytest.mark.skip(reason="outdated api usage")
def test_get_min_max():
    test_mean = pd.Series([5, 10, 20, 50, 100], index=range(5))
    test_sd = pd.Series([1, 3, 5, 10, 15], index=range(5))
    expected = pd.DataFrame()
    expected["x_min"] = np.array(
        [2.6586837, 3.86641019, 9.06608812, 26.58683698, 62.37010755]
    )
    expected["x_max"] = np.array(
        [9.0414898, 23.72824267, 41.5251411, 90.41489799, 156.80510239]
    )
    test = risk_distributions.BaseDistribution._get_min_max(test_mean, test_sd)

    assert np.allclose(test["x_min"], expected["x_min"])
    assert np.allclose(test["x_max"], expected["x_max"])


# NOTE: This test is to ensure that our math to find the parameters for each distribution is correct.
exposure_levels = [(0, 10, 1), (1, 20, 3), (2, 30, 5), (3, 40, 7)]


@pytest.mark.skip(reason="outdated api usage")
@pytest.mark.parametrize("i, mean, sd", exposure_levels)
def test_individual_distribution_get_params(i, mean, sd):
    expected = dict()
    generated = dict()
    # now look into the details of each distribution parameters
    # this is a dictionary of distributions considered for ensemble distribution

    # Beta
    generated["betasr"] = risk_distributions.Beta.get_params(mean, sd)
    expected["betasr"] = pd.DataFrame()
    expected["betasr"]["scale"] = [6.232114, 18.886999, 31.610845, 44.354704]
    expected["betasr"]["a"] = [3.679690, 3.387153, 3.291559, 3.244209]
    expected["betasr"]["b"] = [4.8479, 5.113158, 5.197285, 5.238462]

    # Exponential
    generated["exp"] = risk_distributions.Exponential.get_params(mean, sd)

    expected["exp"] = pd.DataFrame()
    expected["exp"]["scale"] = [10, 20, 30, 40]

    # Gamma
    generated["gamma"] = risk_distributions.Gamma.get_params(mean, sd)

    expected["gamma"] = pd.DataFrame()
    expected["gamma"]["a"] = [100, 44.444444, 36, 32.653061]
    expected["gamma"]["scale"] = [0.1, 0.45, 0.833333, 1.225]

    # Gumbel
    generated["gumbel"] = risk_distributions.Gumbel.get_params(mean, sd)

    expected["gumbel"] = pd.DataFrame()
    expected["gumbel"]["loc"] = [9.549947, 18.649840, 27.749734, 36.849628]
    expected["gumbel"]["scale"] = [0.779697, 2.339090, 3.898484, 5.457878]

    # InverseGamma
    generated["invgamma"] = risk_distributions.InverseGamma.get_params(mean, sd)

    expected["invgamma"] = pd.DataFrame()
    expected["invgamma"]["a"] = [102.000001, 46.444443, 38.000001, 34.653062]
    expected["invgamma"]["scale"] = [1010.000013, 908.888853, 1110.000032, 1346.122489]

    # LogLogistic
    generated["llogis"] = risk_distributions.LogLogistic.get_params(mean, sd)

    expected["llogis"] = pd.DataFrame()
    expected["llogis"]["c"] = [18.246506, 12.254228, 11.062771, 10.553378]
    expected["llogis"]["d"] = [1, 1, 1, 1]
    expected["llogis"]["scale"] = [9.950669, 19.781677, 29.598399, 39.411819]

    # LogNormal
    generated["lnorm"] = risk_distributions.LogNormal.get_params(mean, sd)

    expected["lnorm"] = pd.DataFrame()
    expected["lnorm"]["s"] = [0.099751, 0.149166, 0.165526, 0.173682]
    expected["lnorm"]["scale"] = [9.950372, 19.778727, 29.591818, 39.401219]

    # MirroredGumbel
    generated["mgumbel"] = risk_distributions.MirroredGumbel.get_params(mean, sd)

    expected["mgumbel"] = pd.DataFrame()
    expected["mgumbel"]["loc"] = [3.092878, 10.010861, 17.103436, 24.240816]
    expected["mgumbel"]["scale"] = [0.779697, 2.339090, 3.898484, 5.457878]

    # MirroredGamma
    generated["mgamma"] = risk_distributions.MirroredGamma.get_params(mean, sd)

    expected["mgamma"] = pd.DataFrame()
    expected["mgamma"]["a"] = [12.552364, 14.341421, 14.982632, 15.311779]
    expected["mgamma"]["scale"] = [0.282252, 0.792182, 1.291743, 1.788896]

    # Normal
    generated["norm"] = risk_distributions.Normal.get_params(mean, sd)

    expected["norm"] = pd.DataFrame()
    expected["norm"]["loc"] = [10, 20, 30, 40]
    expected["norm"]["scale"] = [1, 3, 5, 7]

    # Weibull
    generated["weibull"] = risk_distributions.Weibull.get_params(mean, sd)

    expected["weibull"] = pd.DataFrame()
    expected["weibull"]["c"] = [12.153402, 7.906937, 7.061309, 6.699559]
    expected["weibull"]["scale"] = [10.430378, 21.249309, 32.056036, 42.859356]

    for dist in expected.keys():
        for params in expected[dist].keys():
            assert np.isclose(expected[dist][params][i], generated[dist][params])
