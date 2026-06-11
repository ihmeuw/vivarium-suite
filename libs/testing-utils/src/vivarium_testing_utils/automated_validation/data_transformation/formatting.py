import pandas as pd

from vivarium_testing_utils.automated_validation.constants import DRAW_INDEX, SEED_INDEX
from vivarium_testing_utils.automated_validation.data_transformation import calculations


class SimDataFormatter:
    """A SimDataFormatter contains information about how to format particular kinds of
    simulaton data for use in a measure calculation. For example, incidence relies on
    both transition counts and person time data, which require different formatting/ operations
    on assumed columns in the simulation data."""

    def __init__(self, measure: str, entity: str, filter_value: str) -> None:
        self.measure = measure
        self.entity = entity
        self.raw_dataset_name = f"{self.measure}_{self.entity}"
        self.unused_columns = [
            "measure",
            "entity_type",
            "entity",
        ]
        self.filters = {"sub_entity": [filter_value]}
        self.filter_value = filter_value
        self.name = f"{self.filter_value}_{self.measure}"

    def format_dataset(self, dataset: pd.DataFrame) -> pd.DataFrame:
        """Clean up unused columns, and filter for the state."""
        dataset = calculations.marginalize(dataset, self.unused_columns)
        if self.filter_value == "total":
            dataset = calculations.marginalize(dataset, [*self.filters])
        else:
            dataset = calculations.filter_data(dataset, self.filters)
        return dataset


class TransitionCounts(SimDataFormatter):
    """Formatter for simulation data that contains transition counts."""

    def __init__(self, entity: str, start_state: str, end_state: str) -> None:
        super().__init__(
            measure="transition_count",
            entity=entity,
            filter_value=f"{start_state}_to_{end_state}",
        )


class StatePersonTime(SimDataFormatter):
    """Formatter for simulation data that contains person time."""

    def __init__(self, entity: str | None = None, filter_value: str | None = None) -> None:
        super().__init__(
            measure="person_time",
            entity=entity or "total",
            filter_value=filter_value or "total",
        )


class TotalPopulationPersonTime(StatePersonTime):
    """Formatter for simulation data that contains total person time."""

    def __init__(self, scenario_columns: list[str]) -> None:
        """
        Get person time aggregated over populations from total person time dataset.

        Parameters
        ----------
        scenario_columns
            Column names for scenario stratification. Defaults to an empty list.
        """
        super().__init__(entity="total", filter_value="total")
        self.raw_dataset_name = "person_time_total"
        self.name = "total_population_person_time"
        self.scenario_columns = scenario_columns

    def format_dataset(self, dataset: pd.DataFrame) -> pd.DataFrame:
        dataset = super().format_dataset(dataset)
        between_scenario_levels = [DRAW_INDEX, SEED_INDEX] + self.scenario_columns
        levels_to_stratify = [
            level for level in between_scenario_levels if level in dataset.index.names
        ]
        return calculations.stratify(
            data=dataset,
            stratification_cols=levels_to_stratify,
        )


class Deaths(SimDataFormatter):
    """Formatter for simulation data that contains death counts."""

    def __init__(self, cause: str) -> None:
        """
        Initialize the Deaths formatter with cause-specific or all-cause settings.

        Parameters
        ----------
        cause
            The specific cause of death to filter for. If None, all deaths are included.
        """

        self.measure = self.raw_dataset_name = "deaths"
        self.unused_columns = ["measure", "entity_type"]
        self.filter_value = "total" if cause == "all_causes" else cause
        self.filters = {"entity": [self.filter_value], "sub_entity": [self.filter_value]}
        self.name = f"{self.filter_value}_{self.measure}"


class RiskStatePersonTime(SimDataFormatter):
    """RiskStatePersonTime changes the sub_entity name to 'parameter' and, if total=True, replaces the value for *each* risk state
    with the sum over all risk states for the given sub-index.

    """

    def __init__(self, entity: str, sum_all: bool = False) -> None:
        self.entity = entity
        self.raw_dataset_name = f"person_time_{self.entity}"
        self.sum_all = sum_all
        self.name = "person_time"
        if sum_all:
            self.name += "_total"
        self.unused_columns = ["measure", "entity_type", "entity"]

    def format_dataset(self, dataset: pd.DataFrame) -> pd.DataFrame:
        dataset = calculations.marginalize(dataset, self.unused_columns)
        if self.sum_all:
            # Get the levels to group by (all except 'sub_entity')
            group_levels = [
                i for i, name in enumerate(dataset.index.names) if name != "sub_entity"
            ]
            # Use groupby with level numbers and transform to apply sum while preserving index
            dataset["value"] = dataset.groupby(level=group_levels)["value"].transform("sum")

        dataset = dataset.rename_axis(index={"sub_entity": "parameter"})
        return dataset
