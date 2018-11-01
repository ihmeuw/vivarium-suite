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


def _get_optimization_result(data: pd.DataFrame, func: Callable,
                             initial_func: Callable) -> Tuple:
    """Finds the shape parameters of distributions which generates mean/sd close to actual mean/sd.

    Parameters
    ---------
    data :
        Table where each row has a `mean` and `standard_deviation` for a single distribution.
    func:
        The optimization objective function.  Takes arguments `initial guess`, `mean`, and `standard_deviation`.
    initial_func:
        Function to produce initial guess from a `mean` and `standard_deviation`.

    Returns
    --------
        A tuple of the optimization results.
    """

    mean, sd = data['mean'].values, data['standard_deviation'].values
    results = []
    with np.errstate(all='warn'):
        for i in range(len(mean)):
            initial_guess = initial_func(mean[i], sd[i])
            result = optimize.minimize(func, initial_guess, (mean[i], sd[i],), method='Nelder-Mead',
                                       options={'maxiter': 10000})
            results.append(result)
    return tuple(results)


def validate_parameters(data, params, mean, std_dev):
    if ((data is not None or (params and (mean or std_dev)))
            and (params or (data is not None and (mean or std_dev)))):
        raise ValueError("You may supply either a dataframe or pre-calculated parameters or"
                         " mean and standard deviation but not multiple.")
    if params and not isinstance(params, Dict):
        raise TypeError("If you specify pre-constructed parameters, they must be in the form of a dictionary.")
    if data is not None and (not isinstance(data, pd.DataFrame) or data.empty):
        raise TypeError("If you specify data, it must be in the form of a non-empty dataframe.")
    if mean and not std_dev or not mean and std_dev:
        raise ValueError("You must specify both mean and standard deviation.")


class BaseDistribution:
    """Generic vectorized wrapper around scipy distributions."""

    distribution = None

    def __init__(self, data: pd.DataFrame=None, params: Dict[str, Union[np.ndarray, pd.Series]]=None,
                 mean: Union[pd.Series, float, int]=None, std_dev: Union[pd.Series, float, int]=None):

        validate_parameters(data, params, mean, std_dev)

        if (mean and std_dev) or data is not None:
            if mean:
                data = pd.DataFrame({'mean': mean, 'standard_deviation': std_dev})
            self._range = self._get_min_max(data)
            self._parameter_data = self._get_params(data)
        else:
            self._range = {k: v for k, v in params.items() if k in ('x_min', 'x_max')}
            self._parameter_data = {k: v for k, v in params.items() if k not in ('x_min', 'x_max')}


    @staticmethod
    def _get_min_max(data: pd.DataFrame) -> Dict[str, pd.Series]:
        """Gets the upper and lower bounds of the distribution support."""
        data_mean, data_sd = data['mean'], data['standard_deviation']
        alpha = 1 + data_sd ** 2 / data_mean ** 2
        scale = data_mean / np.sqrt(alpha)
        s = np.sqrt(np.log(alpha))
        x_min = stats.lognorm(s=s, scale=scale).ppf(.001)
        x_max = stats.lognorm(s=s, scale=scale).ppf(.999)
        return {'x_min': pd.Series(x_min, index=data.index),
                'x_max': pd.Series(x_max, index=data.index)}

    @classmethod
    def get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
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
        return cls._get_params(data)

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        raise NotImplementedError()

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: Dict[str, np.ndarray]) -> Union[np.ndarray, pd.Series]:
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
        x = self.process(x, "pdf_preprocess", self._range)
        pdf = self.distribution(**self._parameter_data).pdf(x)
        return self.process(pdf, "pdf_postprocess", self._range)

    def ppf(self, x: pd.Series) -> Union[np.ndarray, pd.Series]:
        x = self.process(x, "ppf_preprocess", self._range)
        ppf = self.distribution(**self._parameter_data).ppf(x)
        return self.process(ppf, "ppf_postprocess", self._range)


class Beta(BaseDistribution):

    distribution = stats.beta

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        x_range = cls._get_min_max(data)
        data_mean, data_sd = data['mean'], data['standard_deviation']
        scale = x_range['x_max'] - x_range['x_min']
        a = 1 / scale * (data_mean - x_range['x_min'])
        b = (1 / scale * data_sd) ** 2
        shape_1 = a ** 2 / b * (1 - a) - a
        shape_2 = a / b * (1 - a) ** 2 + (a - 1)
        params = {'scale': pd.Series(scale, index=data.index),
                  'a': pd.Series(shape_1, index=data.index),
                  'b': pd.Series(shape_2, index=data.index)}
        return {**params, **x_range}

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: Dict[str, np.ndarray]) -> np.array:
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
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        return {'scale': pd.Series(data['mean'], index=data.index), **cls._get_min_max(data)}


class Gamma(BaseDistribution):

    distribution = stats.gamma

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        mean, sd = data['mean'], data['standard_deviation']
        a = (mean / sd) ** 2
        scale = sd ** 2 / mean
        return {'a': pd.Series(a, index=data.index),
                'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class Gumbel(BaseDistribution):

    distribution = stats.gumbel_r

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        mean, sd = data['mean'], data['standard_deviation']
        loc = mean - (np.euler_gamma * np.sqrt(6) / np.pi * sd)
        scale = np.sqrt(6) / np.pi * sd
        return {'loc': pd.Series(loc, index=data.index),
                'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class InverseGamma(BaseDistribution):

    distribution = stats.invgamma

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        def f(guess, mean, sd):
            alpha, beta = np.abs(guess)
            mean_guess = beta / (alpha - 1)
            var_guess = beta ** 2 / ((alpha - 1) ** 2 * (alpha - 2))
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(data, f, lambda m, s: np.array((m, m * s)))
        data_size = len(data)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('InverseGamma did not converge!!', 'invgamma')

        shape = np.abs([opt_results[k].x[0] for k in range(data_size)])
        scale = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return {'a': pd.Series(shape, index=data.index), 'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class InverseWeibull(BaseDistribution):

    distribution = stats.invweibull

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        # moments from  Stat Papers (2011) 52: 591. https://doi.org/10.1007/s00362-009-0271-3
        # it is much faster than using stats.invweibull.mean/var
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 - 1 / shape)
            var_guess = scale ** 2 * special.gamma(1 - 2 / shape) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(data, f, lambda m, s: np.array((max(2.2, s / m), m)))
        data_size = len(data)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('InverseWeibull did not converge!!', 'invweibull')

        shape = np.abs([opt_results[k].x[0] for k in range(data_size)])
        scale = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return {'c': pd.Series(shape, index=data.index), 'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class LogLogistic(BaseDistribution):

    distribution = stats.burr12

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            b = np.pi / shape
            mean_guess = scale * b / np.sin(b)
            var_guess = scale ** 2 * 2 * b / np.sin(2 * b) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(data, f, lambda m, s: np.array((max(2, m), m)))
        data_size = len(data)

        if not np.all([opt_results[k].success for k in range(data_size)]):
            raise NonConvergenceError('LogLogistic did not converge!!', 'llogis')

        shape = np.abs([opt_results[k].x[0] for k in range(data_size)])
        scale = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return {'c': pd.Series(shape, index=data.index),
                'd': pd.Series([1]*len(data), index=data.index),
                'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class LogNormal(BaseDistribution):

    distribution = stats.lognorm

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        mean, sd = data['mean'], data['standard_deviation']
        alpha = 1 + sd ** 2 / mean ** 2
        s = np.sqrt(np.log(alpha))
        scale = mean / np.sqrt(alpha)
        return {'s': pd.Series(s, index=data.index),
                'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class MirroredGumbel(BaseDistribution):

    distribution = stats.gumbel_r

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        x_range = cls._get_min_max(data)
        loc = x_range['x_max'] - data['mean'] - (
                    np.euler_gamma * np.sqrt(6) / np.pi * data['standard_deviation'])
        scale = np.sqrt(6) / np.pi * data['standard_deviation']
        return {'loc': pd.Series(loc, index=data.index),
                'scale': pd.Series(scale, index=data.index),
                **x_range}

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: Dict[str, np.ndarray]) -> np.ndarray:
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
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        x_range = cls._get_min_max(data)
        mean, sd = data['mean'], data['standard_deviation']
        a = ((x_range['x_max'] - mean) / sd) ** 2
        scale = sd ** 2 / (x_range['x_max'] - mean)
        return {'a': pd.Series(a, index=data.index), 'scale': pd.Series(scale, index=data.index),
                **x_range}

    def process(self, data: Union[np.ndarray, pd.Series], process_type: str,
                ranges: Dict[str, np.ndarray]) -> np.ndarray:
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
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        return {'loc': pd.Series(data['mean'], index=data.index),
                'scale': pd.Series(data['standard_deviation'], index=data.index),
                **cls._get_min_max(data)}


class Weibull(BaseDistribution):

    distribution = stats.weibull_min

    @classmethod
    def _get_params(cls, data: pd.DataFrame) -> Dict[str, pd.Series]:
        def f(guess, mean, sd):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 + 1 / shape)
            var_guess = scale ** 2 * special.gamma(1 + 2 / shape) - mean_guess ** 2
            return (mean - mean_guess) ** 2 + (sd ** 2 - var_guess) ** 2

        opt_results = _get_optimization_result(data, f, lambda m, s: np.array((m, m / s)))
        data_size = len(data)

        if not np.all([opt_results[k].success is True for k in range(data_size)]):
            raise NonConvergenceError('Weibull did not converge!!', 'weibull')

        shape = np.abs([opt_results[k].x[0] for k in range(data_size)])
        scale = np.abs([opt_results[k].x[1] for k in range(data_size)])
        return {'c': pd.Series(shape, index=data.index), 'scale': pd.Series(scale, index=data.index),
                **cls._get_min_max(data)}


class EnsembleDistribution:
    """Represents an arbitrary distribution as a weighted sum of several concrete distribution types."""

    def __init__(self, weights, distribution_map, data: pd.DataFrame=None, params: Dict[str, Union[np.ndarray, pd.Series]]=None,
                 mean: Union[pd.Series, float, int]=None, std_dev: Union[pd.Series, float, int]=None):
        self.weights, self._distributions = self.get_valid_distributions(weights, distribution_map, data, params, mean,
                                                                         std_dev)

    @staticmethod
    def get_valid_distributions(weights: pd.Series, distribution_map: Dict, data: pd.DataFrame=None,
                                params: Dict[str, Union[np.ndarray, pd.Series]]=None,
                                mean: Union[pd.Series, float, int]=None,
                                std_dev: Union[pd.Series, float, int]=None) -> Tuple[np.ndarray, Dict]:
        """Produces a distribution that filters out non convergence errors and rescales weights appropriately.
        Can specify either data, params, or (mean and standard deviation), but not multiple.

        Parameters
        ----------
        weights :
            A list of normalized distribution weights indexed by distribution type.
        distribution_map :
            Mapping between distribution name and distribution class.
        data :
            Table where each row has a `mean` and `standard_deviation` for a single distribution.
        params :
            Dictionary of pre-calculated parameters for distribution.
        mean :
            Mean value for a single distribution or series of values, each for a single distribution.
        std_dev :
            Standard deviation value for a single distribution or series of values, each for a single distribution.

        Returns
        -------
            Rescaled weights and the subset of the distribution map corresponding to convergent distributions.
        """
        dist = dict()
        for key in distribution_map:
            try:
                dist[key] = distribution_map[key](data=data, params=params, mean=mean, std_dev=std_dev)
            except NonConvergenceError as e:
                if weights[e.dist] > 0.05:
                    weights = weights.drop(e.dist)
                else:
                    raise NonConvergenceError(f'Divergent {key} distribution has weights: {100*weights[key]}%', key)

        return weights/np.sum(weights), dist

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
