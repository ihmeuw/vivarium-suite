from typing import Dict, Callable, Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats, optimize, special


class NonConvergenceError(Exception):
    """ Raised when the optimization fails to converge """
    def __init__(self, message: str, dist: str) -> None:
        super().__init__(message)
        self.dist = dist


class MissingDataError(Exception):
    pass


def _get_optimization_result(mean: pd.Series, sd: pd.Series, func: Callable,
                             initial_func: Callable) -> Tuple:
    """Finds the shape parameters of distributions which generates mean/sd close to actual mean/sd.

    Parameters
    ---------
    mean :
        Series where each row has a mean for a single distribution, matches with sd.
    sd :
        Series where each row has a standard deviation for a single distribution, matches with mean.
    func:
        The optimization objective function.  Takes arguments `initial guess`, `mean`, and `standard_deviation`.
    initial_func:
        Function to produce initial guess from a `mean` and `standard_deviation`.

    Returns
    --------
        A tuple of the optimization results.
    """
    mean, sd = mean.values, sd.values
    results = []
    with np.errstate(all='warn'):
        for i in range(len(mean)):
            initial_guess = initial_func(mean[i], sd[i])
            result = optimize.minimize(func, initial_guess, (mean[i], sd[i],), method='Nelder-Mead',
                                       options={'maxiter': 10000})
            results.append(result)
    return tuple(results)


def validate_parameters(params, mean, sd):
    if (params is not None and (mean is not None or sd is not None)):
        raise ValueError("You may supply either pre-calculated parameters or"
                         " mean and standard deviation but not both.")
    if params is not None and (not isinstance(params, pd.DataFrame) or params.empty):
        raise TypeError("If you specify pre-constructed parameters, they must be in the form of a non-empty dataframe.")
    if mean is not None and sd is None or mean is None and sd is not None:
        raise ValueError("You must specify both mean and standard deviation.")
    if mean is not None and len(mean) != len(sd):
        raise ValueError(f"You must specify mean and standard deviation for the same number of distributions. You "
                         f"specified {len(mean)} mean values and {len(sd)} standard deviation values.")


class BaseDistribution:
    """Generic vectorized wrapper around scipy distributions."""

    distribution = None

    def __init__(self, params: pd.DataFrame=None, mean: Union[pd.Series, float, int]=None,
                 sd: Union[pd.Series, float, int]=None):

        validate_parameters(params, mean, sd)

        if mean is not None:
            self._parameter_data = self.get_params(mean, sd)
        else:
            self._parameter_data = params

    @staticmethod
    def _get_min_max(mean: pd.Series, sd: pd.Series) -> pd.DataFrame:
        """Gets the upper and lower bounds of the distribution support."""
        alpha = 1 + sd ** 2 / mean ** 2
        scale = mean / np.sqrt(alpha)
        s = np.sqrt(np.log(alpha))
        x_min = stats.lognorm(s=s, scale=scale).ppf(.001)
        x_max = stats.lognorm(s=s, scale=scale).ppf(.999)
        return pd.DataFrame({'x_min': x_min, 'x_max': x_max}, index=mean.index)

    @classmethod
    def get_params(cls, mean: Union[pd.Series, float, int], sd: Union[pd.Series, float, int]) -> pd.DataFrame:
        """ Precalculates the parameters needed for distributions, which can then be passed to
        distributions.

        Parameters
        ---------
        data :
            Table where each row has a `mean` and `standard_deviation` for a single distribution.

        Returns
        --------
            A dictionary containing the parameters, as well as the min and max range data.
        """
        return cls._get_params(pd.Series(mean), pd.Series(sd))

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series) -> pd.DataFrame:
        raise NotImplementedError()

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: pd.DataFrame) -> Union[np.ndarray, pd.Series]:
        """Function called before and after distribution looks to handle pre- and post-processing.

        This function should look like an if sieve on the `process_type` and fall back with a call to
        this method if no processing needs to be done.

        Parameters
        ----------
        data :
            The data to be processed.
        process_type :
            One of `pdf_preprocess`, `pdf_postprocess`, `ppf_preprocess`, `ppf_post_process`.
        ranges :
            Upper and lower bounds of the distribution support.

        Returns
        -------
            The processed data.
        """
        return data

    def pdf(self, x: pd.Series) -> Union[np.ndarray, pd.Series]:
        ranges = self._parameter_data[['x_min', 'x_max']]
        dist_params = self._parameter_data[self._parameter_data.columns.difference(['x_min', 'x_max'])].to_dict('series')
        x = self.process(x, "pdf_preprocess", ranges)
        pdf = self.distribution(**dist_params).pdf(x)
        return self.process(pdf, "pdf_postprocess", ranges)

    def ppf(self, x: pd.Series) -> Union[np.ndarray, pd.Series]:
        ranges = self._parameter_data[['x_min', 'x_max']]
        dist_params = (self._parameter_data[self._parameter_data.columns.difference(['x_min', 'x_max'])]
                       .reset_index(drop=True)
                       .to_dict('series'))
        x = self.process(x, "ppf_preprocess", ranges)
        ppf = self.distribution(**dist_params).ppf(x)
        return self.process(ppf, "ppf_postprocess", ranges)


class Beta(BaseDistribution):

    distribution = stats.beta

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        scale = params['x_max'] - params['x_min']
        a = 1 / scale * (mean - params['x_min'])
        b = (1 / scale * sd) ** 2
        params['a'] = a ** 2 / b * (1 - a) - a
        params['b'] = a / b * (1 - a) ** 2 + (a - 1)
        params['scale'] = scale
        return params

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: pd.DataFrame) -> np.array:
        if process_type == 'pdf_preprocess':
            value = data - ranges['x_min']
        elif process_type == 'ppf_postprocess':
            value = data + ranges['x_max'] - ranges['x_min']
        else:
            value = super().process(data, process_type, ranges)
        return np.array(value)


class Exponential(BaseDistribution):

    distribution = stats.expon

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['scale'] = mean
        return params


class Gamma(BaseDistribution):

    distribution = stats.gamma

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['a'] = (mean / sd) ** 2
        params['scale'] = sd ** 2 / mean
        return params


class Gumbel(BaseDistribution):

    distribution = stats.gumbel_r

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['loc'] = mean - (np.euler_gamma * np.sqrt(6) / np.pi * sd)
        params['scale'] = np.sqrt(6) / np.pi * sd
        return params


class InverseGamma(BaseDistribution):

    distribution = stats.invgamma

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        def f(guess, mean, sd):
            alpha, beta = np.abs(guess)
            mean_guess = beta / (alpha - 1)
            var_guess = beta ** 2 / ((alpha - 1) ** 2 * (alpha - 2))
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(mean, sd, f, lambda m, s: np.array((m, m * s)))
        data_size = len(mean)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('InverseGamma did not converge!!', 'invgamma')

        params['a'] = np.abs([opt_results[k].x[0] for k in range(data_size)])
        params['scale'] = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return params


class InverseWeibull(BaseDistribution):

    distribution = stats.invweibull

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        # moments from  Stat Papers (2011) 52: 591. https://doi.org/10.1007/s00362-009-0271-3
        # it is much faster than using stats.invweibull.mean/var
        params = cls._get_min_max(mean, sd)
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 - 1 / shape)
            var_guess = scale ** 2 * special.gamma(1 - 2 / shape) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(mean, sd, f, lambda m, s: np.array((max(2.2, s / m), m)))
        data_size = len(mean)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('InverseWeibull did not converge!!', 'invweibull')

        params['c'] = np.abs([opt_results[k].x[0] for k in range(data_size)])
        params['scale'] = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return params


class LogLogistic(BaseDistribution):

    distribution = stats.burr12

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            b = np.pi / shape
            mean_guess = scale * b / np.sin(b)
            var_guess = scale ** 2 * 2 * b / np.sin(2 * b) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(mean, sd, f, lambda m, s: np.array((max(2, m), m)))
        data_size = len(mean)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('LogLogistic did not converge!!', 'llogis')

        params['c'] = np.abs([opt_results[k].x[0] for k in range(data_size)])
        params['d'] = [1] * data_size
        params['scale'] = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return params


class LogNormal(BaseDistribution):

    distribution = stats.lognorm

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        alpha = 1 + sd ** 2 / mean ** 2
        params['s'] = np.sqrt(np.log(alpha))
        params['scale'] = mean / np.sqrt(alpha)
        return params


class MirroredGumbel(BaseDistribution):

    distribution = stats.gumbel_r

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['loc'] = params['x_max'] - mean - (
                    np.euler_gamma * np.sqrt(6) / np.pi * sd)
        params['scale'] = np.sqrt(6) / np.pi * sd
        return params

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: pd.DataFrame) -> np.ndarray:
        if process_type == 'pdf_preprocess':
            value = ranges['x_max'] - data
        elif process_type == 'ppf_preprocess':
            value = 1 - data
        elif process_type == 'ppf_postprocess':
            value = ranges['x_max'] - data
        else:
            value = super().process(data, process_type, ranges)
        return np.array(value)


class MirroredGamma(BaseDistribution):

    distribution = stats.gamma

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['a'] = ((params['x_max'] - mean) / sd) ** 2
        params['scale'] = sd ** 2 / (params['x_max'] - mean)
        return params

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: pd.DataFrame) -> np.ndarray:
        if process_type == 'pdf_preprocess':
            value = ranges['x_max'] - data
        elif process_type == 'ppf_preprocess':
            value = 1 - data
        elif process_type == 'ppf_postprocess':
            value = ranges['x_max'] - data
        else:
            value = super().process(data, process_type, ranges)
        return np.array(value)


class Normal(BaseDistribution):

    distribution = stats.norm

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        params['loc'] = mean
        params['scale'] = sd
        return params


class Weibull(BaseDistribution):

    distribution = stats.weibull_min

    @classmethod
    def _get_params(cls, mean: pd.Series, sd: pd.Series()) -> pd.DataFrame:
        params = cls._get_min_max(mean, sd)
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 + 1 / shape)
            var_guess = scale ** 2 * special.gamma(1 + 2 / shape) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(mean, sd, f, lambda m, s: np.array((m, m / s)))
        data_size = len(mean)

        if not np.all([opt_results[k].success is True for k in range(data_size)]):
            raise NonConvergenceError('Weibull did not converge!!', 'weibull')

        params['c'] = np.abs([opt_results[k].x[0] for k in range(data_size)])
        params['scale'] = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return params


class EnsembleDistribution:
    """Represents an arbitrary distribution as a weighted sum of several concrete distribution types."""

    def __init__(self, weights, mean: Union[pd.Series, float, int], sd: Union[pd.Series, float, int]):
        self.weights, self._distributions = self.get_valid_distributions(weights, mean, sd)

    @classmethod
    def get_distribution_map(cls):
        return {'betasr': Beta,
                'exp': Exponential,
                'gamma': Gamma,
                'gumbel': Gumbel,
                'invgamma': InverseGamma,
                'invweibull': InverseWeibull,
                'llogis': LogLogistic,
                'lnorm': LogNormal,
                'mgamma': MirroredGamma,
                'mgumbel': MirroredGumbel,
                'norm': Normal,
                'weibull': Weibull}

    @classmethod
    def get_valid_distributions(cls, weights: pd.DataFrame, mean: Union[pd.Series, float, int],
                                sd: Union[pd.Series, float, int]) -> Tuple[np.ndarray, Dict]:
        """Produces a distribution that filters out non convergence errors and rescales weights appropriately.
        Can specify either data, params, or (mean and standard deviation), but not multiple.

        Parameters
        ----------
        weights :
            A single-row dataframe of normalized distribution weights, each column representing
            a different distribution type.
        mean :
            Mean value for a single distribution or series of values, each for a single distribution.
        sd :
            Standard deviation value for a single distribution or series of values, each for a single distribution.

        Returns
        -------
            Rescaled weights and the subset of the distribution map corresponding to convergent distributions.
        """

        distributions = dict()
        distribution_map = cls.get_distribution_map()
        distribution_types = list(set(distribution_map.keys()) & set(weights.columns))
        weights = weights[distribution_types].iloc[0]
        weights = weights/np.sum(weights)
        for name in distribution_types:
            try:
                distributions[name] = distribution_map[name](mean=mean, sd=sd)
            except NonConvergenceError as e:
                if weights[e.dist] > 0.05:
                    weights = weights.drop(e.dist)
                else:
                    raise NonConvergenceError(f'Divergent {key} distribution has weights: {100*weights[name]}%', name)

        return weights/np.sum(weights), distributions

    def pdf(self, x: pd.Series) -> Union[np.ndarray, pd.Series]:
        return np.sum([self.weights[name] * dist.pdf(x)
                       for name, dist in self._distributions.items()], axis=0)

    def ppf(self, x: pd.Series) -> Union[np.ndarray, pd.Series]:
        if not x.empty:
            datas = []
            for name, dist in self._distributions.items():
                datas.append(self.weights[name] * dist.ppf(x))
            return np.sum(datas, axis=0)
        else:
            return np.array([])
