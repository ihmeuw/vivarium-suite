from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Collection, Literal

import pandas as pd
from loguru import logger

from vivarium_testing_utils.automated_validation.bundle import RatioMeasureDataBundle
from vivarium_testing_utils.automated_validation.constants import DRAW_INDEX, DataSource
from vivarium_testing_utils.automated_validation.data_transformation.calculations import (
    stratify,
)
from vivarium_testing_utils.automated_validation.data_transformation.measures import Measure
from vivarium_testing_utils.automated_validation.visualization import dataframe_utils
from vivarium_testing_utils.fuzzy_checker import FuzzyChecker, TestResult

StratValue = str | int | float


@dataclass
class TargetIntervalConfig:
    """Configuration for applying a relative error interval to target proportions
    for specific stratification subsets.

    Parameters
    ----------
    stratifications
        A mapping of stratification names to filter values.
        - "all": match groups where this stratification is NOT present
        - "specific": match groups where this stratification IS present (any value)
        - A specific value: match groups where this stratification
          is present with that exact value
        - If multiple stratifications are specified, all conditions must be met for a match.
          Same behavior as an AND filter across the stratifications.
    relative_error
        The relative error to apply to the target proportion, creating an interval
        of (target * (1 - relative_error), target * (1 + relative_error)).
    """

    stratifications: dict[str, StratValue]
    relative_error: float

    def __post_init__(self) -> None:
        if not (0 < self.relative_error <= 1):
            raise ValueError(
                f"relative_error must be between 0 (exclusive) and 1 (inclusive), "
                f"got {self.relative_error}"
            )


class Comparison(ABC):
    """A Comparison is the basic testing unit to compare two datasets, a "test" dataset and a
    "reference" dataset. The test dataset is the one that is being validated, while the reference
    dataset is the one that is used as a benchmark. The comparison operates on a *measure* of the two datasets,
    typically a derived quantity of the test data such as incidence rate or prevalence."""

    test_bundle: RatioMeasureDataBundle
    reference_bundle: RatioMeasureDataBundle
    proportion_test_results: dict[str, Any]

    @property
    def comparison_key(self) -> str:
        """A key to indentify a comparison of the form 'entity_type.entity.measure'."""
        if self.test_bundle.measure.measure_key != self.reference_bundle.measure.measure_key:
            raise ValueError("Test and reference bundle measure keys must be the same.")
        return self.test_bundle.measure.measure_key

    @property
    @abstractmethod
    def metadata(self) -> pd.DataFrame:
        """A summary of the test data and reference data, including:
        - the measure key
        - source
        - index columns
        - size
        - number of draws
        - a sample of the input draws.
        """
        pass

    @abstractmethod
    def get_frame(
        self,
        stratifications: Collection[str] | Literal["all"] = "all",
        num_rows: int | Literal["all"] = "all",
        sort_by: str = "",
        ascending: bool = False,
        aggregate_draws: bool = False,
    ) -> pd.DataFrame:
        """Get a DataFrame of the comparison data, with naive comparison of the test and reference.

        Parameters:
        -----------
        stratifications
            The stratifications to use for the comparison
        num_rows
            The number of rows to return. If "all", return all rows.
        sort_by
            The column to sort by. Default is "percent_error".
        ascending
            Whether to sort in ascending order. Default is False.
        aggregate_draws
            If True, aggregate over draws and seeds to show means and 95% uncertainty intervals.
        Returns:
        --------
        A DataFrame of the comparison data.
        """
        pass

    @abstractmethod
    def verify(
        self,
        step_size: float | None,
        stratifications: Collection[str] | Literal["all"] = "all",
    ) -> None:
        pass

    @property
    def verified(self) -> bool | None:
        """Whether this comparison passes validation.

        Returns None if verification has not been run yet,
        True if all test results pass, False if any fail.
        """
        if "overall" not in self.proportion_test_results:
            return None
        overall = self.proportion_test_results["overall"]
        stratified = self.proportion_test_results.get("stratified", {})
        reject_nulls = [overall.reject_null] + [
            tr.reject_null for group in stratified.values() for tr in group.values()
        ]
        return not any(reject_nulls)

    @property
    def stratification_metadata(self) -> dict[str, Any]:
        """Compile stratification metadata from proportion_test_results.

        Returns a dict with keys 'dimensions', 'values', 'stratification_groups',
        or an empty dict if not yet verified.
        """
        if "overall" not in self.proportion_test_results:
            return {}
        stratified = self.proportion_test_results.get("stratified", {})

        dimensions: set[str] = set()
        values: dict[str, set[str]] = {}
        stratification_groups: list[list[str]] = []

        for strat_key_tuple, group in stratified.items():
            group_dims = list(strat_key_tuple)
            stratification_groups.append(group_dims)
            dimensions.update(group_dims)
            for test_result in group.values():
                if test_result.index_info:
                    for dim, val in test_result.index_info.items():
                        if dim not in values:
                            values[dim] = set()
                        values[dim].add(str(val))

        return {
            "dimensions": sorted(dimensions),
            "values": {dim: sorted(vals) for dim, vals in values.items()},
            "stratification_groups": sorted(
                stratification_groups, key=lambda group: (len(group), group)
            ),
        }


class FuzzyComparison(Comparison):
    """A FuzzyComparison is a comparison that requires statistical hypothesis testing
    to determine if the distributions of the datasets are the same. We require both the numerator and
    denominator for the test data, to be able to calculate the statistical power."""

    def __init__(
        self,
        test_bundle: RatioMeasureDataBundle,
        reference_bundle: RatioMeasureDataBundle,
    ):
        self.test_bundle = test_bundle
        self.reference_bundle = reference_bundle
        if self.test_bundle.measure != self.reference_bundle.measure:
            raise ValueError("Test and reference measures must be the same.")
        self.measure: Measure = self.test_bundle.measure
        self.proportion_test_results: dict[
            str, TestResult | dict[tuple[str, ...], dict[str, TestResult]]
        ] = {
            "stratified": {},
        }
        self._target_interval_configuration: TargetIntervalConfig | None = None

    @property
    def target_interval_configuration(self) -> TargetIntervalConfig | None:
        """The target interval configuration for this comparison."""
        return self._target_interval_configuration

    @target_interval_configuration.setter
    def target_interval_configuration(self, value: TargetIntervalConfig | None) -> None:
        if value is not None and not isinstance(value, TargetIntervalConfig):
            raise TypeError(
                f"target_interval_configuration must be a TargetIntervalConfig or None, "
                f"got {type(value).__name__}"
            )
        self._target_interval_configuration = value

    @property
    def metadata(self) -> pd.DataFrame:
        """A summary of the test data and reference data, including:
        - the measure key
        - source
        - shared index columns
        - source specific index columns
        - size
        - number of draws
        - a sample of the input draws.
        """
        measure_key = self.measure.measure_key
        test_info = self.test_bundle.get_metadata()
        reference_info = self.reference_bundle.get_metadata()
        return dataframe_utils.format_metadata(measure_key, test_info, reference_info)

    def get_frame(
        self,
        stratifications: Collection[str] | Literal["all"] = "all",
        num_rows: int | Literal["all"] = "all",
        sort_by: str = "",
        ascending: bool = False,
        aggregate_draws: bool = False,
    ) -> pd.DataFrame:
        """Get a DataFrame of the comparison data, with naive comparison of the test and reference.

        Parameters:
        -----------
        stratifications
            The stratifications to use for the comparison
        num_rows
            The number of rows to return. If "all", return all rows.
        sort_by
            The column to sort by. Default for non-aggregated data is "percent_error", for aggregation default is to not sort.
        ascending
            Whether to sort in ascending order. Default is False.
        aggregate_draws
            If True, aggregate over draws to show means and 95% uncertainty intervals.
            Changes the output columns to show mean, 2.5%, and 97.5 for each dataset.
        Returns:
        --------
        A DataFrame of the comparison data.
        """

        test_proportion_data, reference_data = self.align_datasets(stratifications)
        # Renaming and aggregating draws happens here instead of _align datasets because
        # "value" and "input_draw" are needed for comparison plots
        test_proportion_data = test_proportion_data.rename(columns={"value": "rate"})
        reference_data = reference_data.rename(columns={"value": "rate"})
        if aggregate_draws:
            test_proportion_data = self._aggregate_over_draws(test_proportion_data)
            reference_data = self._aggregate_over_draws(reference_data)
        test_proportion_data = test_proportion_data.add_prefix("test_")
        reference_data = reference_data.add_prefix("reference_")

        test_proportion_data, reference_data = self._cast_across_indexes(
            test_proportion_data, reference_data
        )
        merged_data = pd.merge(
            test_proportion_data, reference_data, left_index=True, right_index=True
        )

        if not aggregate_draws:
            merged_data["percent_error"] = (
                (merged_data["test_rate"] - merged_data["reference_rate"])
                / merged_data["reference_rate"]
            ) * 100

        if sort_by:
            sort_key = abs if sort_by == "percent_error" else None
            merged_data = merged_data.sort_values(
                by=sort_by,
                key=sort_key,
                ascending=ascending,
            )

        return merged_data if num_rows == "all" else merged_data.head(n=num_rows)

    def _aggregate_over_draws(self, data: pd.DataFrame) -> pd.DataFrame:
        """Aggregate data over draws and seeds, computing mean and 95% uncertainty intervals."""
        # If data doesn't have draws, return data
        if DRAW_INDEX not in data.index.names:
            logger.warning("Data does not have draws. Returning data without aggregating.")
            return data
        # If data only has draws, aggregate and cast single value to a dataframe
        if DRAW_INDEX in data.index.names and len(data.index.names) == 1:
            data = data.describe(percentiles=[0.025, 0.975])
            aggregated_data = data.T
            aggregated_data.index = pd.Index([0], name="index")
        else:
            # Get the levels to group by (everything except draws and seeds)
            group_levels = [level for level in data.index.names if level != DRAW_INDEX]
            # Group by the remaining levels and aggregate
            aggregated_data = data.groupby(group_levels, sort=False, observed=True)[
                "rate"
            ].describe(percentiles=[0.025, 0.975])

        return aggregated_data[["mean", "2.5%", "97.5%"]]

    def verify(
        self,
        step_size: float | None,
        stratifications: Collection[str] | Literal["all"] = "all",
    ) -> None:
        """Verify test and reference data are statistically indistinguishable according to the fuzzy checker."""

        if self.test_bundle.source != DataSource.SIM:
            raise NotImplementedError("Verification is only implemented for SIM test data.")
        if self.reference_bundle.source not in [DataSource.ARTIFACT, DataSource.GBD]:
            raise NotImplementedError(
                "Verification is only implemented for ARTIFACT or GBD reference data."
            )

        fuzzy_checker = FuzzyChecker()
        # Get intersection of stratifications and shared indices
        intersection = self.test_bundle.index_names.intersection(
            self.reference_bundle.index_names
        )
        if stratifications == "all":
            stratify_cols = intersection
        else:
            if not set(stratifications).issubset(intersection):
                raise ValueError("Stratifications must be a subset of the intersection")
            stratify_cols = set(stratifications)

        test_datasets = {
            key: stratify(data, stratify_cols)
            for key, data in self.test_bundle.datasets.items()
        }
        ref_datasets = {
            key: stratify(data, stratify_cols)
            for key, data in self.reference_bundle.datasets.items()
        }
        # Scale rates to the step size of the simulation
        if step_size is None or "population" in self.measure.measure_key:
            target = ref_datasets["data"]
        else:
            target = ref_datasets["data"] / step_size

        fuzzy_checker.test_proportion_vectorized(
            name=self.measure.measure_key,
            observed_numerator=test_datasets["numerator_data"],
            observed_denominator=test_datasets["denominator_data"],
            target_proportion=target,
            target_interval_config=self.target_interval_configuration,
        )
        for result in fuzzy_checker.proportion_test_diagnostics:
            if result.name_additional == "overall":
                self.proportion_test_results["overall"] = result
            else:
                stratified = self.proportion_test_results["stratified"]
                if isinstance(stratified, dict):
                    strat_key = tuple(result.index_info.keys()) if result.index_info else ()
                    if strat_key not in stratified:
                        stratified[strat_key] = {}
                    stratified[strat_key][result.name_additional] = result

    def align_datasets(
        self,
        stratifications: Collection[str] | Literal["all"] = "all",
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Resolve any index mismatches between the test and reference datasets."""

        intersection = self.test_bundle.index_names.intersection(
            self.reference_bundle.index_names
        )
        if stratifications == "all":
            stratifications = intersection
        else:
            if not set(stratifications).issubset(intersection):
                raise ValueError("Stratifications must be a subset of the intersection")

        aggregated_reference_data = self.reference_bundle.get_measure_data(
            stratifications=set(stratifications) | {DRAW_INDEX}
            if DRAW_INDEX in self.reference_bundle.index_names
            else stratifications,
        )
        stratified_test_data = self.test_bundle.get_measure_data(
            stratifications=set(stratifications) | {DRAW_INDEX}
            if DRAW_INDEX in self.test_bundle.index_names
            else stratifications,
        )

        ## At this point, the only non-common index levels should be scenarios and draws.
        return stratified_test_data, aggregated_reference_data

    def _cast_across_indexes(
        self, test_data: pd.DataFrame, reference_data: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Align dataset indexes if stratifications is an empty list
        One dataset might have a single row, so we cast that row to match the other's length
        If both datasets are one row, they will already have the same index."""
        if len(test_data) == 1 and len(reference_data) != 1:
            test_data = pd.concat(
                [test_data] * len(reference_data), ignore_index=True
            ).set_index(reference_data.index)
        elif len(reference_data) == 1 and len(test_data) != 1:
            reference_data = pd.concat(
                [reference_data] * len(test_data), ignore_index=True
            ).set_index(test_data.index)

        return test_data, reference_data
