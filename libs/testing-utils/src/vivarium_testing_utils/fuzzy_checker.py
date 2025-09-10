#################
# Fuzzy Checker #
#################
from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Collection

import numpy as np
import pandas as pd
import scipy.stats
from loguru import logger
from scipy.special import gammaln
from scipy.stats._distn_infrastructure import rv_continuous_frozen, rv_discrete_frozen


class FuzzyChecker:
    """
    This class manages "fuzzy" checks -- that is, checks of values that are
    subject to stochastic variation.
    It uses statistical hypothesis testing to determine whether the observed
    value in the simulation is extreme enough to reject the null hypothesis that
    the simulation is behaving correctly (according to a supplied verification
    or validation target).

    More detail about the statistics used here can be found at:
    https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#fuzzy-checking

    This is a class so that diagnostics for an entire test run can be tracked,
    and output to a file at the end of the run.

    To use this class, import it and create an instance as a fixture. Note: Users will need
    to pass a fixture containing the output directory for the diagnostics file to the fixture
    that instantiates FuzzyChecker. The output directory should also be added to the .gitignore

    @pytest.fixture(scope="session")
    def output_directory() -> str:
        return "path/to/output/directory"

    @pytest.fixture(scope="session")
    def fuzzy_checker(output_directory) -> FuzzyChecker:
        checker = FuzzyChecker()

        yield checker

        checker.save_diagnostic_output(output_directory)
    """

    def __init__(self) -> None:
        self.proportion_test_diagnostics: list[dict[str, Any]] = []
        self.mean_test_diagnostics: list[dict[str, Any]] = []

    def fuzzy_assert_proportion(
        self,
        observed_numerator: int,
        observed_denominator: int,
        target_proportion: tuple[float, float] | float,
        fail_bayes_factor_cutoff: float = 100.0,
        inconclusive_bayes_factor_cutoff: float = 0.1,
        bug_issue_beta_distribution_parameters: tuple[float, float] = (0.5, 0.5),
        name: str = "",
        name_additional: str = "",
    ) -> None:
        """
        Assert that an observed proportion of events came from a target distribution
        of proportions.
        This method performs a Bayesian hypothesis test between beta-binomial
        distributions based on the target (no bug/issue) and a "bug/issue" distribution
        and raises an AssertionError if the test decisively favors the "bug/issue" distribution.
        It warns, but does not fail, if the test is not conclusive (which usually
        means a larger population size is needed for a conclusive result),
        and gives an additional warning if the test could *never* be conclusive at this sample size.

        See more detail about the statistics used here:
        https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#proportions-and-rates

        :param observed_numerator:
            The observed number of events.
        :param observed_denominator:
            The number of opportunities there were for an event to be observed.
        :param target_proportion:
            What the proportion of events / opportunities *should* be if there is no bug/issue
            in the simulation, as the number of opportunities goes to infinity.
            If this parameter is a tuple of two floats, they are interpreted as the 2.5th percentile
            and the 97.5th percentile of the uncertainty interval about this value.
            If this parameter is a single float, it is interpreted as an exact value (no uncertainty).
            Setting this target distribution is a research task; there is much more guidance on
            doing so at https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#interpreting-the-hypotheses
        :param fail_bayes_factor_cutoff:
            The Bayes factor above which a hypothesis test is considered to favor a bug/issue so strongly
            that the assertion should fail.
            This cutoff trades off sensitivity with specificity and should be set in consultation with research;
            this is described in detail at https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#sensitivity-and-specificity
            The default of 100 is conventionally called a "decisive" result in Bayesian hypothesis testing.
        :param inconclusive_bayes_factor_cutoff:
            The Bayes factor above which a hypothesis test is considered to be inconclusive, not
            ruling out a bug/issue.
            This will cause a warning.
            The default of 0.1 represents what is conventionally considered "substantial" evidence in
            favor of no bug/issue.
        :param bug_issue_beta_distribution_parameters:
            The parameters of the beta distribution characterizing our subjective belief about what
            proportion would occur if there was a bug/issue in the simulation, as the sample size goes
            to infinity.
            Defaults to a Jeffreys prior, which has a decent amount of mass on the entire interval (0, 1) but
            more mass around 0 and 1.
            Generally the default should be used in most circumstances; changing it is probably a
            research decision.
        :param name:
            The name of the assertion, for use in messages and diagnostics.
            All assertions with the same name will output identical warning messages,
            which means pytest will aggregate those warnings.
        :param name_additional:
            An optional additional name attribute that will be output in diagnostics but not in warnings.
            Useful for e.g. specifying the timestep when an assertion happened.

        """
        if isinstance(target_proportion, tuple):
            target_lower_bound, target_upper_bound = target_proportion
        else:
            target_lower_bound = target_upper_bound = target_proportion

        assert (
            observed_numerator <= observed_denominator
        ), f"There cannot be more events ({observed_numerator}) than opportunities for events ({observed_denominator})"
        assert (
            target_upper_bound >= target_lower_bound
        ), f"The lower bound of the V&V target ({target_lower_bound}) cannot be greater than the upper bound ({target_upper_bound})"

        bug_issue_alpha, bug_issue_beta = bug_issue_beta_distribution_parameters
        bug_issue_distribution = scipy.stats.betabinom(
            a=bug_issue_alpha, b=bug_issue_beta, n=observed_denominator
        )

        if target_lower_bound == target_upper_bound:
            no_bug_issue_distribution: rv_discrete_frozen = scipy.stats.binom(
                p=target_lower_bound, n=observed_denominator
            )
        else:
            a, b = self._fit_beta_distribution_to_uncertainty_interval(
                target_lower_bound, target_upper_bound
            )

            no_bug_issue_distribution = scipy.stats.betabinom(
                a=a, b=b, n=observed_denominator
            )

        bayes_factor = self._calculate_bayes_factor(
            observed_numerator, bug_issue_distribution, no_bug_issue_distribution
        )

        observed_proportion = observed_numerator / observed_denominator
        reject_null = bayes_factor > fail_bayes_factor_cutoff
        self.proportion_test_diagnostics.append(
            {
                "name": name,
                "name_addl": name_additional,
                "observed_proportion": observed_proportion,
                "observed_numerator": observed_numerator,
                "observed_denominator": observed_denominator,
                "target_lower_bound": target_lower_bound,
                "target_upper_bound": target_upper_bound,
                "bayes_factor": bayes_factor,
                "reject_null": reject_null,
            }
        )

        if reject_null:
            if observed_proportion < target_lower_bound:
                raise AssertionError(
                    f"{name} value {observed_proportion:g} is significantly less than expected, bayes factor = {bayes_factor:g}"
                )
            else:
                raise AssertionError(
                    f"{name} value {observed_proportion:g} is significantly greater than expected, bayes factor = {bayes_factor:g}"
                )

        if (
            target_lower_bound > 0
            and self._calculate_bayes_factor(
                0, bug_issue_distribution, no_bug_issue_distribution
            )
            < fail_bayes_factor_cutoff
        ):
            logger.warning(
                f"Sample size too small to ever find that the simulation's '{name}' value is less than expected."
            )

        if target_upper_bound < 1 and (
            self._calculate_bayes_factor(
                observed_denominator, bug_issue_distribution, no_bug_issue_distribution
            )
            < fail_bayes_factor_cutoff
        ):
            logger.warning(
                f"Sample size too small to ever find that the simulation's '{name}' value is greater than expected."
            )

        if fail_bayes_factor_cutoff > bayes_factor > inconclusive_bayes_factor_cutoff:
            logger.warning(f"Bayes factor for '{name}' is not conclusive.")

    def fuzzy_assert_mean(
        self,
        target_mean: tuple[float, float] | float,
        observed_values: Collection[float] | None = None,
        observed_zeroth_moment: int | None = None,
        observed_first_moment: float | None = None,
        observed_second_moment: float | None = None,
        fail_bayes_factor_cutoff: float = 100.0,
        inconclusive_bayes_factor_cutoff: float = 0.1,
        bug_issue_distribution_mean_uncertainty_interval: tuple[float, float] | None = None,
        alpha_prior: float = 2.0,
        beta_prior: float | None = None,
        name: str = "",
        name_additional: str = "",
    ) -> None:
        """
        Checks if the observed average (mean) of a set of values matches what you expect, allowing for randomness.
        This uses Bayesian hypothesis testing: it compares how likely your observed data is under two scenarios—one where the simulation is working as intended (target mean), and one where something is wrong (bug/issue mean).
        If the evidence strongly favors the bug/issue scenario, the test fails. If the evidence is unclear, you get a warning.

        Intuitive explanation:
        - We summarize your observed data using simple statistics (count, sum, sum of squares).
        - We use a mathematical model (normal distribution with uncertainty about its mean and variance) to describe what we expect if things are working, and what we expect if something is wrong.
        - We calculate how likely your data is under each scenario, and compare them. If your data is much more likely under the bug/issue scenario, we flag it.

        For more details, see:
        https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#proportions-and-rates

        :param target_mean:
            What the mean *should* be if there is no bug/issue
            in the simulation, as the number of observations/simulants goes to infinity.
            If this parameter is a tuple of two floats, they are interpreted as the 2.5th percentile
            and the 97.5th percentile of the uncertainty interval about this value.
            If this parameter is a single float, it is interpreted as an exact value (no uncertainty).
            Setting this target distribution is a research task; there is much more guidance on
            doing so at https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#interpreting-the-hypotheses
        :param observed_values:
            The observed continuous values in the simulation.
            If omitted, all three of observed_zeroth_moment, observed_first_moment, and observed_second_moment
            must be supplied.
        :param observed_zeroth_moment:
            The zeroth moment of the continuous values in the simulation, which is simply the length of the
            collection of continuous values in the simulation (i.e. the population size if each simulant
            has one continuous value included in this check).
        :param observed_first_moment:
            The first moment of the continuous values in the simulation, which is their sum.
        :param observed_second_moment:
            The second moment of the continuous values in the simulation, which is sum of their squares.
        :param fail_bayes_factor_cutoff:
            The Bayes factor above which a hypothesis test is considered to favor a bug/issue so strongly
            that the assertion should fail.
            This cutoff trades off sensitivity with specificity and should be set in consultation with research;
            this is described in detail at https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html#sensitivity-and-specificity
            The default of 100 is conventionally called a "decisive" result in Bayesian hypothesis testing.
        :param inconclusive_bayes_factor_cutoff:
            The Bayes factor above which a hypothesis test is considered to be inconclusive, not
            ruling out a bug/issue.
            This will cause a warning.
            The default of 0.1 represents what is conventionally considered "substantial" evidence in
            favor of no bug/issue.
        :param bug_issue_distribution_mean_uncertainty_interval:
            Describes what the mean might be if there is a bug/issue. Uses a very wide interval by default.
        :param alpha_prior:
            The alpha parameter of the inverse-gamma prior on the variance of the continuous values.
            Defaults to 2, which is weakly informative.
        :param beta_prior:
            The beta parameter of the inverse-gamma prior on the variance of the continuous values.
            Defaults to (alpha_prior - 1) * data_scale**2 where data_scale is the target mean (if a point)
            or the midpoint of the target (if a UI), which is weakly informative.
        :param name:
            The name of the assertion, for use in messages and diagnostics.
            All assertions with the same name will output identical warning messages,
            which means pytest will aggregate those warnings.
        :param name_additional:
            An optional additional name attribute that will be output in diagnostics but not in warnings.
            Useful for e.g. specifying the timestep when an assertion happened.

        """
        if isinstance(target_mean, tuple):
            target_lower_bound, target_upper_bound = target_mean
        else:
            target_lower_bound = target_upper_bound = target_mean

        assert (
            target_upper_bound >= target_lower_bound
        ), f"The lower bound of the V&V target ({target_lower_bound}) cannot be greater than the upper bound ({target_upper_bound})"

        assert (
            (observed_zeroth_moment is None)
            == (observed_first_moment is None)
            == (observed_second_moment is None)
        )
        assert (observed_first_moment is None) != (observed_values is None)
        if observed_values is not None:
            observed_values = np.array(observed_values)
            observed_zeroth_moment = len(observed_values)
            observed_first_moment = np.sum(observed_values)
            observed_second_moment = np.sum(np.array(observed_values))

        assert (
            observed_zeroth_moment is not None
            and observed_first_moment is not None
            and observed_second_moment is not None
        )

        data_scale = (target_lower_bound + target_upper_bound) / 2
        if beta_prior is None:
            # Suggested by ChatGPT as a reasonable "weakly informative"
            # prior, using a priori information about the expected scale
            # of the data
            # https://chatgpt.com/share/68b768f5-214c-8005-8d4c-1b12c6c4f2d0
            beta_prior = (alpha_prior - 1) * data_scale**2

        if bug_issue_distribution_mean_uncertainty_interval is None:
            # With the default alpha and beta priors, this recovers the default lambda
            # prior ChatGPT suggested above.
            # https://chatgpt.com/share/68b768f5-214c-8005-8d4c-1b12c6c4f2d0
            bug_issue_distribution_mean_uncertainty_interval = (
                -62.0 * data_scale,
                62.0 * data_scale,
            )

        bug_issue_log_likelihood = self._compute_continuous_log_likelihood(
            bug_issue_distribution_mean_uncertainty_interval,
            observed_zeroth_moment,
            observed_first_moment,
            observed_second_moment,
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
        )

        no_bug_issue_log_likelihood = self._compute_continuous_log_likelihood(
            target_mean,
            observed_zeroth_moment,
            observed_first_moment,
            observed_second_moment,
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
        )

        with np.errstate(under="ignore", over="ignore"):
            bayes_factor = np.exp(bug_issue_log_likelihood - no_bug_issue_log_likelihood)

        # https://chatgpt.com/s/t_68c0a1ef82748191872184e93b7bc7e0
        observed_mean = observed_first_moment / observed_zeroth_moment
        observed_variance = (
            observed_second_moment / observed_zeroth_moment - observed_mean**2
        )
        observed_std = np.sqrt(observed_variance)
        reject_null = bayes_factor > fail_bayes_factor_cutoff
        self.mean_test_diagnostics.append(
            {
                "name": name,
                "name_addl": name_additional,
                "observed_mean": observed_mean,
                "observed_std": observed_std,
                "observed_count": observed_zeroth_moment,
                "target_lower_bound": target_lower_bound,
                "target_upper_bound": target_upper_bound,
                "bayes_factor": bayes_factor,
                "reject_null": reject_null,
            }
        )

        if reject_null:
            if observed_mean < target_lower_bound:
                raise AssertionError(
                    f"{name} value {observed_mean:g} is significantly less than expected, bayes factor = {bayes_factor:g}"
                )
            else:
                raise AssertionError(
                    f"{name} value {observed_mean:g} is significantly greater than expected, bayes factor = {bayes_factor:g}"
                )

        if fail_bayes_factor_cutoff > bayes_factor > inconclusive_bayes_factor_cutoff:
            logger.warning(f"Bayes factor for '{name}' is not conclusive.")

    def _calculate_bayes_factor(
        self,
        numerator: int,
        bug_distribution: rv_discrete_frozen,
        no_bug_distribution: rv_discrete_frozen,
    ) -> float:
        # We can be dealing with some _extremely_ unlikely events here, so we have to set numpy to not error
        # if we generate a probability too small to be stored in a floating point number(!), which is known
        # as "underflow"
        with np.errstate(under="ignore"):
            bug_marginal_likelihood = float(bug_distribution.pmf(numerator))
            no_bug_marginal_likelihood = float(no_bug_distribution.pmf(numerator))

        try:
            return bug_marginal_likelihood / no_bug_marginal_likelihood
        except (ZeroDivisionError, FloatingPointError):
            return float("inf")

    @cache
    def _fit_beta_distribution_to_uncertainty_interval(
        self, lower_bound: float, upper_bound: float
    ) -> tuple[float, float]:
        """
        Finds a and b parameters of a beta distribution that approximates the specified 95% UI.
        The overall approach was inspired by https://stats.stackexchange.com/a/112671/.

        SciPy optimization methods turned out not to be able to search such a large and unbounded
        space of possibilities.

        Additionally, they suffer from problems with floating-point precision, which can lead
        to nonsensical results because those methods don't "know" what we know about how beta
        distributions vary with their parameters, and numerical approximation of the derivatives
        is inaccurate.

        An example of a substantial problem here is that very incorrect parameters will have
        CDF values smaller than floating point error at our desired bounds, so they will be
        indistinguishable from each other for derivative purposes, and the derivative might even go the wrong way.

        To address these issues, we use a heuristic approach based on binary search
        and knowledge about how beta distributions react to their parameters
        (using the concentration-and-mean parameterization, since that has clearer behavior):
        - Increasing concentration makes the bounds narrower
        - Decreasing concentration makes the bounds wider
        - Increasing mean increases both bounds
        - Decreasing mean decreases both bounds

        It is much harder to search for the correct concentration -- which is essentially unbounded
        except for overflow limits -- than the correct mean.
        Our strategy is based on this fact: we make mean more "sticky" (only update our best guess
        when we find we must move mean to the left or right), and restart our mean search from scratch
        each time we change the concentration.
        We tried other strategies, but they didn't work consistently.

        This method has been tested on a wide range of inputs and finds reasonable solutions even when
        the bounds themselves (or the difference between them) are only a few orders of magnitude
        larger than the floating point precision.
        """
        assert 0 < lower_bound < upper_bound < 1

        concentration_max = 1e40
        concentration_min = 1e-3

        mean_max = upper_bound
        mean_min = lower_bound
        mean = (upper_bound + lower_bound) / 2

        # Make this a really large number so we are always less than this value in the
        # first iteration of the loop.
        best_error = float(np.finfo(float).max)

        for _ in range(1_000):
            with np.errstate(under="ignore"):
                concentration = np.exp(
                    (np.log(concentration_max) + np.log(concentration_min)) / 2
                )
                dist = scipy.stats.beta(
                    a=mean * concentration,
                    b=(1 - mean) * concentration,
                )
                lb_cdf = dist.cdf(lower_bound)
                ub_cdf = dist.cdf(upper_bound)

                error = self._uncertainty_interval_squared_error(
                    dist, lower_bound, upper_bound
                )
                if error < best_error:
                    best_error = error
                    best_concentration = concentration
                    best_mean = mean
                if best_error < 1e-5:
                    break

                concentration_bounds_changed = False
                mean_bounds_changed = False
                if lb_cdf < 0.025 and ub_cdf > (1 - 0.025):
                    # The distribution is too narrow, so we need to reduce our concentration.
                    concentration_max = concentration
                    concentration_bounds_changed = True
                elif lb_cdf > 0.025 and ub_cdf < (1 - 0.025):
                    # The distribution is too wide, so we need to increase concentration.
                    concentration_min = concentration
                    concentration_bounds_changed = True
                elif ub_cdf >= lb_cdf > 0.025 and 1 >= ub_cdf > (1 - 0.025):
                    # The distribution is high on both quantiles, so we need to decrease the mean.
                    # mean_lower_bound = mean
                    mean_min = mean
                    mean_bounds_changed = True
                elif lb_cdf <= ub_cdf < (1 - 0.025) and 0 <= lb_cdf < 0.025:
                    # The distribution is low on both quantiles, so we need to increase the mean
                    # mean_upper_bound = mean
                    mean_max = mean
                    mean_bounds_changed = True

                if not concentration_bounds_changed and not mean_bounds_changed:
                    break

                if concentration_bounds_changed:
                    # We have been optimizing mean with inaccurate concentration bounds; let's restart
                    # our mean search (which is pretty small/cheap).
                    mean_max = upper_bound
                    mean_min = lower_bound

                if mean_bounds_changed:
                    mean = (mean_min + mean_max) / 2
                    # We have been optimizing concentration with inaccurate mean bounds; let's back off
                    # a bit to explore concentration more.
                    # NOTE: The convergence of this method depends pretty crucially on this backoff
                    # constant. Without it, we don't converge at all in some cases.
                    # If it is too high, convergence is slow and sometimes runs out of iterations.
                    # 2 worked well across a wide range of inputs in preliminary testing.
                    concentration_max = min(concentration_max * 2, 1e40)
                    concentration_min = max(concentration_min / 2, 1e-3)

        assert (
            best_error < 0.1
        ), f"Beta distribution fitting for {lower_bound}, {upper_bound} failed with UI squared error {best_error}"
        if best_error > 1e-5:
            logger.warning(
                f"Didn't find a very good beta distribution for {lower_bound}, {upper_bound} -- using a best guess with UI squared error {best_error}"
            )

        result = (
            best_mean * best_concentration,
            (1 - best_mean) * best_concentration,
        )
        assert len(result) == 2
        return tuple(result)

    def _uncertainty_interval_squared_error(
        self, dist: rv_continuous_frozen, lower_bound: float, upper_bound: float
    ) -> float:
        squared_error_lower = self._quantile_squared_error(dist, lower_bound, 0.025)
        squared_error_upper = self._quantile_squared_error(dist, upper_bound, 0.975)

        try:
            return squared_error_lower + squared_error_upper
        except FloatingPointError:
            return float("inf")

    def _quantile_squared_error(
        self, dist: rv_continuous_frozen, value: float, intended_quantile: float
    ) -> float:
        with np.errstate(under="ignore"):
            actual_quantile = dist.cdf(value)

        if 0 < actual_quantile < 1:
            return float(
                (
                    scipy.special.logit(actual_quantile)
                    - scipy.special.logit(intended_quantile)
                )
                ** 2
            )
        else:
            # In this case, we were so far off that the actual quantile can't even be
            # precisely calculated.
            # We return an arbitrarily large penalty to ensure this is never selected as the minimum.
            return float("inf")

    def _compute_continuous_log_likelihood(
        self,
        target_mean: float | tuple[float, float],
        observed_zeroth_moment: int,
        observed_first_moment: float,
        observed_second_moment: float,
        alpha_prior: float,
        beta_prior: float,
    ) -> float:
        """
        Calculates how likely your observed data is under a model where the mean is either fixed or uncertain.
        If target_mean is a tuple, we allow for uncertainty in the mean (using a range). If it's a float, we treat the mean as exact.
        This function chooses the right formula for each case and returns the log-likelihood (a measure of how well the data fits the model).

        Intuitive explanation:
        - If you have uncertainty about the mean, we use a model that allows the mean to vary within your interval.
        - If you know the mean exactly, we use a simpler model.
        """
        if isinstance(target_mean, tuple):
            assert len(target_mean) == 2
            prior_mu_center, lambda_prior = self._compute_parameters_for_marginal_mu_interval(
                target_mean[0], target_mean[1], alpha_prior, beta_prior
            )

            return self._log_likelihood_normal_inverse_gamma(
                observed_zeroth_moment,
                observed_first_moment,
                observed_second_moment,
                mu0=prior_mu_center,
                lambda0=lambda_prior,
                alpha0=alpha_prior,
                beta0=beta_prior,
            )
        else:
            return self._log_likelihood_normal_inverse_gamma_fixed_mean(
                observed_zeroth_moment,
                observed_first_moment,
                observed_second_moment,
                mu_star=target_mean,
                alpha0=alpha_prior,
                beta0=beta_prior,
            )

    def _log_likelihood_normal_inverse_gamma(
        self,
        zeroth_moment: int,
        first_moment: float,
        second_moment: float,
        mu0: float,
        lambda0: float,
        alpha0: float,
        beta0: float,
    ) -> float:
        """
        Calculates the log-likelihood for your data under a model where the mean is uncertain (not fixed), but centered at mu0 with a certain strength (lambda0).
        This is the standard Bayesian update for a normal distribution with unknown mean and variance, using a normal-inverse-gamma prior.

        Free mean model:
        y_i ~ N(mu, sigma^2)
        mu | sigma^2 ~ N(mu0, sigma^2/lambda0)
        sigma^2 ~ Inv-Gamma(alpha0, beta0)
        Returns log p(y | A).

        See https://chatgpt.com/s/t_68c0a68c053c8191a53e86d1201e7864.
        """
        n = zeroth_moment
        ybar = first_moment / zeroth_moment
        # https://chatgpt.com/s/t_68c09ef4028c8191a4f32acd26987bff
        S = second_moment - 2 * ybar * first_moment + zeroth_moment * (ybar**2)

        lambda_n = lambda0 + n
        alpha_n = alpha0 + n / 2.0
        beta_n = beta0 + 0.5 * (S + (lambda0 * n / lambda_n) * (ybar - mu0) ** 2)

        return float(
            -0.5 * n * np.log(2.0 * np.pi)
            + 0.5 * (np.log(lambda0) - np.log(lambda_n))
            + (gammaln(alpha_n) - gammaln(alpha0))
            + alpha0 * np.log(beta0)
            - alpha_n * np.log(beta_n)
        )

    def _log_likelihood_normal_inverse_gamma_fixed_mean(
        self,
        zeroth_moment: int,
        first_moment: float,
        second_moment: float,
        mu_star: float,
        alpha0: float,
        beta0: float,
    ) -> float:
        """
        Calculates the log-likelihood for your data under a model where the mean is fixed at mu_star.
        This is the standard Bayesian update for a normal distribution with known mean and unknown variance, using an inverse-gamma prior.

        Point mean model:
        y_i ~ N(mu_star, sigma^2)
        sigma^2 ~ Inv-Gamma(alpha0, beta0)
        Returns log p(y | B).
        """
        n = zeroth_moment
        # https://chatgpt.com/s/t_68c09ef4028c8191a4f32acd26987bff
        S_star = second_moment - 2 * mu_star * first_moment + zeroth_moment * (mu_star**2)

        alpha_n = alpha0 + n / 2.0
        beta_n = beta0 + 0.5 * S_star

        return float(
            -0.5 * n * np.log(2.0 * np.pi)
            + (gammaln(alpha_n) - gammaln(alpha0))
            + alpha0 * np.log(beta0)
            - alpha_n * np.log(beta_n)
        )

    def _compute_parameters_for_marginal_mu_interval(
        self,
        desired_lower: float,
        desired_upper: float,
        alpha_prior: float,
        beta_prior: float,
    ) -> tuple[float, float]:
        """
        Compute conjugate prior parameters (mu0, lambda0) so that the *marginal*
        prior for mu (after integrating out sigma^2 under an Inv-Gamma(alpha_prior,beta_prior))
        has a central 95% interval equal to [desired_lower, desired_upper].

        Parameters
        ----------
        desired_lower, desired_upper : float
            Desired central 95% prior interval for mu.
        alpha_prior : float
            alpha parameter of the Inv-Gamma prior on sigma^2 (shape).
            Must be > 0. Typical weak choice: alpha_prior = 2.0.
        beta_prior : float
            beta parameter (scale) of the Inv-Gamma prior on sigma^2.
            Choose based on prior beliefs about sigma (must be > 0).

        Returns
        -------
        prior_mu_center : float
            mu0, the center/location of the conditional Normal prior for mu|sigma2.
        prior_lambda : float
            lambda0, the strength parameter for mu|sigma2 ~ N(mu0, sigma2/lambda0).
        """
        if desired_upper <= desired_lower:
            raise ValueError("desired_upper must be greater than desired_lower")

        if alpha_prior <= 0 or beta_prior <= 0:
            raise ValueError("alpha_prior and beta_prior must be positive")

        # center and half-width of desired interval
        prior_mu_center = 0.5 * (desired_lower + desired_upper)
        half_width = 0.5 * (desired_upper - desired_lower)

        # degrees of freedom of the marginal Student-t for mu
        degrees_freedom = 2.0 * alpha_prior

        # 97.5% quantile of a standard Student-t with nu df
        from scipy.stats import t

        t975 = t.ppf(0.975, df=degrees_freedom)

        # desired marginal scale for mu: s_mu such that t975 * s_mu == half_width
        scale_mu_marginal = half_width / t975

        # solve for lambda0 from s_mu^2 = beta / (alpha * lambda0)
        prior_lambda = beta_prior / (alpha_prior * scale_mu_marginal**2)

        return prior_mu_center, float(prior_lambda)

    def save_diagnostic_output(self, output_directory: Path | str) -> None:
        """
        Note: Users will need to set the output directory by creating a fixture with
        the output directory and passing that fixture to the fixture that instantiates
        FuzzyChecker.
        Save diagnostics for optional human inspection.
        Can be useful to get more information about warnings, or to prioritize
        areas to be more thorough in manual V&V.
        """
        pd.DataFrame(self.proportion_test_diagnostics).to_csv(
            Path(output_directory) / "proportion_test_diagnostics.csv", index=False
        )
        pd.DataFrame(self.mean_test_diagnostics).to_csv(
            Path(output_directory) / "mean_test_diagnostics.csv", index=False
        )
