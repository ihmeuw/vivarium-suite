"""Tests for fuzzy checking of continuous means."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

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


@pytest.mark.parametrize("method", ["conjugate", "fractional"])
@pytest.mark.parametrize("ratio", SD_TO_MEAN_RATIOS)
def test_correct_data_passes(
    ratio: float, method: Literal["conjugate", "fractional"]
) -> None:
    """A simulation matching its target must pass at any SD-to-mean ratio."""
    values = _values(TARGET_MEAN, ratio * TARGET_MEAN, 1_000, seed=12345)
    result = FuzzyChecker().test_mean(
        name="correct", target_mean=TARGET_MEAN, observed_values=values, method=method
    )
    assert not result.reject_null
    assert result.bayes_factor < 1.0


@pytest.mark.parametrize("method", ["conjugate", "fractional"])
@pytest.mark.parametrize("ratio", SD_TO_MEAN_RATIOS)
def test_one_sd_bias_is_caught(
    ratio: float, method: Literal["conjugate", "fractional"]
) -> None:
    """A one-SD bias at n=100 is decisive at any SD-to-mean ratio."""
    sd = ratio * TARGET_MEAN
    values = _values(TARGET_MEAN + sd, sd, 100, seed=12345)
    result = FuzzyChecker().test_mean(
        name="biased", target_mean=TARGET_MEAN, observed_values=values, method=method
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


@pytest.mark.parametrize("method", ["conjugate", "fractional"])
@pytest.mark.parametrize("target", [0.0, -5.0])
def test_targets_at_or_below_zero(
    target: float, method: Literal["conjugate", "fractional"]
) -> None:
    """Zero and negative targets are in-domain: correct passes, biased rejects."""
    checker = FuzzyChecker()
    correct = _values(target, 1.0, 1_000, seed=12345)
    assert not checker.test_mean(
        name="correct", target_mean=target, observed_values=correct, method=method
    ).reject_null

    biased = checker.test_mean(
        name="biased", target_mean=target, observed_values=correct + 0.5, method=method
    )
    assert biased.reject_null
    assert biased.comparison_to_target == "Overestimated"


def test_fractional_interval_target_matches_point_in_the_limit() -> None:
    """A vanishingly narrow target interval reproduces the point-target Bayes factor."""
    values = _values(TARGET_MEAN, 15.0, 500, seed=12345)
    checker = FuzzyChecker()
    point = checker.test_mean(
        name="point", target_mean=TARGET_MEAN, observed_values=values, method="fractional"
    )
    narrow = checker.test_mean(
        name="narrow",
        target_mean=(TARGET_MEAN - 1e-4, TARGET_MEAN + 1e-4),
        observed_values=values,
        method="fractional",
    )
    assert narrow.bayes_factor == pytest.approx(point.bayes_factor, rel=1e-4)


def test_fractional_rejects_conjugate_hyperparameters() -> None:
    """The fractional method has no hyperparameters, so passing them is an error."""
    values = _values(TARGET_MEAN, 15.0, 100, seed=12345)
    checker = FuzzyChecker()
    with pytest.raises(ValueError, match="no prior hyperparameters"):
        checker.test_mean(
            name="x",
            target_mean=TARGET_MEAN,
            observed_values=values,
            method="fractional",
            beta_prior=1.0,
        )
    with pytest.raises(ValueError, match="no prior hyperparameters"):
        checker.test_mean(
            name="x",
            target_mean=TARGET_MEAN,
            observed_values=values,
            method="fractional",
            bug_issue_distribution_mean_uncertainty_interval=(0.0, 200.0),
        )


def test_fractional_input_validation() -> None:
    """Reject out-of-range training fractions, spreadless data, and unknown methods."""
    values = _values(TARGET_MEAN, 15.0, 100, seed=12345)
    checker = FuzzyChecker()
    for bad_fraction in [1.0 / 200, 1.0, 1.5]:
        with pytest.raises(ValueError, match="training_fraction"):
            checker.test_mean(
                name="x",
                target_mean=TARGET_MEAN,
                observed_values=values,
                method="fractional",
                training_fraction=bad_fraction,
            )
    with pytest.raises(ValueError, match="distinct observed values"):
        checker.test_mean(
            name="x",
            target_mean=TARGET_MEAN,
            observed_values=[5.0, 5.0, 5.0],
            method="fractional",
        )
    with pytest.raises(ValueError, match="Unknown method"):
        checker.test_mean(
            name="x", target_mean=TARGET_MEAN, observed_values=values, method="nonsense"  # type: ignore[arg-type]
        )


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
    # The filename carries the xdist worker id when tests run in parallel.
    diagnostics_files = list(tmp_path.glob("mean_test_diagnostics*.csv"))
    assert len(diagnostics_files) == 1
    assert "hemoglobin" in diagnostics_files[0].read_text()


# --- Tests adapted from vivarium_testing_utils#79 (zmbc) ---------------------
#
# The original API took a user-supplied standard_deviation_estimate; here that
# maps onto the conjugate method's variance prior, beta_prior = (alpha - 1) *
# estimate**2, so the estimate-relative-error dimension exercises exactly the
# mis-estimate robustness the original formulation could not achieve. The
# fractional method has no such parameter, so it runs the same scenarios
# without that dimension.

ZEB_SHIFTS = [
    -10_000_000,
    -1_000_000,
    -1_000,
    -100,
    -10,
    -0.1,
    0,
    0.1,
    10,
    100,
    1_000,
    1_000_000,
    10_000_000,
]

METHOD_AND_SD_ESTIMATE_ERROR = [
    ("conjugate", 0.1),
    ("conjugate", 1.0),
    ("conjugate", 10.0),
    ("fractional", 1.0),
]


def _zeb_seed(*args: object) -> int:
    """Reproduce the original tests' parameter-hashed seeds."""
    import hashlib
    import json

    return int.from_bytes(hashlib.sha256(json.dumps(args).encode()).digest(), "big")


def _sd_estimate_kwargs(
    method: str, actual_sd: float, relative_error: float
) -> dict[str, object]:
    """Translate the original standard_deviation_estimate argument to the new API."""
    if method == "fractional":
        return {"method": "fractional"}
    # alpha_prior defaults to 2, so (alpha - 1) is 1.
    return {"beta_prior": (actual_sd * relative_error) ** 2}


def _shifted_target(
    target_mean: float | tuple[float, float], shift: float
) -> float | tuple[float, float]:
    """Translate a target by the scenario's shift."""
    if isinstance(target_mean, tuple):
        return (target_mean[0] + shift, target_mean[1] + shift)
    return target_mean + shift


@pytest.mark.parametrize("method, sd_estimate_relative_error", METHOD_AND_SD_ESTIMATE_ERROR)
@pytest.mark.parametrize("shift", ZEB_SHIFTS)
@pytest.mark.parametrize(
    "actual_mean, actual_sd, n, target_mean",
    [
        (0, 0.5, 5, 0),
        (0, 50_000, 50, 0),
        (9, 100, 5_000, (-10, 10)),
        (9, 100, 500_000, (-10, 10)),
        # Would fail, but the SD is too big for the sample to show it.
        (-20, 10_000, 500_000, (-10, 10)),
        # Would fail, but the sample size is too small to show it.
        (-20, 100, 100, (-10, 10)),
    ],
)
def test_pass_assert_mean(
    shift: float,
    actual_mean: float,
    actual_sd: float,
    n: int,
    target_mean: float | tuple[float, float],
    method: str,
    sd_estimate_relative_error: float,
) -> None:
    """Data consistent with its target must not fail, at any translation."""
    seed = _zeb_seed(shift, actual_mean, actual_sd, n, target_mean)
    rng = np.random.default_rng(seed)
    observed_values = rng.normal(actual_mean + shift, actual_sd, size=n)
    FuzzyChecker().assert_mean(
        observed_values,
        target_mean=_shifted_target(target_mean, shift),
        name="pass",
        **_sd_estimate_kwargs(method, actual_sd, sd_estimate_relative_error),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("method, sd_estimate_relative_error", METHOD_AND_SD_ESTIMATE_ERROR)
@pytest.mark.parametrize("shift", ZEB_SHIFTS)
@pytest.mark.parametrize(
    "actual_mean, actual_sd, n, target_mean, match",
    [
        (-30, 100, 3_000, (-10, 10), "is significantly less than expected"),
        (30, 100, 3_000, (-10, 10), "is significantly greater than expected"),
        (-25, 100, 100_000, (-10, 10), "is significantly less than expected"),
        (25, 100, 100_000, (-10, 10), "is significantly greater than expected"),
        # Test extreme precision with large numbers.
        pytest.param(
            5,
            100,
            10_000_000,
            0,
            "is significantly greater than expected",
            marks=pytest.mark.slow,
        ),
    ],
)
def test_fail_assert_mean(
    shift: float,
    actual_mean: float,
    actual_sd: float,
    n: int,
    target_mean: float | tuple[float, float],
    match: str,
    method: str,
    sd_estimate_relative_error: float,
) -> None:
    """Biased data must fail with the right direction, at any translation."""
    seed = _zeb_seed(shift, actual_mean, actual_sd, n, target_mean)
    rng = np.random.default_rng(seed)
    observed_values = rng.normal(actual_mean + shift, actual_sd, size=n)
    with pytest.raises(AssertionError, match=match):
        FuzzyChecker().assert_mean(
            observed_values,
            target_mean=_shifted_target(target_mean, shift),
            name="fail",
            **_sd_estimate_kwargs(method, actual_sd, sd_estimate_relative_error),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("method, sd_estimate_relative_error", METHOD_AND_SD_ESTIMATE_ERROR)
@pytest.mark.parametrize("shift", [0.0, 1_000_000.0])
@pytest.mark.parametrize("actual_sd", [1.0, 100.0])
def test_pass_then_inconclusive_then_fail(
    shift: float,
    actual_sd: float,
    method: str,
    sd_estimate_relative_error: float,
) -> None:
    """Growing bias moves the verdict pass -> inconclusive -> fail, in order.

    Bounded adaptation of the original's walking loop: scan biases on a fixed
    grid of standard errors and require all three verdicts to appear, in order,
    with no regression.
    """
    n = 10_000
    seed = _zeb_seed(shift, actual_sd, n, sd_estimate_relative_error)
    values = np.random.default_rng(seed).normal(shift, actual_sd, size=n)
    standard_error = actual_sd / np.sqrt(n)
    checker = FuzzyChecker()

    statuses = []
    for bias_in_standard_errors in np.linspace(0.0, 15.0, 61):
        result = checker.test_mean(
            name="walk",
            target_mean=shift,
            observed_values=values + bias_in_standard_errors * standard_error,
            **_sd_estimate_kwargs(method, actual_sd, sd_estimate_relative_error),  # type: ignore[arg-type]
        )
        if result.bayes_factor > 100:
            statuses.append("fail")
        elif result.bayes_factor > 0.1:
            statuses.append("inconclusive")
        else:
            statuses.append("pass")

    assert set(statuses) == {"pass", "inconclusive", "fail"}
    severity = [{"pass": 0, "inconclusive": 1, "fail": 2}[status] for status in statuses]
    assert severity == sorted(severity)
