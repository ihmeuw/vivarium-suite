from abc import ABC
from collections.abc import Collection
from typing import Any, Literal

import pandas as pd

from vivarium_testing_utils.automated_validation.constants import (
    DRAW_INDEX,
    SEED_INDEX,
    DataSource,
)
from vivarium_testing_utils.automated_validation.data_loader import DataLoader
from vivarium_testing_utils.automated_validation.data_transformation import (
    age_groups,
    calculations,
)
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    CategoricalRelativeRisk,
    Measure,
    RatioMeasure,
    RiskExposure,
)
from vivarium_testing_utils.automated_validation.visualization import dataframe_utils


class MeasureDataBundle(ABC):
    measure: Measure
    source: DataSource
    data_loader: DataLoader
    scenarios: dict[str, str] | None


class RatioMeasureDataBundle:
    def __init__(
        self,
        measure: RatioMeasure,
        source: DataSource,
        data_loader: DataLoader,
        age_group_df: pd.DataFrame,
        scenarios: dict[str, str] | None = None,
    ) -> None:
        self.measure = measure
        self.source = source
        self.scenarios = scenarios if scenarios is not None else {}
        self.datasets = self._get_formatted_datasets(data_loader, age_group_df)
        self.weights = self._get_aggregated_weights(data_loader, age_group_df)

    @property
    def dataset_names(self) -> dict[str, str]:
        """Return a dictionary of required datasets for the specified source."""
        if self.source == DataSource.SIM:
            return self.measure.sim_output_datasets
        elif self.source in ([DataSource.ARTIFACT, DataSource.GBD]):
            return self.measure.sim_input_datasets
        else:
            raise ValueError(f"Unsupported data source: {self.source}")

    @property
    def index_names(self) -> set[str]:
        return {
            index_name
            for key in self.datasets
            for index_name in self.datasets[key].index.names
        }

    def get_metadata(self) -> dict[str, Any]:
        """Organize the data information into a dictionary for display by a styled pandas DataFrame.
        Apply formatting to values that need special handling.

        Returns:
        --------
        A dictionary containing the formatted data information.

        """
        dataframe = self.get_measure_data("all")
        data_info: dict[str, Any] = {}

        # Source as string
        data_info["source"] = self.source.value

        # Index columns as comma-separated string
        data_info["index_columns"] = list(dataframe.index.names)

        # Size as formatted string
        size = dataframe.shape
        data_info["size"] = f"{size[0]:,} rows × {size[1]:,} columns"

        # Draw information
        if DRAW_INDEX in dataframe.index.names:
            num_draws = dataframe.index.get_level_values(DRAW_INDEX).nunique()
            data_info["num_draws"] = f"{num_draws:,}"
            draw_values = list(dataframe.index.get_level_values(DRAW_INDEX).unique())
            data_info[DRAW_INDEX + "s"] = dataframe_utils.format_draws_sample(
                draw_values, self.source
            )

        # Seeds information
        if SEED_INDEX in dataframe.index.names:
            num_seeds = dataframe.index.get_level_values(SEED_INDEX).nunique()
            data_info["num_seeds"] = f"{num_seeds:,}"

        return data_info

    def _get_formatted_datasets(
        self, data_loader: DataLoader, age_group_data: pd.DataFrame
    ) -> dict[str, pd.DataFrame]:
        """Formats measure datasets depending on the source."""
        raw_datasets = data_loader._get_raw_data_from_source(self.dataset_names, self.source)
        if self.source == DataSource.SIM:
            datasets = self.measure.get_ratio_datasets_from_sim(
                **raw_datasets,
            )
        elif self.source in [DataSource.ARTIFACT, DataSource.GBD]:
            data = self.measure.get_measure_data_from_sim_inputs(**raw_datasets)
            datasets = {"data": data}
        elif self.source == DataSource.CUSTOM:
            raise NotImplementedError
        else:
            raise ValueError(f"Unsupported data source: {self.source}")

        datasets = {
            dataset_name: age_groups.format_dataframe_from_age_bin_df(dataset, age_group_data)
            for dataset_name, dataset in datasets.items()
        }
        datasets = {
            key: calculations.filter_data(dataset, self.scenarios, drop_singles=True)
            for key, dataset in datasets.items()
        }

        return datasets

    def _get_aggregated_weights(
        self, data_loader: DataLoader, age_group_data: pd.DataFrame
    ) -> pd.DataFrame | None:
        """Fetches and aggregates weights if required by the measure."""
        if self.source not in [DataSource.ARTIFACT, DataSource.GBD]:
            return None

        raw_weights = data_loader._get_raw_data_from_source(
            self.measure.rate_aggregation_weights.weight_keys, self.source
        )
        weights = self.measure.rate_aggregation_weights.get_weights(**raw_weights)
        return age_groups.format_dataframe_from_age_bin_df(weights, age_group_data)

    def get_measure_data(
        self, stratifications: Collection[str] | Literal["all"]
    ) -> pd.DataFrame:
        """Get the measure data, optionally aggregated over specified stratifications."""
        if self.source == DataSource.SIM:
            return self._aggregate_scenario_stratifications(self.datasets, stratifications)
        elif self.source in [DataSource.ARTIFACT, DataSource.GBD]:
            return self._aggregate_sim_input_stratifications(stratifications)
        elif self.source == DataSource.CUSTOM:
            raise NotImplementedError
        else:
            raise ValueError(f"Unsupported data source: {self.source}")

    def _aggregate_scenario_stratifications(
        self,
        datasets: dict[str, pd.DataFrame],
        stratifications: Collection[str] | Literal["all"],
    ) -> pd.DataFrame:
        """This will remove index levels corresponding to the specified stratifications"""
        datasets = {
            key: calculations.stratify(datasets[key], stratifications) for key in datasets
        }
        return self.measure.get_measure_data_from_ratio(**datasets)

    def _aggregate_sim_input_stratifications(
        self, stratifications: Collection[str] | Literal["all"]
    ) -> pd.DataFrame:
        """Aggregate the artifact data over specified stratifications. Stratifactions will be retained
        in the returned data."""
        data = self.datasets["data"].copy()
        if stratifications != "all":
            stratifications = list(stratifications)
            # Retain input_draw, comparison._aggregate_over_draws is the only place we should aggregate over draws.
            if DRAW_INDEX in data.index.names and DRAW_INDEX not in stratifications:
                stratifications.append(DRAW_INDEX)
        if self.weights is None:
            raise ValueError("Weights are required for aggregating artifact data.")

        # Update scenario columns to retain during aggregation
        scenario_cols = []
        # NOTE: This is a hack to handle alignment of index levels in weighted_average. Risk
        # stratification column is treated as a scenario column and the population can be
        # broadcast across each index group since the exposure for each group should sum to 1.
        if isinstance(self.measure, (RiskExposure, CategoricalRelativeRisk)):
            scenario_cols.append(self.measure.risk_stratification_column)
        scenario_cols.extend(list(self.scenarios.keys()))
        weighted_avg = calculations.weighted_average(
            data, self.weights, stratifications, scenario_cols
        )

        # Reference data can be a float or dataframe. Convert floats so dataframes are aligned
        if not isinstance(weighted_avg, pd.DataFrame):
            weighted_avg = pd.DataFrame(
                {"value": [weighted_avg]}, index=pd.Index([0], name="index")
            )
        return weighted_avg
