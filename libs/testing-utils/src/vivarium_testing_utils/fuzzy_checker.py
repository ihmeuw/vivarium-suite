#################
# Fuzzy Checker #
#################
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from itertools import chain, combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vivarium_testing_utils.automated_validation.comparison import TargetIntervalConfig

import numpy as np
import pandas as pd
import scipy.stats
from loguru import logger
from scipy.stats._distn_infrastructure import rv_continuous_frozen, rv_discrete_frozen


@dataclass
class TestResult:

    """Class to store metadata for individual tests run by FuzzyChecker."""

    name: str
    """Name of the test proportion being calculated."""
    name_additional: str
    """Additional name for test, used for when the same proportion is calculated multiple times."""
    observed_proportion: float
    """The observed proportion of a specific event happening."""
    observed_numerator: int
    """Observed counts of the event happening."""
    observed_denominator: int
    """Total counts of opportunities for the event to happen."""
    target_lower_bound: float
    """Lower bound of the target proportion range."""
    target_upper_bound: float
    """Upper bound of the target proportion range."""
    bayes_factor: float
    """Calculated Bayes factor from the test for the observed proportion."""
    reject_null: bool
    """Whether the null hypothesis was rejected."""
    bug_issue_distribution: tuple[float, float]
    """The bug/issue distribution used in the test."""
    no_bug_issue_distribution: rv_discrete_frozen
    """The no-bug/issue distribution used in the test."""
    index_info: dict[str, Any] | None = None
    """Index name mapping for name_additional attribute."""
    confidence: str = "Conclusive"
    """Whether the test result is conclusive or inconclusive based on sample size and Bayes factor."""
    lower_bound_bayes_factor: float | None = None
    """Bayes factor at numerator=0, used to check if sample size is too small for lower bound detection."""
    upper_bound_bayes_factor: float | None = None
    """Bayes factor at numerator=denominator, used to check if sample size is too small for upper bound detection."""

    @property
    def comparison_to_target(self) -> str:
        """Describe whether the observed proportion is below, above, or aligned with target."""
        if not self.reject_null:
            return "No significant difference"

        if self.observed_proportion < self.target_lower_bound:
            return "Underestimated"

        if self.observed_proportion > self.target_upper_bound:
            return "Overestimated"

        return "No significant difference"

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary of the main metadata for this TestResult, including index info if present."""
        results_dict = {
            "name": self.name,
            "name_additional": self.name_additional,
            "observed_proportion": self.observed_proportion,
            "observed_numerator": self.observed_numerator,
            "observed_denominator": self.observed_denominator,
            "target_lower_bound": self.target_lower_bound,
            "target_upper_bound": self.target_upper_bound,
            "bayes_factor": self.bayes_factor,
            "reject_null": self.reject_null,
            "comparison_to_target": self.comparison_to_target,
            "confidence": self.confidence,
        }
        if self.index_info is not None:
            results_dict["index_info"] = self.index_info
        return results_dict


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
            A mapping of stratification names to their values for the current row.
        config
            Optional configuration for applying a relative error to specific groups based on their index values.

        Returns
        -------
            The original target_val if no config or no match, or a
            (lower_bound, upper_bound) tuple if the config matches.

        """
        if config is None:
            return target_val

        update_target = True
        index_names = set(index_info.keys())
        for strat_name, filter_value in config.stratifications.items():
            if filter_value == "all" and strat_name in index_names:
                # "all" means the stratification must NOT be present
                update_target = False
                break
            elif filter_value == "specific" and strat_name not in index_names:
                # "specific" means the stratification must be present
                update_target = False
                break
            elif filter_value not in ("all", "specific") and (
                strat_name not in index_names or index_info.get(strat_name) != filter_value
            ):
                # A specific value: stratification must be present with this exact value
                update_target = False
                break

        if update_target:
            # All conditions matched — apply the relative error interval
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
        else:
            return target_val

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
            Optional configuration for applying a relative error to specific groups based on their index values.
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
            Optional configuration for applying a relative error to specific groups based on their index values.

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

        # Apply target interval config to overall test - this would require "all" for all stratifications
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
