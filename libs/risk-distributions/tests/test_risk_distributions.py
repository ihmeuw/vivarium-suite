import pandas as pd
import numpy as np
import pytest

from risk_distributions import risk_distributions


def test_import():
    from risk_distributions import risk_distributions

# NOTE: This test is to ensure that our math to find the parameters for each distribution is correct.
exposure_levels = [(0, 10, 1), (1, 20, 3), (2, 30, 5), (3, 40, 7)]
@pytest.mark.parametrize('i, mean, sd', exposure_levels)
def test_individual_distribution_get_params(i, mean, sd):
    expected = dict()
    generated = dict()
    # now look into the details of each distribution parameters
    # this is a dictionary of distributions considered for ensemble distribution
    e = pd.DataFrame({'mean': mean, 'standard_deviation': sd}, index=[0])

    # Beta
    generated['betasr'] = risk_distributions.Beta.get_params(e)
    expected['betasr'] = dict()
    expected['betasr']['scale'] = [6.232114, 18.886999, 31.610845, 44.354704]
    expected['betasr']['a'] = [3.679690, 3.387153, 3.291559, 3.244209]
    expected['betasr']['b'] = [4.8479, 5.113158, 5.197285, 5.238462]

    # Exponential
    generated['exp'] = risk_distributions.Exponential.get_params(e)

    expected['exp'] = dict()
    expected['exp']['scale'] = [10, 20, 30, 40]

    # Gamma
    generated['gamma'] = risk_distributions.Gamma.get_params(e)

    expected['gamma'] = dict()
    expected['gamma']['a'] =[100, 44.444444, 36, 32.653061]
    expected['gamma']['scale'] = [0.1, 0.45, 0.833333, 1.225]

    # Gumbel
    generated['gumbel'] = risk_distributions.Gumbel.get_params(e)

    expected['gumbel'] = dict()
    expected['gumbel']['loc'] =[9.549947, 18.649840, 27.749734, 36.849628]
    expected['gumbel']['scale'] = [0.779697, 2.339090, 3.898484, 5.457878]

    # InverseGamma
    generated['invgamma'] = risk_distributions.InverseGamma.get_params(e)

    expected['invgamma'] = dict()
    expected['invgamma']['a'] = [102.000001, 46.444443, 38.000001, 34.653062]
    expected['invgamma']['scale'] = [1010.000013, 908.888853, 1110.000032, 1346.122489]

    # LogLogistic
    generated['llogis'] = risk_distributions.LogLogistic.get_params(e)

    expected['llogis'] = dict()
    expected['llogis']['c'] = [18.246506, 12.254228, 11.062771, 10.553378]
    expected['llogis']['d'] = [1, 1, 1, 1]
    expected['llogis']['scale'] = [9.950669, 19.781677, 29.598399, 39.411819]

    # LogNormal
    generated['lnorm'] = risk_distributions.LogNormal.get_params(e)

    expected['lnorm'] = dict()
    expected['lnorm']['s'] = [0.099751, 0.149166, 0.165526, 0.173682]
    expected['lnorm']['scale'] = [9.950372, 19.778727, 29.591818, 39.401219]

    # MirroredGumbel
    generated['mgumbel'] = risk_distributions.MirroredGumbel.get_params(e)

    expected['mgumbel'] = dict()
    expected['mgumbel']['loc'] = [3.092878, 10.010861, 17.103436, 24.240816]
    expected['mgumbel']['scale'] = [0.779697,2.339090,3.898484, 5.457878]

    # MirroredGamma
    generated['mgamma'] = risk_distributions.MirroredGamma.get_params(e)

    expected['mgamma'] = dict()
    expected['mgamma']['a'] = [12.552364, 14.341421, 14.982632, 15.311779]
    expected['mgamma']['scale'] = [0.282252, 0.792182, 1.291743, 1.788896]

    # Normal
    generated['norm'] = risk_distributions.Normal.get_params(e)

    expected['norm'] = dict()
    expected['norm']['loc'] = [10, 20, 30, 40]
    expected['norm']['scale'] = [1, 3, 5, 7]

    # Weibull
    generated['weibull'] = risk_distributions.Weibull.get_params(e)

    expected['weibull'] = dict()
    expected['weibull']['c'] = [12.153402, 7.906937, 7.061309, 6.699559]
    expected['weibull']['scale'] = [10.430378, 21.249309, 32.056036, 42.859356]

    for dist in expected.keys():
        for params in expected[dist].keys():
            assert np.isclose(expected[dist][params][i], generated[dist][params])
