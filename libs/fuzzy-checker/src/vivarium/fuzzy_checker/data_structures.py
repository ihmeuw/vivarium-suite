"""Data structures used by :class:`vivarium.fuzzy_checker.fuzzy_checker.FuzzyChecker`."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from scipy.stats._distn_infrastructure import rv_discrete_frozen

Confidence = Literal["Conclusive", "Inconclusive", "Did not evaluate"]
"""How much a test result tells you.

"Did not evaluate" is not a weak result but the absence of one: the hypothesis
test never ran, so nothing about the result speaks to whether the simulation is
correct.
"""


# Keyword-only so that subclasses can add fields without the inherited
# relative_error silently claiming a caller's first positional argument.
@dataclass(kw_only=True)
class TargetIntervalConfig:
    """Configuration for applying a relative error interval to target proportions.

    Applies to every tested group. Subclasses can restrict which groups it
    applies to by overriding :meth:`applies_to`.

    Parameters
    ----------
    relative_error
        The relative error to apply to the target proportion, creating an interval
        of (target * (1 - relative_error), target * (1 + relative_error)).
    """

    relative_error: float

    def __post_init__(self) -> None:
        if not (0 < self.relative_error <= 1):
            raise ValueError(
                f"relative_error must be between 0 (exclusive) and 1 (inclusive), "
                f"got {self.relative_error}"
            )

    def applies_to(self, index_info: dict[str, Any]) -> bool:
        """Return whether the interval should be applied to the described group.

        Parameters
        ----------
        index_info
            A mapping of index names to their values for the group under test.
            Empty for the population-level test.
        """
        return True


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
    confidence: Confidence = "Conclusive"
    """Whether the test result is conclusive or inconclusive based on sample size and Bayes factor."""
    lower_bound_bayes_factor: float | None = None
    """Bayes factor at numerator=0, used to check if sample size is too small for lower bound detection."""
    upper_bound_bayes_factor: float | None = None
    """Bayes factor at numerator=denominator, used to check if sample size is too small for upper bound detection."""

    @property
    def evaluated(self) -> bool:
        """Whether the test produced a usable Bayes factor.

        A nan Bayes factor means the test never ran, and every comparison against nan
        is False, so ``reject_null`` comes back False without having been decided.
        Callers must check this before reading a result as a pass.
        """
        return not math.isnan(self.bayes_factor)

    @property
    def comparison_to_target(self) -> str:
        """Describe whether the observed proportion is below, above, or aligned with target."""
        if not self.evaluated:
            return "Did not evaluate"

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
            "evaluated": self.evaluated,
            "comparison_to_target": self.comparison_to_target,
            "confidence": self.confidence,
        }
        if self.index_info is not None:
            results_dict["index_info"] = self.index_info
        return results_dict
