import warnings
from typing import Callable, Dict, Tuple, Union

import numpy as np
import pandas as pd
from scipy import optimize, special, stats

from risk_distributions.formatting import (
    Parameter,
    Parameters,
    cast_to_series,
    format_call_data,
    format_data,
)


class BaseDistribution:
    """Generic vectorized wrapper around scipy distributions."""

    distribution = None
    expected_parameters = ()

    def __init__(
        self, parameters: Parameters = None, mean: Parameter = None, sd: Parameter = None
    ):
        self.parameters = self.get_parameters(parameters, mean, sd)

    @classmethod
    def get_parameters(
        cls, parameters: Parameters = None, mean: Parameter = None, sd: Parameter = None
    ) -> pd.DataFrame:
        required_parameters = list(cls.expected_parameters + ("x_min", "x_max"))
        if parameters is not None:
            if not (mean is None and sd is None):
                raise ValueError(
                    "You may supply either pre-calculated parameters or"
                    " mean and standard deviation but not both."
                )
            parameters = format_data(parameters, required_parameters, "parameters")

        else:
            if mean is None or sd is None:
                raise ValueError(
                    "You may supply either pre-calculated parameters or"
                    " mean and standard deviation but not both."
                )

            mean, sd = cast_to_series(mean, sd)

            parameters = pd.DataFrame(0, columns=required_parameters, index=mean.index)

            computable = cls.computable_parameter_index(mean, sd)
            parameters.loc[computable, ["x_min", "x_max"]] = cls.compute_min_max(
                mean.loc[computable], sd.loc[computable]
            )
            # The scipy.stats distributions run optimization routines that handle FloatingPointErrors,
            # transforming them into RuntimeWarnings. This gets noisy in our logs.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                parameters.loc[
                    computable, list(cls.expected_parameters)
                ] = cls._get_parameters(
                    mean.loc[computable],
                    sd.loc[computable],
                    parameters.loc[computable, "x_min"],
                    parameters.loc[computable, "x_max"],
                )

        return parameters

    @staticmethod
    def computable_parameter_index(mean: pd.Series, sd: pd.Series) -> pd.Index:
        return mean[(mean != 0) & ~np.isnan(mean) & (sd != 0) & ~np.isnan(sd)].index

    @staticmethod
    def compute_min_max(mean: pd.Series, sd: pd.Series) -> pd.DataFrame:
        """Gets the upper and lower bounds of the distribution support."""
        # noinspection PyTypeChecker
        alpha = 1 + sd**2 / mean**2
        scale = mean / np.sqrt(alpha)
        s = np.sqrt(np.log(alpha))
        x_min = stats.lognorm(s=s, scale=scale).ppf(0.001)
        x_max = stats.lognorm(s=s, scale=scale).ppf(0.999)
        return pd.DataFrame({"x_min": x_min, "x_max": x_max}, index=mean.index)

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        raise NotImplementedError()

    def process(
        self, data: pd.Series, parameters: pd.DataFrame, process_type: str
    ) -> pd.Series:
        """Function called before and after distribution looks to handle pre- and post-processing.

        This function should look like an if sieve on the `process_type` and fall back with a call to
        this method if no processing needs to be done.

        Parameters
        ----------
        data
            The data to be processed.
        parameters
            Parameter data to be used in the processing.
        process_type
            One of `pdf_preprocess`, `pdf_postprocess`, `ppf_preprocess`, `ppf_post_process`.

        Returns
        -------
        pandas.Series
            The processed data.
        """
        return data

    def pdf(
        self, x: Union[pd.Series, np.ndarray, float, int]
    ) -> Union[pd.Series, np.ndarray, float]:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, parameters = format_call_data(x, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(x)
            & (parameters["x_min"] <= x)
            & (x <= parameters["x_max"])
        ].index

        x.loc[computable] = self.process(
            x.loc[computable], parameters.loc[computable], "pdf_preprocess"
        )

        p = pd.Series(np.nan, x.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            p.loc[computable] = self.distribution(**params.to_dict("series")).pdf(
                x.loc[computable]
            )

            p.loc[computable] = self.process(
                p.loc[computable], parameters.loc[computable], "pdf_postprocess"
            )

        if single_val:
            p = p.iloc[0]
        if values_only:
            p = p.values
        return p

    def ppf(
        self, q: Union[pd.Series, np.ndarray, float, int]
    ) -> Union[pd.Series, np.ndarray, float]:
        single_val = isinstance(q, (float, int))
        values_only = isinstance(q, np.ndarray)

        q, parameters = format_call_data(q, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(q)
            & (0.001 <= q.values)
            & (q.values <= 0.999)
        ].index

        q.loc[computable] = self.process(
            q.loc[computable], parameters.loc[computable], "ppf_preprocess"
        )

        x = pd.Series(np.nan, q.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            x.loc[computable] = self.distribution(**params.to_dict("series")).ppf(
                q.loc[computable]
            )

            x.loc[computable] = self.process(
                x.loc[computable], parameters.loc[computable], "ppf_postprocess"
            )

        if single_val:
            x = x.iloc[0]
        if values_only:
            x = x.values
        return x

    def cdf(
        self, x: Union[pd.Series, np.ndarray, float, int]
    ) -> Union[pd.Series, np.ndarray, float]:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, parameters = format_call_data(x, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(x)
            & (parameters["x_min"] <= x)
            & (x <= parameters["x_max"])
        ].index

        x.loc[computable] = self.process(
            x.loc[computable], parameters.loc[computable], "cdf_preprocess"
        )

        c = pd.Series(np.nan, x.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            c.loc[computable] = self.distribution(**params.to_dict("series")).cdf(
                x.loc[computable]
            )

            c.loc[computable] = self.process(
                c.loc[computable], parameters.loc[computable], "cdf_postprocess"
            )

        if single_val:
            c = c.iloc[0]
        if values_only:
            c = c.values
        return c


class Beta(BaseDistribution):

    distribution = stats.beta
    expected_parameters = ("a", "b", "scale", "loc")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        scale = x_max - x_min
        a = 1 / scale * (mean - x_min)
        # noinspection PyTypeChecker
        b = (1 / scale * sd) ** 2
        params = pd.DataFrame(
            {
                "a": a**2 / b * (1 - a) - a,
                "b": a / b * (1 - a) ** 2 + (a - 1),
                "scale": scale,
                "loc": x_min,
            },
            index=mean.index,
        )
        return params


class Exponential(BaseDistribution):

    distribution = stats.expon
    expected_parameters = ("scale",)

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        return pd.DataFrame({"scale": mean}, index=mean.index)


class Gamma(BaseDistribution):

    distribution = stats.gamma
    expected_parameters = ("a", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        # noinspection PyTypeChecker
        params = pd.DataFrame(
            {
                "a": (mean / sd) ** 2,
                "scale": sd**2 / mean,
            },
            index=mean.index,
        )
        return params


class Gumbel(BaseDistribution):

    distribution = stats.gumbel_r
    expected_parameters = ("loc", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        params = pd.DataFrame(
            {
                "loc": mean - (np.euler_gamma * np.sqrt(6) / np.pi * sd),
                "scale": np.sqrt(6) / np.pi * sd,
            },
            index=mean.index,
        )
        return params


class InverseGamma(BaseDistribution):

    distribution = stats.invgamma
    expected_parameters = ("a", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        def target_function(guess, m, s):
            alpha, beta = np.abs(guess)
            mean_guess = beta / (alpha - 1)
            var_guess = beta**2 / ((alpha - 1) ** 2 * (alpha - 2))
            return (m - mean_guess) ** 2 + (s**2 - var_guess) ** 2

        opt_results = _get_optimization_result(
            mean, sd, target_function, lambda m, s: np.array((m, m * s))
        )

        result_indices = range(len(mean))
        if not np.all([opt_results[k].success for k in result_indices]):
            raise NonConvergenceError("InverseGamma did not converge!!", "invgamma")

        params = pd.DataFrame(
            {
                "a": np.abs([opt_results[k].x[0] for k in result_indices]),
                "scale": np.abs([opt_results[k].x[1] for k in result_indices]),
            },
            index=mean.index,
        )
        return params


class InverseWeibull(BaseDistribution):

    distribution = stats.invweibull
    expected_parameters = ("c", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        # moments from  Stat Papers (2011) 52: 591. https://doi.org/10.1007/s00362-009-0271-3
        # it is much faster than using stats.invweibull.mean/var
        def target_function(guess, m, s):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 - 1 / shape)
            var_guess = scale**2 * special.gamma(1 - 2 / shape) - mean_guess**2
            return (m - mean_guess) ** 2 + (s**2 - var_guess) ** 2

        opt_results = _get_optimization_result(
            mean, sd, target_function, lambda m, s: np.array((max(2.2, s / m), m))
        )

        result_indices = range(len(mean))
        if not np.all([opt_results[k].success for k in result_indices]):
            raise NonConvergenceError("InverseWeibull did not converge!!", "invweibull")

        params = pd.DataFrame(
            {
                "c": np.abs([opt_results[k].x[0] for k in result_indices]),
                "scale": np.abs([opt_results[k].x[1] for k in result_indices]),
            },
            index=mean.index,
        )
        return params


class LogLogistic(BaseDistribution):

    distribution = stats.burr12
    expected_parameters = ("c", "d", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        def target_function(guess, m, s):
            shape, scale = np.abs(guess)
            b = np.pi / shape
            mean_guess = scale * b / np.sin(b)
            var_guess = scale**2 * 2 * b / np.sin(2 * b) - mean_guess**2
            return (m - mean_guess) ** 2 + (s**2 - var_guess) ** 2

        opt_results = _get_optimization_result(
            mean, sd, target_function, lambda m, s: np.array((max(2, m), m))
        )

        result_indices = range(len(mean))
        if not np.all([opt_results[k].success for k in result_indices]):
            raise NonConvergenceError("LogLogistic did not converge!!", "llogis")

        params = pd.DataFrame(
            {
                "c": np.abs([opt_results[k].x[0] for k in result_indices]),
                "d": 1,
                "scale": np.abs([opt_results[k].x[1] for k in result_indices]),
            },
            index=mean.index,
        )
        return params


class LogNormal(BaseDistribution):

    distribution = stats.lognorm
    expected_parameters = ("s", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        # noinspection PyTypeChecker
        alpha = 1 + sd**2 / mean**2
        params = pd.DataFrame(
            {
                "s": np.sqrt(np.log(alpha)),
                "scale": mean / np.sqrt(alpha),
            },
            index=mean.index,
        )
        return params


class MirroredGumbel(BaseDistribution):

    distribution = stats.gumbel_r
    expected_parameters = ("loc", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        params = pd.DataFrame(
            {
                "loc": x_max - mean - (np.euler_gamma * np.sqrt(6) / np.pi * sd),
                "scale": np.sqrt(6) / np.pi * sd,
            },
            index=mean.index,
        )
        return params

    def process(
        self, data: pd.Series, parameters: pd.DataFrame, process_type: str
    ) -> pd.Series:
        x_min, x_max = (
            parameters.loc[data.index, "x_min"],
            parameters.loc[data.index, "x_max"],
        )
        if process_type in ["pdf_preprocess", "cdf_preprocess"]:
            value = x_max - data
        elif process_type == "ppf_preprocess":
            # noinspection PyTypeChecker
            value = 1 - data
        elif process_type == "ppf_postprocess":
            value = x_max - data
        else:
            value = super().process(data, parameters, process_type)
        return value


class MirroredGamma(BaseDistribution):

    distribution = stats.gamma
    expected_parameters = ("a", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        # noinspection PyTypeChecker
        params = pd.DataFrame(
            {"a": ((x_max - mean) / sd) ** 2, "scale": sd**2 / (x_max - mean)},
            index=mean.index,
        )
        return params

    def process(
        self, data: pd.Series, parameters: pd.DataFrame, process_type: str
    ) -> pd.Series:
        x_min, x_max = (
            parameters.loc[data.index, "x_min"],
            parameters.loc[data.index, "x_max"],
        )
        if process_type in ["pdf_preprocess", "cdf_preprocess"]:
            value = x_max - data
        elif process_type == "ppf_preprocess":
            # noinspection PyTypeChecker
            value = 1 - data
        elif process_type == "ppf_postprocess":
            value = x_max - data
        else:
            value = super().process(data, parameters, process_type)
        return value


class Normal(BaseDistribution):

    distribution = stats.norm
    expected_parameters = ("loc", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        params = pd.DataFrame(
            {
                "loc": mean,
                "scale": sd,
            },
            mean.index,
        )
        return params


class Weibull(BaseDistribution):

    distribution = stats.weibull_min
    expected_parameters = ("c", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series, sd: pd.Series, x_min: pd.Series, x_max: pd.Series
    ) -> pd.DataFrame:
        def target_function(guess, m, s):
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 + 1 / shape)
            var_guess = scale**2 * special.gamma(1 + 2 / shape) - mean_guess**2
            return (m - mean_guess) ** 2 + (s**2 - var_guess) ** 2

        opt_results = _get_optimization_result(
            mean, sd, target_function, lambda m, s: np.array((m, m / s))
        )

        result_indices = range(len(mean))
        if not np.all([opt_results[k].success is True for k in result_indices]):
            raise NonConvergenceError("Weibull did not converge!!", "weibull")

        params = pd.DataFrame(
            {
                "c": np.abs([opt_results[k].x[0] for k in result_indices]),
                "scale": np.abs([opt_results[k].x[1] for k in result_indices]),
            },
            index=mean.index,
        )
        return params


class EnsembleDistribution:
    """Represents an arbitrary distribution as a weighted sum of several concrete distribution types."""

    _distribution_map = {
        "betasr": Beta,
        "exp": Exponential,
        "gamma": Gamma,
        "gumbel": Gumbel,
        "invgamma": InverseGamma,
        "invweibull": InverseWeibull,
        "llogis": LogLogistic,
        "lnorm": LogNormal,
        "mgamma": MirroredGamma,
        "mgumbel": MirroredGumbel,
        "norm": Normal,
        "weibull": Weibull,
    }

    def __init__(
        self,
        weights: Parameters,
        parameters: Dict[str, Parameters] = None,
        mean: Parameter = None,
        sd: Parameter = None,
    ):
        self.weights, self.parameters = self.get_parameters(weights, parameters, mean, sd)

    @classmethod
    def get_parameters(
        cls,
        weights: Parameters,
        parameters: Parameters = None,
        mean: Parameter = None,
        sd: Parameter = None,
    ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        weights = format_data(weights, list(cls._distribution_map.keys()), "weights")

        params = {}
        for name, dist in cls._distribution_map.items():
            try:
                param = parameters[name] if parameters else None
                params[name] = dist.get_parameters(param, mean, sd)
            except NonConvergenceError:
                if weights[name].max() < 0.05:
                    weights.loc[name, :] = 0
                else:
                    raise NonConvergenceError(
                        f"Divergent {name} distribution has "
                        f"weights: {100 * weights[name]}%",
                        name,
                    )

        # Rescale weights in case we floored any of them:
        non_zero_rows = weights[weights.sum(axis=1) != 0]
        weights.loc[non_zero_rows.index] = non_zero_rows.divide(
            non_zero_rows.sum(axis=1), axis=0
        )

        return weights, params

    def pdf(
        self, x: Union[pd.Series, np.ndarray, float, int]
    ) -> Union[pd.Series, np.ndarray, float]:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, weights = format_call_data(x, self.weights)

        computable = weights[(weights.sum(axis=1) != 0) & ~np.isnan(x)].index

        p = pd.Series(np.nan, index=x.index)

        if not computable.empty:
            p.loc[computable] = 0
            for name, parameters in self.parameters.items():
                w = weights.loc[computable, name]
                params = parameters.loc[computable] if len(parameters) > 1 else parameters
                p += w * self._distribution_map[name](parameters=params).pdf(
                    x.loc[computable]
                )

        if single_val:
            p = p.iloc[0]
        if values_only:
            p = p.values
        return p

    def ppf(
        self,
        q: Union[pd.Series, np.ndarray, float, int],
        q_dist: Union[pd.Series, np.ndarray, float, int],
    ) -> Union[pd.Series, np.ndarray, float]:
        """Quantile function using 2 propensities.

        Parameters
        ---------
        q :
            value propensity
        q_dist :
            propensity for picking the distribution

        Returns
        --------
        Union[pandas.Series, numpy.ndarray, float]
            Sample from the ensembled distribution.
        """
        single_val = isinstance(q, (float, int))
        values_only = isinstance(q, np.ndarray)

        q, weights = format_call_data(q, self.weights)
        q_dist, _ = format_call_data(q_dist, self.weights)

        computable = weights[(weights.sum(axis=1) != 0) & ~np.isnan(q)].index
        x = pd.Series(np.nan, index=q.index)

        if not computable.empty:
            p_bins = np.cumsum(weights.loc[computable], axis=1)
            choice_index = (q_dist.loc[computable].values[np.newaxis].T > p_bins).sum(axis=1)
            x.loc[computable] = 0
            idx_lookup = {v: i for i, v in enumerate(weights.columns)}
            for name, parameters in self.parameters.items():
                idx = choice_index[choice_index == idx_lookup[name]].index
                if len(idx):
                    params = (
                        parameters.loc[computable.intersection(idx)]
                        if len(parameters) > 1
                        else parameters
                    )
                    x[idx] = self._distribution_map[name](parameters=params).ppf(q[idx])

        if single_val:
            x = x.iloc[0]
        if values_only:
            x = x.values
        return x

    def cdf(
        self, x: Union[pd.Series, np.ndarray, float, int]
    ) -> Union[pd.Series, np.ndarray, float]:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, weights = format_call_data(x, self.weights)

        computable = weights[(weights.sum(axis=1) != 0) & ~np.isnan(x)].index

        c = pd.Series(np.nan, index=x.index)

        c.loc[computable] = 0

        if not computable.empty:
            for name, parameters in self.parameters.items():
                w = weights.loc[computable, name]
                params = parameters.loc[computable] if len(parameters) > 1 else parameters
                c += w * self._distribution_map[name](parameters=params).cdf(
                    x.loc[computable]
                )

        if single_val:
            c = c.iloc[0]
        if values_only:
            c = c.values
        return c


class NonConvergenceError(Exception):
    """Raised when the optimization fails to converge"""

    def __init__(self, message: str, dist: str) -> None:
        super().__init__(message)
        self.dist = dist


def _get_optimization_result(
    mean: pd.Series, sd: pd.Series, func: Callable, initial_func: Callable
) -> Tuple:
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
    with np.errstate(all="warn"):
        for i in range(len(mean)):
            initial_guess = initial_func(mean[i], sd[i])
            result = optimize.minimize(
                func,
                initial_guess,
                (
                    mean[i],
                    sd[i],
                ),
                method="Nelder-Mead",
                options={"maxiter": 10000},
            )
            results.append(result)
    return tuple(results)
