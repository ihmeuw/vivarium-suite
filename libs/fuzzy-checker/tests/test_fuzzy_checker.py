from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pytest
import scipy
from _pytest.logging import LogCaptureFixture
from scipy.stats._distn_infrastructure import rv_continuous_frozen

if TYPE_CHECKING:
    from py._path.local import LocalPath

from vivarium.fuzzy_checker import FuzzyChecker, TargetIntervalConfig, TestResult

OBSERVED_DENOMINATORS = [100_000, 1_000_000, 10_000_000]
TARGET_PROPORTION = 0.1
LOWER_BOUNDS = [
    1e-14,
    1e-10,
    0.000001,
    0.01,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.8,
    0.9,
    0.99999,
    1.0 - 1e-10,
]
WIDTHS = [
    1e-14,
    1e-12,
    1e-10,
    0.0000001,
    0.00001,
    0.001,
    0.01,
    0.03,
    0.05,
    0.1,
    0.2,
    0.4,
    0.6,
    0.9,
]


@pytest.mark.parametrize(
    "numerator, denominator, target_proportion",
    [(10_008, 100_000, 0.1), (976, 1_000_000, 0.001), (1_049, 50_000, (0.0198, 0.0202))],
)
def test_pass_assert_proportion(
    numerator: int, denominator: int, target_proportion: float
) -> None:
    FuzzyChecker().assert_proportion(numerator, denominator, target_proportion)


@pytest.mark.parametrize(
    "numerator, denominator, target_proportion, match",
    [
        (901, 100_000, 0.05, "is significantly less than expected"),
        (1_150, 50_000, 0.02, "is significantly greater than expected"),
    ],
)
def test_fail_assert_proportion(
    numerator: int, denominator: int, target_proportion: float, match: str
) -> None:
    with pytest.raises(AssertionError, match=match):
        FuzzyChecker().assert_proportion(numerator, denominator, target_proportion)


def test_small_sample_size_assert_proportion(caplog: LogCaptureFixture) -> None:
    FuzzyChecker().assert_proportion(1, 10, 0.1)
    assert "Sample size too small" in caplog.text


def test_not_conclusive_assert_proportion(caplog: LogCaptureFixture) -> None:
    """This test verifies we will pass, then be inconclusive, then fail.
    The numbers used in this test are arbitrary but are intended to be conservative
    estimates of the number of iterations needed to reach each state
    Creating an instance here allows us to cache some of the computation for the while loop
    """
    fuzzy_checker = FuzzyChecker()
    numerator = 1_000
    while True:
        caplog.clear()
        fuzzy_checker.assert_proportion(numerator, 10_000, 0.1)
        if "is not conclusive" in caplog.text:
            assert numerator > 1050
            break
        if numerator > 1_200:
            raise RuntimeError("Test did not reach the expected warning")
        numerator += 1

    while True:
        caplog.clear()
        try:
            fuzzy_checker.assert_proportion(numerator, 10_000, 0.1)
            assert "is not conclusive" in caplog.text
        except AssertionError as e:
            assert "is significantly greater" in str(e)
            assert numerator > 1_100
            break
        if numerator > 1_300:
            raise RuntimeError("Test did not reach the expected warning")
        numerator += 1


@pytest.mark.parametrize("step", (-1, 1))
def test__calculate_bayes_factor(step: int) -> None:
    # This is the base case where our numerator / denominator = target_proportion
    numerator = 10_000
    denominator = 100_000
    # Parametrize rv_discrete for no bug distribution
    # I am keeping the defaults for the bug distribution to remain 0.5 for alpha and beta
    bug_issue_distribution = scipy.stats.betabinom(a=0.5, b=0.5, n=denominator)
    no_bug_issue_distribution = scipy.stats.binom(p=TARGET_PROPORTION, n=denominator)
    bayes_factor = FuzzyChecker()._calculate_bayes_factor(
        numerator, bug_issue_distribution, no_bug_issue_distribution
    )
    previous_bayes_factor = bayes_factor
    assert isinstance(bayes_factor, float)
    assert bayes_factor > 0
    while numerator > 0 and numerator < 100_000:
        numerator += step
        bayes_factor = FuzzyChecker()._calculate_bayes_factor(
            numerator, bug_issue_distribution, no_bug_issue_distribution
        )
        assert isinstance(bayes_factor, float)
        assert bayes_factor > 0
        # Break once we reach infinity
        if bayes_factor == float("inf"):
            # Simple check to make sure this doesn't happen too early
            assert abs(numerator - 10_000) > 50
            break
        # Check that Bayes factor is getting larger (except for small wiggles) as we move
        # further from the target proportion
        assert bayes_factor - previous_bayes_factor >= float(np.finfo(float).min) * 1_000
        previous_bayes_factor = bayes_factor


def test_zero_division__calculate_bayes_factor() -> None:
    # This is just testing that we will hit a zero division error or floating point error
    # and handle it correctly.
    # We want the case where we observe a proportion that indicates an event is very likely
    # but we expect it to be very unlikely.
    numerator = 10_000_000 - 1
    denominator = 10_000_000
    target_proportion = 0.1
    # I am keeping the defaults for the bug distribution to remain 0.5 for alpha and beta
    bug_issue_distribution = scipy.stats.betabinom(a=0.5, b=0.5, n=denominator)
    no_bug_issue_distribution = scipy.stats.binom(p=target_proportion, n=denominator)
    bayes_factor = FuzzyChecker()._calculate_bayes_factor(
        numerator, bug_issue_distribution, no_bug_issue_distribution
    )
    assert bayes_factor == float("inf")


@pytest.mark.parametrize("lower_bound", LOWER_BOUNDS)
@pytest.mark.parametrize("width", WIDTHS)
def test__fit_beta_distribution_to_uncertainty_interval(
    lower_bound: float, width: float
) -> None:
    upper_bound = lower_bound + width
    if upper_bound >= 1:
        pytest.skip("Upper bound cannot be more than 1.")
    a, b = FuzzyChecker()._fit_beta_distribution_to_uncertainty_interval(
        lower_bound, upper_bound
    )
    dist = scipy.stats.beta(
        a=a,
        b=b,
    )
    with np.errstate(under="ignore"):
        lb_cdf = dist.cdf(lower_bound)
        ub_cdf = dist.cdf(upper_bound)
    assert np.isclose(
        lb_cdf, 0.025, atol=0.01
    ), f"{lb_cdf} not close to {0.025}, {lower_bound} {upper_bound}"
    assert np.isclose(
        ub_cdf, 0.975, atol=0.01
    ), f"{ub_cdf} not close to {0.975}, {lower_bound} {upper_bound}"


def test__imprecise_fit_beta_distribution(caplog: LogCaptureFixture) -> None:
    # We want a narrow distribution with a small lower bound
    lower_bound = 0.1
    width = 1e-14
    upper_bound = lower_bound + width
    a, b = FuzzyChecker()._fit_beta_distribution_to_uncertainty_interval(
        lower_bound, upper_bound
    )
    assert "Didn't find a very good beta distribution" in caplog.text


@pytest.mark.parametrize("lower_bound", LOWER_BOUNDS)
@pytest.mark.parametrize("width", WIDTHS)
def test__uncertainty_interval_squared_error(lower_bound: float, width: float) -> None:
    upper_bound = lower_bound + width
    if upper_bound >= 1:
        pytest.skip("Upper bound cannot be more than 1.")

    dist = _make_beta_distribution(lower_bound, upper_bound)
    error = FuzzyChecker()._uncertainty_interval_squared_error(dist, lower_bound, upper_bound)
    assert isinstance(error, float)


@pytest.mark.parametrize("lower_bound", LOWER_BOUNDS, ids=lambda x: x)
@pytest.mark.parametrize("width", WIDTHS, ids=lambda x: x)
def test__quantile_squared_error(lower_bound: float, width: float) -> None:
    upper_bound = lower_bound + width
    if upper_bound >= 1:
        pytest.skip("Upper bound cannot be more than 1.")

    dist = _make_beta_distribution(lower_bound, upper_bound)
    squared_error_lower = FuzzyChecker()._quantile_squared_error(dist, lower_bound, 0.025)
    squared_error_upper = FuzzyChecker()._quantile_squared_error(dist, upper_bound, 0.975)
    assert isinstance(squared_error_lower, float)
    assert isinstance(squared_error_upper, float)


def test_save_diagnostic_output(tmpdir: LocalPath) -> None:
    fuzzy_checker = FuzzyChecker()
    fuzzy_checker.assert_proportion(10_008, 100_000, 0.1)
    fuzzy_checker.save_diagnostic_output(tmpdir)
    assert len(tmpdir.listdir()) == 1

    output = pd.read_csv(tmpdir.listdir()[0])
    assert output.shape == (1, 15)


###########
# Helpers #
###########


def _make_beta_distribution(lower_bound: float, upper_bound: float) -> rv_continuous_frozen:
    concentration_max = 1e40
    concentration_min = 1e-3
    concentration = np.exp((np.log(concentration_max) + np.log(concentration_min)) / 2)
    mean = (upper_bound + lower_bound) / 2
    return scipy.stats.beta(
        a=mean * concentration,
        b=(1 - mean) * concentration,
    )


def test_fuzzy_checker_test_proportion_no_assertion_error() -> None:
    """Tests that FuzzyChecker.test_proportion returns a TestResult without raising an assertion."""

    test_proportion = FuzzyChecker().test_proportion(
        name="test_proportion_no_assertion_error",
        name_additional="unit_test",
        target_proportion=0.9,
        observed_numerator=10_008,
        observed_denominator=100_000,
        bug_issue_beta_distribution_parameters=(0.5, 0.5),
        fail_bayes_factor_cutoff=3.0,
    )
    assert isinstance(test_proportion, TestResult)
    assert test_proportion.reject_null is True


class TestFuzzyCheckerTestProportionVectorized:
    def test_fuzzy_test_proportion_vectorized_pass(
        self,
        simple_demographic_index: pd.MultiIndex,
        observed_proportion_dataframe: pd.DataFrame,
    ) -> None:
        numerator = pd.DataFrame(
            {"value": [10_000, 25_000, 50_000, 75_000]}, index=simple_demographic_index
        )
        denominator = pd.DataFrame(
            {"value": [100_000, 100_000, 100_000, 100_000]}, index=simple_demographic_index
        )
        fuzzy_checker = FuzzyChecker()
        fuzzy_checker.test_proportion_vectorized(
            name="test_proportion_vectorized_passes",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=observed_proportion_dataframe,
        )
        assert all(
            not result.reject_null for result in fuzzy_checker.proportion_test_diagnostics
        )
        assert len(fuzzy_checker.proportion_test_diagnostics) == 21

    def test_fuzzy_test_proportion_vectorized_fail(
        self,
        simple_demographic_index: pd.MultiIndex,
        observed_proportion_dataframe: pd.DataFrame,
    ) -> None:
        # NOTE: first group is twice as high as observered proportion
        numerator = pd.DataFrame(
            {"value": [20_000, 25_000, 50_000, 75_000]}, index=simple_demographic_index
        )
        denominator = pd.DataFrame(
            {"value": [100_000, 100_000, 100_000, 100_000]}, index=simple_demographic_index
        )
        fuzzy_checker = FuzzyChecker()
        fuzzy_checker.test_proportion_vectorized(
            name="test_proportion_vectorized_fails",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=observed_proportion_dataframe,
            fail_bayes_factor_cutoff=3.0,
        )
        assert any(result.reject_null for result in fuzzy_checker.proportion_test_diagnostics)
        assert len(fuzzy_checker.proportion_test_diagnostics) == 21


class SexOnlyTargetIntervalConfig(TargetIntervalConfig):
    """A config that only applies to groups stratified by sex."""

    def applies_to(self, index_info: dict[str, Any]) -> bool:
        return "sex" in index_info


class TestApplyTargetIntervalConfig:
    """Tests for FuzzyChecker._apply_target_interval_config."""

    def test_apply_target_interval_config_applies(self) -> None:
        """When the config applies, _apply_target_interval_config should return a tuple."""
        fuzzy_checker = FuzzyChecker()
        config = TargetIntervalConfig(relative_error=0.1)
        result = fuzzy_checker._apply_target_interval_config(
            target_val=0.5,
            index_info={"age": "Early Neonatal", "year": 2024},
            config=config,
        )
        assert isinstance(result, tuple)
        assert result == (0.45, 0.55)

    def test_apply_target_interval_config_does_not_apply(self) -> None:
        """When the config does not apply, the original float should be returned."""
        fuzzy_checker = FuzzyChecker()
        config = SexOnlyTargetIntervalConfig(relative_error=0.1)
        result = fuzzy_checker._apply_target_interval_config(
            target_val=0.5,
            index_info={"age": "Early Neonatal", "year": 2024},
            config=config,
        )
        assert isinstance(result, float)
        assert result == 0.5

    def test_apply_target_interval_config_clipping(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the interval exceeds [0, 1], values should be clipped and a warning logged."""
        fuzzy_checker = FuzzyChecker()
        config = TargetIntervalConfig(relative_error=0.5)
        # target_val=0.9, relative_error=0.5 -> upper = 0.9 * 1.5 = 1.35, should clip to 1.0
        result = fuzzy_checker._apply_target_interval_config(
            target_val=0.9,
            index_info={"age": "Early Neonatal"},
            config=config,
        )
        assert isinstance(result, tuple)
        assert result == (0.45, 1.0)
        assert "clipped" in caplog.text.lower()

    def test_apply_target_interval_config_none(self) -> None:
        """When config is None, the original target_val should be returned."""
        fuzzy_checker = FuzzyChecker()
        result = fuzzy_checker._apply_target_interval_config(
            target_val=0.5,
            index_info={"sex": "Male", "age": "Early Neonatal"},
            config=None,
        )
        assert isinstance(result, float)
        assert result == 0.5

    @pytest.mark.parametrize("relative_error", [-0.1, 0.0, 1.1])
    def test_relative_error_must_be_in_unit_interval(self, relative_error: float) -> None:
        with pytest.raises(ValueError, match="relative_error must be between"):
            TargetIntervalConfig(relative_error=relative_error)

    def test_config_is_keyword_only(self) -> None:
        """Positional construction must fail cleanly so subclass fields stay unambiguous."""
        with pytest.raises(TypeError, match="positional argument"):
            TargetIntervalConfig(0.1)  # type: ignore[call-arg]


class TestTargetIntervalVectorized:
    """Tests for target interval config integration with test_proportion_vectorized."""

    @pytest.fixture
    def demographic_index(self) -> pd.MultiIndex:
        """A MultiIndex with sex, age, year stratifications."""
        return pd.MultiIndex.from_tuples(
            [
                ("Male", "Early Neonatal", 2024),
                ("Male", "Late Neonatal", 2024),
                ("Female", "Early Neonatal", 2024),
                ("Female", "Late Neonatal", 2024),
                ("Male", "Early Neonatal", 2025),
                ("Male", "Late Neonatal", 2025),
                ("Female", "Early Neonatal", 2025),
                ("Female", "Late Neonatal", 2025),
            ],
            names=["sex", "age", "year"],
        )

    def test_target_interval_no_config(self, simple_demographic_index: pd.MultiIndex) -> None:
        """With no config, behavior should be identical to the default."""
        numerator = pd.DataFrame(
            {"value": [10_000, 25_000, 50_000, 75_000]}, index=simple_demographic_index
        )
        denominator = pd.DataFrame(
            {"value": [100_000, 100_000, 100_000, 100_000]}, index=simple_demographic_index
        )
        target = pd.DataFrame(
            {"value": [0.10, 0.25, 0.50, 0.75]}, index=simple_demographic_index
        )

        fuzzy_checker_no_config = FuzzyChecker()
        fuzzy_checker_no_config.test_proportion_vectorized(
            name="no_config",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=target,
            target_interval_config=None,
        )

        fuzzy_checker_default = FuzzyChecker()
        fuzzy_checker_default.test_proportion_vectorized(
            name="default",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=target,
        )

        assert len(fuzzy_checker_no_config.proportion_test_diagnostics) == len(
            fuzzy_checker_default.proportion_test_diagnostics
        )
        for r1, r2 in zip(
            fuzzy_checker_no_config.proportion_test_diagnostics,
            fuzzy_checker_default.proportion_test_diagnostics,
        ):
            assert r1.reject_null == r2.reject_null

    def test_target_interval_applies_to_every_group(
        self, demographic_index: pd.MultiIndex
    ) -> None:
        """A base config applies the interval to every group, including the overall test."""
        target_val = 0.1
        numerator = pd.DataFrame({"value": [10_000] * 8}, index=demographic_index)
        denominator = pd.DataFrame({"value": [100_000] * 8}, index=demographic_index)
        target = pd.DataFrame({"value": [target_val] * 8}, index=demographic_index)
        config = TargetIntervalConfig(relative_error=0.1)

        fuzzy_checker = FuzzyChecker()
        fuzzy_checker.test_proportion_vectorized(
            name="base_config",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=target,
            target_interval_config=config,
        )

        assert fuzzy_checker.proportion_test_diagnostics != []
        for result in fuzzy_checker.proportion_test_diagnostics:
            assert result.target_lower_bound == pytest.approx(0.09)
            assert result.target_upper_bound == pytest.approx(0.11)

    def test_target_interval_subclass_restricts_groups(
        self, demographic_index: pd.MultiIndex
    ) -> None:
        """A subclass's applies_to hook governs which groups get an interval target."""
        target_val = 0.1
        numerator = pd.DataFrame({"value": [10_000] * 8}, index=demographic_index)
        denominator = pd.DataFrame({"value": [100_000] * 8}, index=demographic_index)
        target = pd.DataFrame({"value": [target_val] * 8}, index=demographic_index)
        config = SexOnlyTargetIntervalConfig(relative_error=0.1)

        fuzzy_checker = FuzzyChecker()
        fuzzy_checker.test_proportion_vectorized(
            name="sex_only",
            observed_numerator=numerator,
            observed_denominator=denominator,
            target_proportion=target,
            target_interval_config=config,
        )

        assert fuzzy_checker.proportion_test_diagnostics != []
        for result in fuzzy_checker.proportion_test_diagnostics:
            strat_names = set(result.index_info.keys()) if result.index_info else set()
            if "sex" in strat_names:
                assert result.target_lower_bound == pytest.approx(0.09)
                assert result.target_upper_bound == pytest.approx(0.11)
            else:
                assert result.target_lower_bound == target_val
                assert result.target_upper_bound == target_val
