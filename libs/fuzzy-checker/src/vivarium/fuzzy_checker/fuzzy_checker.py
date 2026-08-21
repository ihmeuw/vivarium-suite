"""The :class:`FuzzyChecker` for statistical verification of stochastic simulation values."""
from __future__ import annotations

import os
from collections.abc import Collection
from functools import cache
from itertools import chain, combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import scipy.stats
from loguru import logger
from scipy.special import gammaln
from scipy.stats._distn_infrastructure import rv_continuous_frozen, rv_discrete_frozen

from vivarium.fuzzy_checker.data_structures import (
    MeanTestResult,
    TargetIntervalConfig,
    TestResult,
)

# The weight, in pseudo-observations, of the bug/issue hypothesis's mean prior in
# test_mean: mu | sigma^2 ~ N(mu0, sigma^2 / lambda0) with lambda0 this value, so
# the alternative's mean is deliberately almost data-free. The historic default
# interval of +/-62 data scales encoded exactly this weight at alpha_prior=2.
_BUG_MEAN_PSEUDO_OBSERVATIONS = 0.001


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
        self.proportion_test_diagnostics: list[TestResult] = []
        self.mean_test_diagnostics: list[MeanTestResult] = []

    def assert_proportion(
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
        test_proportion = self.test_proportion(
            name=name,
            name_additional=name_additional,
            target_proportion=target_proportion,
            observed_numerator=observed_numerator,
            observed_denominator=observed_denominator,
            bug_issue_beta_distribution_parameters=bug_issue_beta_distribution_parameters,
            fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
            inconclusive_bayes_factor_cutoff=inconclusive_bayes_factor_cutoff,
        )

        if test_proportion.reject_null:
            if test_proportion.observed_proportion < test_proportion.target_lower_bound:
                raise AssertionError(
                    f"{name} value {test_proportion.observed_proportion:g} is significantly less than expected, bayes factor = {test_proportion.bayes_factor:g}"
                )
            else:
                raise AssertionError(
                    f"{name} value {test_proportion.observed_proportion:g} is significantly greater than expected, bayes factor = {test_proportion.bayes_factor:g}"
                )

        if test_proportion.confidence == "Inconclusive":
            if (
                test_proportion.lower_bound_bayes_factor is not None
                and test_proportion.lower_bound_bayes_factor < fail_bayes_factor_cutoff
            ):
                logger.warning(
                    f"Sample size too small to ever find that the simulation's '{name}' value is less than expected."
                )

            if (
                test_proportion.upper_bound_bayes_factor is not None
                and test_proportion.upper_bound_bayes_factor < fail_bayes_factor_cutoff
            ):
                logger.warning(
                    f"Sample size too small to ever find that the simulation's '{name}' value is greater than expected."
                )

            if (
                fail_bayes_factor_cutoff
                > test_proportion.bayes_factor
                > inconclusive_bayes_factor_cutoff
            ):
                logger.warning(f"Bayes factor for '{name}' is not conclusive.")

        self.proportion_test_diagnostics.append(test_proportion)

    def test_proportion(
        self,
        name: str = "",
        name_additional: str = "",
        target_proportion: float | tuple[float, float] = 0.1,
        observed_numerator: int = 0,
        observed_denominator: int = 0,
        bug_issue_beta_distribution_parameters: tuple[float, float] = (0.5, 0.5),
        fail_bayes_factor_cutoff: float = 100.0,
        inconclusive_bayes_factor_cutoff: float = 0.1,
    ) -> TestResult:
        """Convert a dictionary representation of a test result to a TestResult object."""
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

        (
            confidence,
            lower_bound_bayes_factor,
            upper_bound_bayes_factor,
        ) = self._determine_confidence(
            target_lower_bound=target_lower_bound,
            target_upper_bound=target_upper_bound,
            observed_denominator=observed_denominator,
            bayes_factor=bayes_factor,
            fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
            inconclusive_bayes_factor_cutoff=inconclusive_bayes_factor_cutoff,
            bug_issue_distribution=bug_issue_distribution,
            no_bug_issue_distribution=no_bug_issue_distribution,
        )

        return TestResult(
            name=name,
            name_additional=name_additional,
            observed_proportion=observed_proportion,
            observed_numerator=observed_numerator,
            observed_denominator=observed_denominator,
            target_lower_bound=target_lower_bound,
            target_upper_bound=target_upper_bound,
            bayes_factor=bayes_factor,
            reject_null=reject_null,
            bug_issue_distribution=bug_issue_distribution,
            no_bug_issue_distribution=no_bug_issue_distribution,
            confidence=confidence,
            lower_bound_bayes_factor=lower_bound_bayes_factor,
            upper_bound_bayes_factor=upper_bound_bayes_factor,
        )

    @staticmethod
    def _get_stratification_combinations(
        index_names: list[str],
    ) -> list[tuple[str, ...]]:
        """Return all strict sub-combinations of index_names.

        Generates combinations of sizes len(index_names)-1 down to 1,
        excluding the full set (already tested per-row) and the empty
        set (already tested as the overall population-level check).
        """
        return list(
            chain.from_iterable(
                combinations(index_names, row) for row in range(len(index_names) - 1, 0, -1)
            )
        )

    def _apply_target_interval_config(
        self,
        target_val: float,
        index_info: dict[str, Any],
        config: TargetIntervalConfig | None = None,
    ) -> float | tuple[float, float]:
        """Check if a group matches the target interval config and return the
        (possibly updated) target value.

        Parameters
        ----------
        target_val
            The original target proportion value.
        index_info
            A mapping of index names to their values for the current row.
        config
            Optional configuration for applying a relative error to the target.

        Returns
        -------
            The original target_val if there is no config or it does not apply to
            this group, or a (lower_bound, upper_bound) tuple if it does.

        """
        if config is None or not config.applies_to(index_info):
            return target_val

        lower = target_val * (1 - config.relative_error)
        upper = target_val * (1 + config.relative_error)
        clipped_lower = max(0.0, lower)
        clipped_upper = min(1.0, upper)
        if clipped_lower != lower or clipped_upper != upper:
            logger.warning(
                f"Target interval clipped to [{clipped_lower}, {clipped_upper}] "
                f"(original: [{lower}, {upper}]) due to target_interval_configuration."
            )
        return (clipped_lower, clipped_upper)

    def _test_all_groups(
        self,
        data: pd.DataFrame,
        index_names: list[str],
        name: str,
        bug_issue_beta_distribution_parameters: tuple[float, float],
        fail_bayes_factor_cutoff: float,
        target_interval_config: TargetIntervalConfig | None = None,
    ) -> None:
        """Run test_proportion for each row in data and append results to diagnostics.

        Parameters
        ----------
        data
            DataFrame with columns "numerator", "denominator", and "target".
        index_names
            The index column names corresponding to the data's index levels.
        name
            The name of the proportion being tested.
        bug_issue_beta_distribution_parameters
            The parameters of the beta distribution characterizing the bug/issue hypothesis.
        fail_bayes_factor_cutoff
            The Bayes factor cutoff for rejecting the null hypothesis.
        target_interval_config
            Optional configuration for applying a relative error to the target proportions.
        """
        for idx, row in data.iterrows():
            numerator_val = row["numerator"]
            denominator_val = row["denominator"]

            if denominator_val == 0:
                if numerator_val > 0:
                    raise ValueError(
                        f"Group {idx} has a numerator of {numerator_val} but a denominator of 0."
                    )
                continue

            target_val = float(row["target"])

            if isinstance(idx, tuple):
                index_info = dict(zip(index_names, idx))
            elif index_names and index_names[0] is not None:
                index_info = {index_names[0]: idx}
            else:
                raise ValueError(
                    "Index must be a tuple or a single value with a named index level"
                )

            target_proportion = self._apply_target_interval_config(
                target_val, index_info, target_interval_config
            )

            result = self.test_proportion(
                name=name,
                name_additional=str(idx),
                observed_numerator=numerator_val,
                observed_denominator=denominator_val,
                target_proportion=target_proportion,
                bug_issue_beta_distribution_parameters=bug_issue_beta_distribution_parameters,
                fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
            )
            result.index_info = index_info
            self.proportion_test_diagnostics.append(result)

    def test_proportion_vectorized(
        self,
        observed_numerator: pd.DataFrame,
        observed_denominator: pd.DataFrame,
        target_proportion: pd.DataFrame,
        name: str = "",
        bug_issue_beta_distribution_parameters: tuple[float, float] = (0.5, 0.5),
        fail_bayes_factor_cutoff: float = 100.0,
        target_interval_config: TargetIntervalConfig | None = None,
    ) -> None:
        """Vectorized version of test_proportion that operates on DataFrames.

        Performs test_proportion for each row/index group in the input data structures,
        enabling efficient batch testing of multiple proportions.

        Parameters:
        -----------
        observed_numerator
            A DataFrame with a single column named "value" containing the observed number
            of events for each group. Values should represent counts
        observed_denominator
            A DataFrame with a single column named "value" containing the number of opportunities
            for events for each group. Values should represent counts
        target_proportion
            A DataFrame with a single column named "value" containing the target proportion
            for each group. Values should be floats between 0 and 1
        name
            The name of the proportion being tested
        bug_issue_beta_distribution_parameters
            The parameters of the beta distribution characterizing the bug/issue hypothesis.
        fail_bayes_factor_cutoff
            The Bayes factor above which a hypothesis test is considered to favor a bug/issue.
        target_interval_config
            Optional configuration for applying a relative error to the target proportions.

        """

        # Reorder index levels to match target_proportion for proper alignment
        target_index_order = target_proportion.index.names
        if isinstance(observed_numerator.index, pd.MultiIndex):
            observed_numerator = observed_numerator.reorder_levels(target_index_order)
        if isinstance(observed_denominator.index, pd.MultiIndex):
            observed_denominator = observed_denominator.reorder_levels(target_index_order)

        # NOTE: Use inner join to keep only rows where all three DataFrames have matching indices
        # Observed numerator and denominator should have the same indices, and target_proportion
        # might have additional levels where verification would be unnecessary
        combined_data = pd.concat(
            [
                observed_numerator["value"].rename("numerator"),
                observed_denominator["value"].rename("denominator"),
                target_proportion["value"].rename("target"),
            ],
            axis=1,
            join="inner",
        )

        # Test proportion for each group at the most granular level
        index_names = list(combined_data.index.names)
        self._test_all_groups(
            data=combined_data,
            index_names=index_names,
            name=name,
            bug_issue_beta_distribution_parameters=bug_issue_beta_distribution_parameters,
            fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
            target_interval_config=target_interval_config,
        )

        # Test sub-stratification combinations
        combined_data["weighted_target"] = (
            combined_data["target"] * combined_data["denominator"]
        )
        for stratifications in self._get_stratification_combinations(index_names):
            group_cols = list(stratifications)
            grouped = combined_data.groupby(group_cols, sort=False, observed=True)
            agg_data = grouped[["numerator", "denominator", "weighted_target"]].sum()
            agg_data["target"] = agg_data["weighted_target"] / agg_data["denominator"]

            self._test_all_groups(
                data=agg_data,
                index_names=group_cols,
                name=name,
                bug_issue_beta_distribution_parameters=bug_issue_beta_distribution_parameters,
                fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
                target_interval_config=target_interval_config,
            )

        # Test population level proportion
        # Calculate weighted average of target proportions (weighted by denominator)
        weighted_target = (
            combined_data["weighted_target"].sum() / combined_data["denominator"].sum()
        )

        # The population-level test has no index values, so applies_to is called
        # with an empty index_info
        overall_target: float | tuple[float, float] = self._apply_target_interval_config(
            target_val=weighted_target,
            index_info={},
            config=target_interval_config,
        )

        overall = self.test_proportion(
            name=name,
            name_additional="overall",
            observed_numerator=combined_data["numerator"].sum(),
            observed_denominator=combined_data["denominator"].sum(),
            target_proportion=overall_target,
            bug_issue_beta_distribution_parameters=bug_issue_beta_distribution_parameters,
            fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
        )
        self.proportion_test_diagnostics.append(overall)

    def _determine_confidence(
        self,
        target_lower_bound: float,
        target_upper_bound: float,
        observed_denominator: int,
        bayes_factor: float,
        fail_bayes_factor_cutoff: float,
        inconclusive_bayes_factor_cutoff: float,
        bug_issue_distribution: rv_discrete_frozen,
        no_bug_issue_distribution: rv_discrete_frozen,
    ) -> tuple[str, float | None, float | None]:
        """Determine confidence level and compute edge Bayes factors."""
        confidence = "Conclusive"
        lower_bound_bayes_factor = None
        upper_bound_bayes_factor = None

        if target_lower_bound > 0:
            lower_bound_bayes_factor = self._calculate_bayes_factor(
                0, bug_issue_distribution, no_bug_issue_distribution
            )
            if lower_bound_bayes_factor < fail_bayes_factor_cutoff:
                confidence = "Inconclusive"

        if target_upper_bound < 1:
            upper_bound_bayes_factor = self._calculate_bayes_factor(
                observed_denominator, bug_issue_distribution, no_bug_issue_distribution
            )
            if upper_bound_bayes_factor < fail_bayes_factor_cutoff:
                confidence = "Inconclusive"

        if fail_bayes_factor_cutoff > bayes_factor > inconclusive_bayes_factor_cutoff:
            confidence = "Inconclusive"

        return confidence, lower_bound_bayes_factor, upper_bound_bayes_factor

    def _calculate_bayes_factor(
        self,
        numerator: int,
        bug_distribution: rv_discrete_frozen,
        no_bug_distribution: rv_discrete_frozen,
    ) -> float:
        """Return the ratio of the bug to no-bug marginal likelihoods at the numerator."""
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
        """Return the summed squared error of the distribution's 2.5th and 97.5th quantiles."""
        squared_error_lower = self._quantile_squared_error(dist, lower_bound, 0.025)
        squared_error_upper = self._quantile_squared_error(dist, upper_bound, 0.975)

        try:
            return squared_error_lower + squared_error_upper
        except FloatingPointError:
            return float("inf")

    def _quantile_squared_error(
        self, dist: rv_continuous_frozen, value: float, intended_quantile: float
    ) -> float:
        """Return the squared logit error between the distribution's CDF at value and the intended quantile."""
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

    def assert_mean(
        self,
        observed_values: Collection[float] | None = None,
        target_mean: tuple[float, float] | float = 0.0,
        *,
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
        """Assert that observed continuous values came from a distribution with a target mean.

        Perform a Bayesian hypothesis test comparing how likely the observed data
        is under two scenarios -- one where the simulation is working as intended
        (target mean) and one where something is wrong (bug/issue mean). Raise an
        AssertionError if the test decisively favors the bug/issue scenario and
        warn if the test is not conclusive.

        For more details, see:
        https://vivarium-research.readthedocs.io/en/latest/model_design/vivarium_features/automated_v_and_v/index.html

        Parameters
        ----------
        observed_values
            The observed continuous values in the simulation. If omitted, all three
            of observed_zeroth_moment, observed_first_moment, and
            observed_second_moment must be supplied instead.
        target_mean
            What the mean *should* be if there is no bug/issue in the simulation,
            as the number of observations goes to infinity. A tuple of two floats
            is interpreted as the 2.5th and 97.5th percentiles of an uncertainty
            interval; a single float is interpreted as an exact value.
        observed_zeroth_moment
            The count of observed values.
        observed_first_moment
            The sum of observed values.
        observed_second_moment
            The sum of squares of observed values.
        fail_bayes_factor_cutoff
            The Bayes factor above which the test is considered to favor a
            bug/issue so strongly that the assertion should fail. The default of
            100 is conventionally called a "decisive" result.
        inconclusive_bayes_factor_cutoff
            The Bayes factor above which the test is considered inconclusive, not
            ruling out a bug/issue. Causes a warning.
        bug_issue_distribution_mean_uncertainty_interval
            What the mean might be if there is a bug/issue. Defaults to a very
            wide interval centered on the target, sized so the bug hypothesis's
            mean prior carries a weight of ``_BUG_MEAN_PSEUDO_OBSERVATIONS``
            (0.001) observations -- about 62 observed standard deviations to
            either side at the default priors.
        alpha_prior
            The alpha parameter of the inverse-gamma prior on the variance of the
            continuous values. Defaults to 2, which is weakly informative.
        beta_prior
            The beta parameter of the inverse-gamma prior on the variance.
            Defaults to ``(alpha_prior - 1) * observed_variance``, an
            empirical-Bayes choice that keeps the test's power independent of how
            the data's spread compares to its mean.
        name
            The name of the assertion, for use in messages and diagnostics.
        name_additional
            An optional additional name output in diagnostics but not warnings,
            e.g. the timestep when the assertion happened.

        Raises
        ------
        AssertionError
            If the test decisively favors the bug/issue hypothesis.
        """
        test_mean = self.test_mean(
            name=name,
            name_additional=name_additional,
            target_mean=target_mean,
            observed_values=observed_values,
            observed_zeroth_moment=observed_zeroth_moment,
            observed_first_moment=observed_first_moment,
            observed_second_moment=observed_second_moment,
            bug_issue_distribution_mean_uncertainty_interval=bug_issue_distribution_mean_uncertainty_interval,
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
            fail_bayes_factor_cutoff=fail_bayes_factor_cutoff,
        )

        if test_mean.reject_null:
            if test_mean.observed_mean < test_mean.target_lower_bound:
                raise AssertionError(
                    f"{name} value {test_mean.observed_mean:g} is significantly less "
                    f"than expected, bayes factor = {test_mean.bayes_factor:g}"
                )
            else:
                raise AssertionError(
                    f"{name} value {test_mean.observed_mean:g} is significantly greater "
                    f"than expected, bayes factor = {test_mean.bayes_factor:g}"
                )

        if fail_bayes_factor_cutoff > test_mean.bayes_factor > inconclusive_bayes_factor_cutoff:
            logger.warning(f"Bayes factor for '{name}' is not conclusive.")

        self.mean_test_diagnostics.append(test_mean)

    def test_mean(
        self,
        name: str = "",
        name_additional: str = "",
        target_mean: tuple[float, float] | float = 0.0,
        observed_values: Collection[float] | None = None,
        observed_zeroth_moment: int | None = None,
        observed_first_moment: float | None = None,
        observed_second_moment: float | None = None,
        bug_issue_distribution_mean_uncertainty_interval: tuple[float, float] | None = None,
        alpha_prior: float = 2.0,
        beta_prior: float | None = None,
        fail_bayes_factor_cutoff: float = 100.0,
    ) -> MeanTestResult:
        """Run the Bayesian hypothesis test for one observed mean and return its result.

        Parameters are as described in :meth:`assert_mean`.
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
        ), "Either all three moments or none of them must be supplied"
        assert (observed_first_moment is None) != (
            observed_values is None
        ), "Exactly one of observed_values or the three moments must be supplied"
        if observed_values is not None:
            values = np.asarray(observed_values, dtype=float)
            observed_zeroth_moment = len(values)
            observed_first_moment = float(np.sum(values))
            observed_second_moment = float(np.sum(values**2))

        assert (
            observed_zeroth_moment is not None
            and observed_first_moment is not None
            and observed_second_moment is not None
        )

        target_midpoint = (target_lower_bound + target_upper_bound) / 2
        observed_mean = observed_first_moment / observed_zeroth_moment
        observed_variance = (
            observed_second_moment / observed_zeroth_moment - observed_mean**2
        )
        observed_std = float(np.sqrt(observed_variance))

        if beta_prior is None:
            # Scale the variance prior by the observed spread of the data, not the
            # target mean: a prior that assumes sigma ~ |target mean| swamps the
            # evidence whenever the data's spread is much smaller than its mean,
            # silently costing the test its power against real bias.
            beta_prior = (alpha_prior - 1) * observed_variance

        if bug_issue_distribution_mean_uncertainty_interval is None:
            # Center the bug hypothesis on the target so that targets at or below
            # zero remain in-domain. The width encodes the prior weight directly:
            # this interval round-trips through
            # _compute_parameters_for_marginal_mu_interval to
            # lambda0 = _BUG_MEAN_PSEUDO_OBSERVATIONS for any alpha and beta
            # (it is ~62 observed SDs at the defaults).
            degrees_freedom = 2.0 * alpha_prior
            half_width = scipy.stats.t.ppf(0.975, df=degrees_freedom) * float(
                np.sqrt(beta_prior / (alpha_prior * _BUG_MEAN_PSEUDO_OBSERVATIONS))
            )
            bug_issue_distribution_mean_uncertainty_interval = (
                target_midpoint - half_width,
                target_midpoint + half_width,
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
            bayes_factor = float(
                np.exp(bug_issue_log_likelihood - no_bug_issue_log_likelihood)
            )

        reject_null = bayes_factor > fail_bayes_factor_cutoff

        return MeanTestResult(
            name=name,
            name_additional=name_additional,
            observed_mean=observed_mean,
            observed_std=observed_std,
            observed_count=observed_zeroth_moment,
            target_lower_bound=target_lower_bound,
            target_upper_bound=target_upper_bound,
            bayes_factor=bayes_factor,
            reject_null=reject_null,
        )

    def _compute_continuous_log_likelihood(
        self,
        target_mean: float | tuple[float, float],
        observed_zeroth_moment: int,
        observed_first_moment: float,
        observed_second_moment: float,
        alpha_prior: float,
        beta_prior: float,
    ) -> float:
        """Return the log marginal likelihood of the data under a fixed or uncertain mean."""
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
        """Return the log marginal likelihood under the free-mean model.

        The model is the standard normal-inverse-gamma conjugate family:
        ``y_i ~ N(mu, sigma^2)``, ``mu | sigma^2 ~ N(mu0, sigma^2/lambda0)``,
        ``sigma^2 ~ Inv-Gamma(alpha0, beta0)``.
        """
        n = zeroth_moment
        ybar = first_moment / zeroth_moment
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
        """Return the log marginal likelihood under the fixed-mean model.

        The model is ``y_i ~ N(mu_star, sigma^2)`` with
        ``sigma^2 ~ Inv-Gamma(alpha0, beta0)``.
        """
        n = zeroth_moment
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
        """Return (mu0, lambda0) whose marginal prior for mu has the desired 95% interval.

        After integrating out sigma^2 under ``Inv-Gamma(alpha_prior, beta_prior)``,
        the marginal prior for mu is a Student-t; solve for the (mu0, lambda0)
        that give it a central 95% interval of [desired_lower, desired_upper].
        """
        if desired_upper <= desired_lower:
            raise ValueError("desired_upper must be greater than desired_lower")

        if alpha_prior <= 0 or beta_prior <= 0:
            raise ValueError("alpha_prior and beta_prior must be positive")

        prior_mu_center = 0.5 * (desired_lower + desired_upper)
        half_width = 0.5 * (desired_upper - desired_lower)

        degrees_freedom = 2.0 * alpha_prior
        t975 = scipy.stats.t.ppf(0.975, df=degrees_freedom)

        # The marginal scale for mu satisfies s_mu^2 = beta0 / (alpha0 * lambda0).
        scale_mu_marginal = half_width / t975
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
        # Include the xdist worker id so parallel workers (separate processes)
        # don't overwrite each other's diagnostics file.
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        suffix = f"_{worker}" if worker else ""
        diagnostics: dict[str, list[TestResult] | list[MeanTestResult]] = {
            "proportion": self.proportion_test_diagnostics,
            "mean": self.mean_test_diagnostics,
        }
        for kind, results in diagnostics.items():
            output = pd.DataFrame(results)
            output.to_csv(
                Path(output_directory) / f"{kind}_test_diagnostics{suffix}.csv",
                index=False,
            )
