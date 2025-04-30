#################
# Fuzzy Checker #
#################
from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import scipy.stats
from loguru import logger
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

    def save_diagnostic_output(self, output_directory: Path | str) -> None:
        """
        Note: Users will need to set the output directory by creating a fixture with
        the output directory and passing that fixture to the fixture that instantiates
        FuzzyChecker.
        Save diagnostics for optional human inspection.
        Can be useful to get more information about warnings, or to prioritize
        areas to be more thorough in manual V&V.
        """
        output = pd.DataFrame(self.proportion_test_diagnostics)
        output.to_csv(Path(output_directory) / "proportion_test_diagnostics.csv", index=False)
