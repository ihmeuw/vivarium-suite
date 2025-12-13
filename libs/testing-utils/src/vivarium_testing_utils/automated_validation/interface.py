from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Literal, Mapping

import pandas as pd
import yaml
from IPython.display import HTML, display
from matplotlib.figure import Figure
from vivarium_inputs import utilities as vi

from vivarium_testing_utils.automated_validation.bundle import RatioMeasureDataBundle
from vivarium_testing_utils.automated_validation.comparison import Comparison, FuzzyComparison
from vivarium_testing_utils.automated_validation.data_loader import DataLoader, DataSource
from vivarium_testing_utils.automated_validation.data_transformation.calculations import (
    filter_data,
)
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    CategoricalRelativeRisk,
    Measure,
    MeasureMapper,
    RatioMeasure,
)
from vivarium_testing_utils.automated_validation.data_transformation.utils import (
    add_comparison_metadata_levels,
    drop_extra_columns,
    get_measure_index_names,
    set_gbd_index,
    set_validation_index,
)
from vivarium_testing_utils.automated_validation.visualization import plot_utils


class ValidationContext:
    def __init__(self, results_dir: str | Path, scenario_columns: Collection[str] = ()):
        self.results_dir = Path(results_dir)
        self.data_loader = DataLoader(self.results_dir)
        self.comparisons: dict[str, Comparison] = {}
        self.age_groups = self._get_age_groups()
        self.scenario_columns = scenario_columns
        self.location = self.data_loader.location
        self.measure_mapper = MeasureMapper()

    def get_sim_outputs(self) -> list[str]:
        """Get a list of the datasets available in the given simulation output directory."""
        return sorted(self.data_loader.get_sim_outputs())

    def get_artifact_keys(self) -> list[str]:
        """Get a list of the artifact keys available to compare against."""
        return sorted(self.data_loader.get_artifact_keys())

    def get_raw_data(self, data_key: str, source: str) -> Any:
        """Return a copy of the data for manual inspection."""
        return self.data_loader.get_data(data_key, DataSource.from_str(source))

    def upload_custom_data(
        self, data_key: str, data: pd.DataFrame | pd.Series[float]
    ) -> None:
        """Upload a custom DataFrame or Series to the context given by a data key."""
        if isinstance(data, pd.Series):
            data = data.to_frame(name="value")
        self.data_loader.upload_custom_data(data_key, data)

    def add_comparison(
        self,
        measure_key: str,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
    ) -> None:
        """Add a comparison to the context given a measure key and data sources."""
        if measure_key.endswith(".relative_risk"):
            raise ValueError(
                f"For relative risk measures, use 'add_relative_risk_comparison' instead. "
                f"Got measure_key='{measure_key}'"
            )

        measure = self.measure_mapper.get_measure_from_key(
            measure_key, list(self.scenario_columns)
        )
        self._add_comparison_with_measure(
            measure,
            test_source,
            ref_source,
            test_scenarios,
            ref_scenarios,
        )

    def add_relative_risk_comparison(
        self,
        risk_factor: str,
        affected_entity: str,
        affected_measure: str,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
        risk_stratification_column: str | None = None,
        risk_state_mapping: dict[str, str] | None = None,
    ) -> None:
        """Add a relative risk comparison to the context.

        Parameters
        ----------
        risk_factor
            The risk factor name (e.g., 'child_stunting')
        affected_entity
            The entity affected by the risk factor (e.g., 'cause.diarrheal_diseases')
        affected_measure
            The measure to calculate (e.g., 'excess_mortality_rate', 'incidence_rate')
        risk_stratification_column
            The column to use for stratifying the risk factor in simulation data (e.g., 'risk_factor')
        test_source
            Source for test data ('sim', 'artifact', or 'custom')
        ref_source
            Source for reference data ('sim', 'artifact', or 'custom')
        test_scenarios
            Dictionary of scenario filters for test data
        ref_scenarios
            Dictionary of scenario filters for reference data
        """

        measure = CategoricalRelativeRisk(
            risk_factor,
            affected_entity,
            affected_measure,
            risk_stratification_column,
            risk_state_mapping,
        )
        self._add_comparison_with_measure(
            measure, test_source, ref_source, test_scenarios, ref_scenarios
        )

    def _add_comparison_with_measure(
        self,
        measure: Measure,
        test_source: str,
        ref_source: str,
        test_scenarios: dict[str, str] = {},
        ref_scenarios: dict[str, str] = {},
    ) -> None:
        """Internal method to add a comparison with a pre-constructed measure."""

        test_source_enum = DataSource.from_str(test_source)
        ref_source_enum = DataSource.from_str(ref_source)

        # Check if the measure is a RatioMeasure for FuzzyComparison
        if not isinstance(measure, RatioMeasure):
            raise NotImplementedError(
                f"Measure {measure.measure_key} is not a RatioMeasure. Only RatioMeasures are currently supported for comparisons."
            )

        for source, scenarios in (
            (test_source_enum, test_scenarios),
            (ref_source_enum, ref_scenarios),
        ):
            if source == DataSource.SIM and set(scenarios.keys()) != set(
                self.scenario_columns
            ):
                raise ValueError(
                    f"Each simulation comparison subject must choose a specific scenario. "
                    f"You are missing scenarios for: {set(self.scenario_columns) - set(scenarios.keys())}."
                )

        test_data_bundle = RatioMeasureDataBundle(
            measure=measure,
            source=test_source_enum,
            data_loader=self.data_loader,
            age_group_df=self.age_groups,
            scenarios=test_scenarios,
        )
        ref_data_bundle = RatioMeasureDataBundle(
            measure=measure,
            source=ref_source_enum,
            data_loader=self.data_loader,
            age_group_df=self.age_groups,
            scenarios=ref_scenarios,
        )

        comparison = FuzzyComparison(
            test_bundle=test_data_bundle, reference_bundle=ref_data_bundle
        )
        self.comparisons[measure.measure_key] = comparison

    def verify(self, comparison_key: str, stratifications: Collection[str] = ()):  # type: ignore[no-untyped-def]
        self.comparisons[comparison_key].verify(stratifications)

    def metadata(self, comparison_key: str) -> pd.DataFrame:
        comparison_metadata = self.comparisons[comparison_key].metadata
        directory_metadata = self._get_directory_metadata()

        data = pd.concat([comparison_metadata, directory_metadata])
        # Display draw values on multiple lines if necessary
        display_df = data.copy()
        display_df["Test Data"] = display_df["Test Data"].str.wrap(30, break_long_words=False)
        display_df["Reference Data"] = display_df["Reference Data"].str.wrap(
            30, break_long_words=False
        )

        display(HTML(display_df.to_html().replace("\\n", "<br>")))  # type: ignore[no-untyped-call]
        return data

    def _get_directory_metadata(self) -> pd.DataFrame:
        """Add model run metadata to the dictionary."""
        sim_run_time = self.results_dir.name
        sim_dt = datetime.strptime(sim_run_time, "%Y_%m_%d_%H_%M_%S").strftime(
            "%b %d %H:%M %Y"
        )
        artifact_run_time = self._get_artifact_creation_time()
        directory_metadata = pd.DataFrame(
            {
                "Property": ["Run Time"],
                "Test Data": [sim_dt],
                "Reference Data": [artifact_run_time],
            }
        )

        return directory_metadata.set_index("Property")

    def _get_artifact_creation_time(self) -> str:
        """Get the artifact creation time from the artifact file."""
        artifact_path = Path(
            yaml.safe_load((self.results_dir / "model_specification.yaml").open("r"))[
                "configuration"
            ]["input_data"]["artifact_path"]
        )
        os_time = os.path.getmtime(artifact_path)
        artifact_time = datetime.fromtimestamp(os_time).strftime("%b %d %H:%M %Y")

        return artifact_time

    def get_frame(
        self,
        comparison_key: str,
        stratifications: Collection[str] | Literal["all"] = "all",
        num_rows: int | Literal["all"] = "all",
        sort_by: str = "",
        filters: Mapping[str, str | list[str]] | None = None,
        ascending: bool = False,
        aggregate_draws: bool = False,
    ) -> pd.DataFrame:
        """Get a DataFrame of the comparison data, with naive comparison of the test and reference.

        Parameters:
        -----------
        comparison_key
            The key of the comparison for which to get the data
        stratifications
            The stratifications to use for the comparison. If "all", no aggregation will happen and
            all existing stratifications will remain. If an empty list is passed, no stratifications
            will be retained.
        num_rows
            The number of rows to return. If "all", return all rows.
        filters
            A mapping of index levels to filter values. Only rows matching the filter will be included.
        sort_by
            The column to sort by. Default is "percent_error" for non-aggregated data, and no sorting for aggregated data.
        ascending
            Whether to sort in ascending order. Default is False.
        aggregate_draws
            If True, aggregate over draws to show means and 95% uncertainty intervals.

        Returns:
        --------
        A DataFrame of the comparison data.
        """
        if not aggregate_draws and not sort_by:
            sort_by = "percent_error"

        if (isinstance(num_rows, int) and num_rows > 0) or num_rows == "all":
            data = self.comparisons[comparison_key].get_frame(
                stratifications, num_rows, sort_by, ascending, aggregate_draws
            )
            data = self.format_ui_data_index(data, comparison_key)
            return (
                filter_data(data, filters, drop_singles=False)
                if filters is not None
                else data
            )
        else:
            raise ValueError("num_rows must be a positive integer or literal 'all'")

    def plot_comparison(
        self,
        comparison_key: str,
        type: str,
        condition: dict[str, Any] = {},
        stratifications: Collection[str] | Literal["all"] = "all",
        **kwargs: Any,
    ) -> Figure | list[Figure]:
        """Create a plot for the given comparison.

        Parameters
        ----------
        comparison_key
            The comparison object to plot.
        type
            Type of plot to create.
        condition
            Conditions to filter the data by, by default {}
        stratifications
            Stratifications to retain in the plotted dataset, by default "all"
        **kwargs
            Additional keyword arguments for specific plot types.

        Returns
        -------
            The generated figure or list of figures.
        """
        return plot_utils.plot_comparison(
            self.comparisons[comparison_key], type, condition, stratifications, **kwargs
        )

    def generate_comparisons(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def verify_all(self):  # type: ignore[no-untyped-def]
        for comparison in self.comparisons.values():
            comparison.verify()

    def plot_all(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_results(self, verbose: bool = False):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    # TODO MIC-6047 Let user pass in custom age groups
    def _get_age_groups(self) -> pd.DataFrame:
        """Get the age groups from the given DataFrame or from the artifact."""
        from vivarium.framework.artifact.artifact import ArtifactException

        try:
            age_groups: pd.DataFrame = self.data_loader.get_data(
                "population.age_bins", DataSource.ARTIFACT
            )
        # If we can't find the age groups in the artifact, get them directly from vivarium inputs
        except ArtifactException:
            from vivarium_inputs import get_age_bins

            age_groups = get_age_bins()

        # mypy wants this to do type narrowing
        if age_groups is None:
            raise ValueError(
                "No age groups found. Please provide a DataFrame or use the artifact."
            )
            # relabel index level age_group_name to age_group

        return age_groups.rename_axis(index={"age_group_name": "age_group"})

    def cache_gbd_data(
        self,
        data_key: str,
        data: pd.DataFrame | dict[str, str] | str,
        overwrite: bool = False,
    ) -> None:
        """Upload the output of a get_draws call to the context given by a data key."""
        formatted_data: pd.DataFrame | dict[str, str] | str
        if isinstance(data, pd.DataFrame):
            formatted_data = self._format_to_vivarium_inputs_conventions(data, data_key)
            formatted_data = set_validation_index(formatted_data)
        else:
            formatted_data = data
        self.data_loader.cache_gbd_data(data_key, formatted_data, overwrite=overwrite)

    def _format_to_vivarium_inputs_conventions(
        self, data: pd.DataFrame, data_key: str
    ) -> pd.DataFrame:
        """Format the output of a get_draws call to data schema conventions for the validation context."""
        if "relative_risk" in data_key:
            data = vi.get_affected_measure_column(data)
        data = drop_extra_columns(data, data_key)
        data = set_gbd_index(data, data_key=data_key)
        data = vi.scrub_gbd_conventions(data, self.location)
        data = vi.split_interval(data, interval_column="age", split_column_prefix="age")
        data = vi.split_interval(data, interval_column="year", split_column_prefix="year")
        formatted_data: pd.DataFrame = vi.sort_hierarchical_data(data)
        return formatted_data

    @staticmethod
    def format_ui_data_index(data: pd.DataFrame, comparison_key: str) -> pd.DataFrame:
        """Format and sort the data for UI display.

        Parameters
        ----------
        data
            The DataFrame to sort.
        comparison_key
            The comparison key for logging purposes.

        Returns
        -------
            The sorted DataFrame.
        """

        expected_order = get_measure_index_names(comparison_key, "vivarium")
        ordered_cols = [col for col in expected_order if col in data.index.names]
        extra_idx_cols = [col for col in data.index.names if col not in ordered_cols]
        sorted_index = ordered_cols + extra_idx_cols
        sorted = data.reorder_levels(sorted_index).sort_index()
        return add_comparison_metadata_levels(sorted, comparison_key)

    def add_new_measure(self, measure_key: str, measure_class: type[Measure]) -> None:
        """Add a new measure class to the context's measure mapper.

        Parameters
        ----------
        measure_key
            The measure key in format 'entity_type.entity.measure_key' or 'entity_type.measure_key'.
        measure_class
            The class implementing the measure.
        """

        self.measure_mapper.add_new_measure(measure_key, measure_class)
