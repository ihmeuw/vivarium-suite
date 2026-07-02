from __future__ import annotations

import copy
import warnings
from collections.abc import Callable
from typing import Any, TypeAlias, cast

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy import optimize, special, stats

from vivarium.risk_distributions.formatting import (
    Parameter,
    Parameters,
    cast_to_series,
    format_call_data,
    format_data,
)

Numeric: TypeAlias = "pd.Series[Any] | npt.NDArray[Any] | float"
NumericInput: TypeAlias = "Numeric | int"


class BaseDistribution:
    """Generic vectorized wrapper around scipy distributions."""

    # scipy distributions (e.g. ``stats.beta``) are unstubbed; subclasses assign one.
    distribution: Any = None
    expected_parameters: tuple[str, ...] = ()
    _extra_parameters: tuple[str, ...] = ()

    def __init__(
        self,
        parameters: Parameters | None = None,
        mean: Parameter | None = None,
        sd: Parameter | None = None,
        computability_bound: float = 0.001,
    ) -> None:
        self.parameters = self.get_parameters(parameters, mean, sd, computability_bound)
        self.computability_bound = computability_bound

    @classmethod
    def get_parameters(
        cls,
        parameters: Parameters | None = None,
        mean: Parameter | None = None,
        sd: Parameter | None = None,
        computability_bound: float = 0.001,
    ) -> pd.DataFrame:
        required_parameters = list(
            cls.expected_parameters
            + cls._extra_parameters
            + ("computability_min", "computability_max")
        )
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

            parameters = pd.DataFrame(0.0, columns=required_parameters, index=mean.index)

            computable = cls.computable_parameter_index(mean, sd)

            # The scipy.stats distributions run optimization routines that handle FloatingPointErrors,
            # transforming them into RuntimeWarnings. This gets noisy in our logs.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                parameters.loc[
                    computable, list(cls.expected_parameters + cls._extra_parameters)
                ] = cls._get_parameters(
                    mean.loc[computable], sd.loc[computable], computability_bound
                )
            parameters.loc[
                computable, ["computability_min", "computability_max"]
            ] = cls.get_computability_bounds(parameters.loc[computable], computability_bound)

        return parameters

    @staticmethod
    def computable_parameter_index(mean: pd.Series[Any], sd: pd.Series[Any]) -> pd.Index[Any]:
        index: pd.Index[Any] = mean[
            (mean != 0) & ~np.isnan(mean) & (sd != 0) & ~np.isnan(sd)
        ].index
        return index

    @classmethod
    def get_computability_bounds(
        cls, parameters: pd.DataFrame, computability_bound: float
    ) -> pd.DataFrame:
        kwargs = {parameter: parameters[parameter] for parameter in cls.expected_parameters}
        compatability_min = cls.distribution(**kwargs).ppf(computability_bound)
        compatability_max = cls.distribution(**kwargs).ppf(1 - computability_bound)
        return pd.DataFrame(
            {"computability_min": compatability_min, "computability_max": compatability_max},
            index=parameters.index,
        )

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        raise NotImplementedError()

    def process(
        self, data: pd.Series[Any], parameters: pd.DataFrame, process_type: str
    ) -> pd.Series[Any]:
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

    def pdf(self, x: NumericInput) -> Numeric:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, parameters = format_call_data(x, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(x)
            & (parameters["computability_min"] <= x)
            & (x <= parameters["computability_max"])
        ].index

        x.loc[computable] = self.process(
            x.loc[computable], parameters.loc[computable], "pdf_preprocess"
        )

        p = pd.Series(np.nan, x.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            kwargs = {parameter: params[parameter] for parameter in self.expected_parameters}
            p.loc[computable] = self.distribution(**kwargs).pdf(x.loc[computable])

            p.loc[computable] = self.process(
                p.loc[computable], parameters.loc[computable], "pdf_postprocess"
            )

        result: Numeric = p
        if single_val:
            result = p.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", p.values)
        return result

    def ppf(self, q: NumericInput) -> Numeric:
        single_val = isinstance(q, (float, int))
        values_only = isinstance(q, np.ndarray)

        q, parameters = format_call_data(q, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(q)
            & (self.computability_bound <= q.to_numpy())
            & (q.to_numpy() <= 1 - self.computability_bound)
        ].index

        q.loc[computable] = self.process(
            q.loc[computable], parameters.loc[computable], "ppf_preprocess"
        )

        x = pd.Series(np.nan, q.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            kwargs = {parameter: params[parameter] for parameter in self.expected_parameters}
            x.loc[computable] = self.distribution(**kwargs).ppf(q.loc[computable])

            x.loc[computable] = self.process(
                x.loc[computable], parameters.loc[computable], "ppf_postprocess"
            )

        result: Numeric = x
        if single_val:
            result = x.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", x.values)
        return result

    def cdf(self, x: NumericInput) -> Numeric:
        single_val = isinstance(x, (float, int))
        values_only = isinstance(x, np.ndarray)

        x, parameters = format_call_data(x, self.parameters)

        computable = parameters[
            (parameters.sum(axis=1) != 0)
            & ~np.isnan(x)
            & (parameters["computability_min"] <= x)
            & (x <= parameters["computability_max"])
        ].index

        x.loc[computable] = self.process(
            x.loc[computable], parameters.loc[computable], "cdf_preprocess"
        )

        c = pd.Series(np.nan, x.index)

        if not computable.empty:
            params = parameters.loc[computable, list(self.expected_parameters)]
            kwargs = {parameter: params[parameter] for parameter in self.expected_parameters}
            c.loc[computable] = self.distribution(**kwargs).cdf(x.loc[computable])

            c.loc[computable] = self.process(
                c.loc[computable], parameters.loc[computable], "cdf_postprocess"
            )

        result: Numeric = c
        if single_val:
            result = c.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", c.values)
        return result


class MirroredDistribution(BaseDistribution):
    _extra_parameters = ("mirror_point",)

    def __init__(
        self,
        parameters: Parameters | None = None,
        mean: Parameter | None = None,
        sd: Parameter | None = None,
        computability_bound: float = 0.001,
    ) -> None:
        super().__init__(parameters, mean, sd, computability_bound)
        # When called from EnsembleDistribution.ppf, only parameters are provided.
        if mean is not None and sd is not None:
            mean, sd = cast_to_series(mean, sd)
            self.mirror_point = self.compute_mirror_point(mean, sd, computability_bound)
        else:
            self.mirror_point = self.parameters["mirror_point"]

    @staticmethod
    def compute_mirror_point(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.Series[Any]:
        """Computes the point around which the distribution is mirrored.

        NOTE: In the corresponding GBD code, this is called 'x_max'.
        """
        alpha = 1 + sd**2 / mean**2
        scale = mean / np.sqrt(alpha)
        s = np.sqrt(np.log(alpha))
        mirror_point: pd.Series[Any] = pd.Series(
            stats.lognorm(s=s, scale=scale).ppf(1 - computability_bound),
            index=mean.index,
        )
        return mirror_point

    @classmethod
    def get_computability_bounds(
        cls, parameters: pd.DataFrame, computability_bound: float
    ) -> pd.DataFrame:
        """Reflect the underlying distribution's support bounds into data space.

        A data-space value ``x`` is evaluated via its reflection ``mirror_point - x``,
        so the underlying quantile bounds (which live in the reflected space) must be
        mapped back with ``mirror_point - bound`` before the base ``cdf``/``pdf``
        computability mask can compare them against ``x``. The reflection also swaps the
        min and max.
        """
        transformed = super().get_computability_bounds(parameters, computability_bound)
        mirror_point = parameters["mirror_point"]
        return pd.DataFrame(
            {
                "computability_min": mirror_point - transformed["computability_max"],
                "computability_max": mirror_point - transformed["computability_min"],
            },
            index=parameters.index,
        )


class Beta(BaseDistribution):
    distribution = stats.beta
    expected_parameters = ("a", "b", "scale", "loc")

    @staticmethod
    def compute_scaling_bounds(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        """Gets the upper and lower bounds of the distribution support."""
        # noinspection PyTypeChecker
        alpha = 1 + sd**2 / mean**2
        scale = mean / np.sqrt(alpha)
        s = np.sqrt(np.log(alpha))
        x_min = stats.lognorm(s=s, scale=scale).ppf(computability_bound)
        x_max = stats.lognorm(s=s, scale=scale).ppf(1 - computability_bound)
        return pd.DataFrame({"x_min": x_min, "x_max": x_max}, index=mean.index)

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        x_bounds = Beta.compute_scaling_bounds(mean, sd, computability_bound)
        x_min = x_bounds["x_min"]
        x_max = x_bounds["x_max"]
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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        return pd.DataFrame({"scale": mean}, index=mean.index)


class Gamma(BaseDistribution):
    distribution = stats.gamma
    expected_parameters = ("a", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        def target_function(guess: npt.NDArray[Any], m: float, s: float) -> float:
            alpha, beta = np.abs(guess)
            mean_guess = beta / (alpha - 1)
            var_guess = beta**2 / ((alpha - 1) ** 2 * (alpha - 2))
            return float((m - mean_guess) ** 2 + (s**2 - var_guess) ** 2)

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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        # moments from  Stat Papers (2011) 52: 591. https://doi.org/10.1007/s00362-009-0271-3
        # it is much faster than using stats.invweibull.mean/var
        def target_function(guess: npt.NDArray[Any], m: float, s: float) -> float:
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 - 1 / shape)
            var_guess = scale**2 * special.gamma(1 - 2 / shape) - mean_guess**2
            return float((m - mean_guess) ** 2 + (s**2 - var_guess) ** 2)

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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        def target_function(guess: npt.NDArray[Any], m: float, s: float) -> float:
            shape, scale = np.abs(guess)
            b = np.pi / shape
            mean_guess = scale * b / np.sin(b)
            var_guess = scale**2 * 2 * b / np.sin(2 * b) - mean_guess**2
            return float((m - mean_guess) ** 2 + (s**2 - var_guess) ** 2)

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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
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


class MirroredGumbel(MirroredDistribution):
    distribution = stats.gumbel_r
    expected_parameters = ("loc", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        # Persist mirror_point in params so it's available when re-instantiated with only parameters.
        mirror_point = MirroredGumbel.compute_mirror_point(mean, sd, computability_bound)
        params = pd.DataFrame(
            {
                "loc": mirror_point - mean - (np.euler_gamma * np.sqrt(6) / np.pi * sd),
                "scale": np.sqrt(6) / np.pi * sd,
                "mirror_point": mirror_point,
            },
            index=mean.index,
        )
        return params

    def process(
        self, data: pd.Series[Any], parameters: pd.DataFrame, process_type: str
    ) -> pd.Series[Any]:
        if process_type in ["pdf_preprocess", "cdf_preprocess"]:
            value = self.mirror_point - data
        elif process_type in ["ppf_preprocess", "cdf_postprocess"]:
            # noinspection PyTypeChecker
            value = 1 - data
        elif process_type == "ppf_postprocess":
            value = self.mirror_point - data
        else:
            value = super().process(data, parameters, process_type)
        return value


class MirroredGamma(MirroredDistribution):
    distribution = stats.gamma
    expected_parameters = ("a", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        # noinspection PyTypeChecker
        mirror_point = MirroredGamma.compute_mirror_point(mean, sd, computability_bound)
        params = pd.DataFrame(
            {
                "a": ((mirror_point - mean) / sd) ** 2,
                "scale": sd**2 / (mirror_point - mean),
                "mirror_point": mirror_point,
            },
            index=mean.index,
        )
        return params

    def process(
        self, data: pd.Series[Any], parameters: pd.DataFrame, process_type: str
    ) -> pd.Series[Any]:
        if process_type in ["pdf_preprocess", "cdf_preprocess"]:
            value = self.mirror_point - data
        elif process_type in ["ppf_preprocess", "cdf_postprocess"]:
            # noinspection PyTypeChecker
            value = 1 - data
        elif process_type == "ppf_postprocess":
            value = self.mirror_point - data
        else:
            value = super().process(data, parameters, process_type)
        return value


class Normal(BaseDistribution):
    distribution = stats.norm
    expected_parameters = ("loc", "scale")

    @staticmethod
    def _get_parameters(
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
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
        mean: pd.Series[Any], sd: pd.Series[Any], computability_bound: float
    ) -> pd.DataFrame:
        def target_function(guess: npt.NDArray[Any], m: float, s: float) -> float:
            shape, scale = np.abs(guess)
            mean_guess = scale * special.gamma(1 + 1 / shape)
            var_guess = scale**2 * special.gamma(1 + 2 / shape) - mean_guess**2
            return float((m - mean_guess) ** 2 + (s**2 - var_guess) ** 2)

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
        parameters: dict[str, Parameters] | None = None,
        mean: Parameter | None = None,
        sd: Parameter | None = None,
        computability_bound: float = 0.001,
    ) -> None:
        self.weights, self.parameters = self.get_parameters(
            weights, parameters, mean, sd, computability_bound
        )

    @classmethod
    def get_parameters(
        cls,
        weights: Parameters,
        parameters: dict[str, Parameters] | None = None,
        mean: Parameter | None = None,
        sd: Parameter | None = None,
        computability_bound: float = 0.001,
    ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
        expected_columns = list(cls._distribution_map.keys())

        weights = cls.fill_missing_weights(weights, expected_columns)
        weights = format_data(weights, expected_columns, "weights")

        params: dict[str, pd.DataFrame] = {}
        for name, dist in cls._distribution_map.items():
            try:
                param = parameters[name] if parameters else None
                params[name] = dist.get_parameters(param, mean, sd, computability_bound)
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
        # pandas-stubs' _LocIndexerFrame.__setitem__ accepts list/mask/slice keys but
        # not a bare Index; .loc[Index] = DataFrame is valid at runtime. weights is a
        # DataFrame here (from format_data above).
        weights.loc[non_zero_rows.index] = non_zero_rows.divide(  # type: ignore[call-overload]
            non_zero_rows.sum(axis=1), axis=0
        )

        return weights, params

    @classmethod
    def get_expected_parameters(cls, distribution_name: str) -> list[str]:
        """Get the expected parameters for a given distribution in the ensemble."""
        return [
            *cls._distribution_map[distribution_name].expected_parameters,
            "x_min",
            "x_max",
        ]

    @staticmethod
    def fill_missing_weights(weights: Parameters, expected_columns: list[str]) -> Parameters:
        weights = copy.deepcopy(weights)

        # Get existing keys/columns/index based on weights type
        if isinstance(weights, dict):
            column_names = set(weights.keys())
        elif isinstance(weights, pd.DataFrame):
            column_names = set(weights.columns)
        elif isinstance(weights, pd.Series):
            column_names = set(weights.index)
        else:
            column_names = None  # For list, tuple, np.array, we can't fill missing columns

        # Add missing columns with 0.0 value. ``column_names`` is only populated in
        # the dict/DataFrame/Series branches above, so when it is truthy ``weights``
        # is one of those indexable types; cast documents that for the assignment.
        if column_names and column_names < set(expected_columns):
            indexable = cast("dict[str, Any] | pd.DataFrame | pd.Series[Any]", weights)
            for col in expected_columns:
                if col not in column_names:
                    indexable[col] = 0.0
        return weights

    def pdf(self, x: NumericInput) -> Numeric:
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

        result: Numeric = p
        if single_val:
            result = p.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", p.values)
        return result

    def ppf(
        self,
        q: NumericInput,
        q_dist: NumericInput,
    ) -> Numeric:
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

        result: Numeric = x
        if single_val:
            result = x.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", x.values)
        return result

    def cdf(self, x: NumericInput) -> Numeric:
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

        result: Numeric = c
        if single_val:
            result = c.iloc[0]
        if values_only:
            result = cast("npt.NDArray[Any]", c.values)
        return result


class NonConvergenceError(Exception):
    """Raised when the optimization fails to converge"""

    def __init__(self, message: str, dist: str) -> None:
        super().__init__(message)
        self.dist = dist


def _get_optimization_result(
    mean: pd.Series[Any],
    sd: pd.Series[Any],
    func: Callable[[npt.NDArray[Any], float, float], float],
    initial_func: Callable[[float, float], npt.NDArray[Any]],
) -> tuple[Any, ...]:
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
    mean_values, sd_values = mean.values, sd.values
    results = []
    with np.errstate(all="warn"):
        for i in range(len(mean_values)):
            initial_guess = initial_func(mean_values[i], sd_values[i])
            result = optimize.minimize(
                func,
                initial_guess,
                (
                    mean_values[i],
                    sd_values[i],
                ),
                method="Nelder-Mead",
                options={"maxiter": 10000},
            )
            results.append(result)
    return tuple(results)
