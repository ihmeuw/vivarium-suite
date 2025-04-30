from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
import scipy
from _pytest.logging import LogCaptureFixture
from scipy.stats._distn_infrastructure import rv_continuous_frozen

if TYPE_CHECKING:
    from py._path.local import LocalPath

from vivarium_testing_utils.fuzzy_checker import FuzzyChecker

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
def test_pass_fuzzy_assert_proportion(
    numerator: int, denominator: int, target_proportion: float
) -> None:
    FuzzyChecker().fuzzy_assert_proportion(numerator, denominator, target_proportion)


@pytest.mark.parametrize(
    "numerator, denominator, target_proportion, match",
    [
        (901, 100_000, 0.05, "is significantly less than expected"),
        (1_150, 50_000, 0.02, "is significantly greater than expected"),
    ],
)
def test_fail_fuzzy_assert_proportion(
    numerator: int, denominator: int, target_proportion: float, match: str
) -> None:
    with pytest.raises(AssertionError, match=match):
        FuzzyChecker().fuzzy_assert_proportion(numerator, denominator, target_proportion)


def test_small_sample_size_fuzzy_assert_proportion(caplog: LogCaptureFixture) -> None:
    FuzzyChecker().fuzzy_assert_proportion(1, 10, 0.1)
    assert "Sample size too small" in caplog.text


def test_not_conclusive_fuzzy_assert_proportion(caplog: LogCaptureFixture) -> None:
    """This test verifies we will pass, then be inconclusive, then fail.
    The numbers used in this test are arbitrary but are intended to be conservative
    estimates of the number of iterations needed to reach each state
    Creating an instance here allows us to cache some of the computation for the while loop
    """
    fuzzy_checker = FuzzyChecker()
    numerator = 1_000
    while True:
        caplog.clear()
        fuzzy_checker.fuzzy_assert_proportion(numerator, 10_000, 0.1)
        if "is not conclusive" in caplog.text:
            assert numerator > 1050
            break
        if numerator > 1_200:
            raise RuntimeError("Test did not reach the expected warning")
        numerator += 1

    while True:
        caplog.clear()
        try:
            fuzzy_checker.fuzzy_assert_proportion(numerator, 10_000, 0.1)
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
    fuzzy_checker.fuzzy_assert_proportion(10_008, 100_000, 0.1)
    fuzzy_checker.save_diagnostic_output(tmpdir)
    assert len(tmpdir.listdir()) == 1

    output = pd.read_csv(tmpdir.listdir()[0])
    assert output.shape == (1, 9)


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
