"""Tests for fuzzy checking of continuous means."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vivarium.fuzzy_checker import FuzzyChecker
from vivarium.fuzzy_checker.fuzzy_checker import _BUG_MEAN_PSEUDO_OBSERVATIONS

TARGET_MEAN = 100.0
# The power of the test must not depend on how the data's spread compares to
# its mean; every ratio here silently defeated the test before the priors were
# derived from the observed spread.
SD_TO_MEAN_RATIOS = [0.001, 0.01, 0.1, 1.0, 10.0]


def _values(mean: float, sd: float, n: int, seed: int) -> np.ndarray:
    """Draw a reproducible normal sample."""
    return np.random.default_rng(seed).normal(mean, sd, size=n)


@pytest.mark.parametrize("ratio", SD_TO_MEAN_RATIOS)
def test_correct_data_passes(ratio: float) -> None:
    """A simulation matching its target must pass at any SD-to-mean ratio."""
    values = _values(TARGET_MEAN, ratio * TARGET_MEAN, 1_000, seed=12345)
    result = FuzzyChecker().test_mean(
        name="correct", target_mean=TARGET_MEAN, observed_values=values
    )
    assert not result.reject_null
    assert result.bayes_factor < 1.0


@pytest.mark.parametrize("ratio", SD_TO_MEAN_RATIOS)
def test_one_sd_bias_is_caught(ratio: float) -> None:
    """A one-SD bias at n=100 is decisive at any SD-to-mean ratio."""
    sd = ratio * TARGET_MEAN
    values = _values(TARGET_MEAN + sd, sd, 100, seed=12345)
    result = FuzzyChecker().test_mean(
        name="biased", target_mean=TARGET_MEAN, observed_values=values
    )
    assert result.reject_null


@pytest.mark.parametrize(
    "shift, direction",
    [(-1.0, "less"), (1.0, "greater")],
)
def test_assert_mean_reports_direction(shift: float, direction: str) -> None:
    """A decisive failure names which side of the target the data fell on."""
    values = _values(TARGET_MEAN + shift, 1.0, 1_000, seed=12345)
    with pytest.raises(AssertionError, match=f"significantly {direction} than expected"):
        FuzzyChecker().assert_mean(values, target_mean=TARGET_MEAN, name="biased")


@pytest.mark.parametrize("target", [0.0, -5.0])
def test_targets_at_or_below_zero(target: float) -> None:
    """Zero and negative targets are in-domain: correct passes, biased rejects."""
    checker = FuzzyChecker()
    correct = _values(target, 1.0, 1_000, seed=12345)
    assert not checker.test_mean(
        name="correct", target_mean=target, observed_values=correct
    ).reject_null

    biased = checker.test_mean(
        name="biased", target_mean=target, observed_values=correct + 0.5
    )
    assert biased.reject_null
    assert biased.comparison_to_target == "Overestimated"


def test_moments_match_values() -> None:
    """Supplying moments gives the same result as supplying the values."""
    values = _values(TARGET_MEAN, 15.0, 1_000, seed=12345)
    from_values = FuzzyChecker().test_mean(
        name="values", target_mean=TARGET_MEAN, observed_values=values
    )
    from_moments = FuzzyChecker().test_mean(
        name="moments",
        target_mean=TARGET_MEAN,
        observed_zeroth_moment=len(values),
        observed_first_moment=float(np.sum(values)),
        observed_second_moment=float(np.sum(values**2)),
    )
    assert from_moments.bayes_factor == pytest.approx(from_values.bayes_factor, rel=1e-12)
    assert from_moments.observed_mean == pytest.approx(from_values.observed_mean)
    assert from_moments.observed_std == pytest.approx(from_values.observed_std)


def test_interval_target() -> None:
    """A target uncertainty interval passes data inside it and rejects data far outside."""
    checker = FuzzyChecker()
    inside = _values(101.0, 15.0, 1_000, seed=12345)
    assert not checker.test_mean(
        name="inside", target_mean=(98.0, 102.0), observed_values=inside
    ).reject_null

    outside = _values(90.0, 15.0, 1_000, seed=12345)
    assert checker.test_mean(
        name="outside", target_mean=(98.0, 102.0), observed_values=outside
    ).reject_null


def test_input_validation() -> None:
    """Reject inverted target bounds and over- or under-specified observations."""
    checker = FuzzyChecker()
    values = [1.0, 2.0, 3.0]

    with pytest.raises(AssertionError, match="cannot be greater than the upper bound"):
        checker.test_mean(name="x", target_mean=(2.0, 1.0), observed_values=values)

    with pytest.raises(AssertionError):
        checker.test_mean(name="x", target_mean=2.0)  # no observations at all

    with pytest.raises(AssertionError):
        checker.test_mean(  # both forms at once
            name="x",
            target_mean=2.0,
            observed_values=values,
            observed_zeroth_moment=3,
            observed_first_moment=6.0,
            observed_second_moment=14.0,
        )

    with pytest.raises(AssertionError):
        checker.test_mean(  # incomplete moments
            name="x", target_mean=2.0, observed_zeroth_moment=3, observed_first_moment=6.0
        )


@pytest.mark.parametrize("alpha_prior", [2.0, 3.0, 5.0])
def test_default_bug_interval_encodes_named_prior_weight(alpha_prior: float) -> None:
    """The default bug-mean interval round-trips to the named pseudo-observation weight."""
    import scipy.stats

    checker = FuzzyChecker()
    beta_prior = (alpha_prior - 1) * 15.0**2
    half_width = scipy.stats.t.ppf(0.975, df=2 * alpha_prior) * float(
        np.sqrt(beta_prior / (alpha_prior * _BUG_MEAN_PSEUDO_OBSERVATIONS))
    )
    _, lambda0 = checker._compute_parameters_for_marginal_mu_interval(
        TARGET_MEAN - half_width, TARGET_MEAN + half_width, alpha_prior, beta_prior
    )
    assert lambda0 == pytest.approx(_BUG_MEAN_PSEUDO_OBSERVATIONS)


def test_mean_diagnostics_saved(tmp_path: Path) -> None:
    """Passing mean tests accumulate in diagnostics and write to their own file."""
    checker = FuzzyChecker()
    values = _values(TARGET_MEAN, 15.0, 1_000, seed=12345)
    checker.assert_mean(values, target_mean=TARGET_MEAN, name="hemoglobin")
    assert len(checker.mean_test_diagnostics) == 1
    assert checker.mean_test_diagnostics[0].name == "hemoglobin"

    checker.save_diagnostic_output(tmp_path)
    diagnostics_file = tmp_path / "mean_test_diagnostics.csv"
    assert diagnostics_file.exists()
    assert "hemoglobin" in diagnostics_file.read_text()
