from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pandas as pd

from vivarium_testing_utils.automated_validation.constants import DataSource
from vivarium_testing_utils.automated_validation.data_transformation import (
    calculations,
    utils,
)
from vivarium_testing_utils.automated_validation.data_transformation.data_schema import (
    SimOutputData,
    SingleNumericColumn,
)
from vivarium_testing_utils.automated_validation.data_transformation.formatting import (
    Deaths,
    RiskStatePersonTime,
    SimDataFormatter,
    StatePersonTime,
    TotalPopulationPersonTime,
    TransitionCounts,
)
from vivarium_testing_utils.automated_validation.data_transformation.rate_aggregation import (
    RateAggregationWeights,
    population_weighted,
)


class Measure(ABC):
    """A Measure contains key information and methods to take raw data from a DataSource
    and process it into an epidemiological measure suitable for use in a Comparison."""

    def __init__(self, entity_type: str, entity: str, measure: str) -> None:
        self.entity_type = entity_type
        self.entity = entity
        self.measure = measure

    @property
    def measure_key(self) -> str:
        """Return the key for this measure."""
        return self.artifact_key

    @property
    def artifact_key(self) -> str:
        parts = [self.entity_type, self.entity, self.measure]
        return ".".join([part for part in parts if part])

    @property
    def title(self) -> str:
        """Return a formatted title for the measure."""
        return _format_title(self.measure_key)

    def __str__(self) -> str:
        return self.measure_key

    @property
    @abstractmethod
    def sim_output_datasets(self) -> dict[str, str]:
        """Return a dictionary of required datasets for this measure."""
        pass

    @property
    @abstractmethod
    def sim_input_datasets(self) -> dict[str, str]:
        """Return a dictionary of required datasets for this measure."""
        pass

    @abstractmethod
    def get_measure_data_from_sim_inputs(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """Process artifact data into a format suitable for calculations."""
        pass

    @abstractmethod
    def get_measure_data_from_sim(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """Process raw simulation data into a format suitable for calculations."""
        pass

    @property
    @abstractmethod
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Override in subclasses to specify aggregation behavior."""
        pass


class RatioMeasure(Measure, ABC):
    """A Measure that calculates ratio data from simulation data."""

    def __init__(
        self,
        entity_type: str,
        entity: str,
        measure: str,
        numerator: SimDataFormatter,
        denominator: SimDataFormatter,
    ) -> None:
        super().__init__(entity_type, entity, measure)
        self.numerator = numerator
        self.denominator = denominator

    @property
    def sim_output_datasets(self) -> dict[str, str]:
        """Return a dictionary of required datasets for this measure."""
        return {
            "numerator_data": self.numerator.raw_dataset_name,
            "denominator_data": self.denominator.raw_dataset_name,
        }

    @property
    def sim_input_datasets(self) -> dict[str, str]:
        """Return a dictionary of required datasets for this measure."""
        return {
            "data": self.artifact_key,
        }

    @utils.check_io(
        numerator_data=SingleNumericColumn,
        denominator_data=SingleNumericColumn,
        out=SingleNumericColumn,
    )
    def get_measure_data_from_ratio(
        self, numerator_data: pd.DataFrame, denominator_data: pd.DataFrame
    ) -> pd.DataFrame:
        """Compute final measure data from separate numerator and denominator data."""
        return calculations.ratio(numerator_data, denominator_data)

    @utils.check_io(out=SingleNumericColumn)
    def get_measure_data_from_sim(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """Process raw simulation data into a format suitable for calculations."""
        return self.get_measure_data_from_ratio(
            **self.get_ratio_datasets_from_sim(*args, **kwargs)
        )

    @utils.check_io(
        numerator_data=SimOutputData,
        denominator_data=SimOutputData,
    )
    def get_ratio_datasets_from_sim(
        self,
        numerator_data: pd.DataFrame,
        denominator_data: pd.DataFrame,
    ) -> dict[str, pd.DataFrame]:
        """Process raw simulation data and return numerator and denominator DataFrames separately."""
        numerator_data = self.numerator.format_dataset(numerator_data)
        denominator_data = self.denominator.format_dataset(denominator_data)
        numerator_data, denominator_data = _align_indexes(numerator_data, denominator_data)
        return {"numerator_data": numerator_data, "denominator_data": denominator_data}


class Incidence(RatioMeasure):
    """Computes Susceptible Population Incidence Rate."""

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return RateAggregationWeights(
            weight_keys={
                "population": "population.structure",
                "prevalence": f"cause.{self.entity}.prevalence",
            },
            # TODO: Update formula to account for having more than two states. Only works for SI and SIS models.
            formula=lambda population, prevalence: population * (1 - prevalence),
            description="Person-time × (1 - prevalence) weighted average",
        )

    def __init__(self, cause: str) -> None:
        super().__init__(
            entity_type="cause",
            entity=cause,
            measure="incidence_rate",
            numerator=TransitionCounts(cause, f"susceptible_to_{cause}", cause),
            denominator=StatePersonTime(cause, f"susceptible_to_{cause}"),
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data


class Prevalence(RatioMeasure):
    """Computes Prevalence of cause in the population."""

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return population_weighted()

    def __init__(self, cause: str) -> None:
        super().__init__(
            entity_type="cause",
            entity=cause,
            measure="prevalence",
            numerator=StatePersonTime(cause, cause),
            denominator=StatePersonTime(cause),
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data


class SIRemission(RatioMeasure):
    """Computes (SI) remission rate among infected population."""

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return RateAggregationWeights(
            weight_keys={
                "population": "population.structure",
                "prevalence": f"cause.{self.entity}.prevalence",
            },
            formula=lambda population, prevalence: population * prevalence,
            description="Person-time × prevalence weighted average",
        )

    def __init__(self, cause: str) -> None:
        super().__init__(
            entity_type="cause",
            entity=cause,
            measure="remission_rate",
            numerator=TransitionCounts(cause, cause, f"susceptible_to_{cause}"),
            denominator=StatePersonTime(cause, cause),
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data


class CauseSpecificMortalityRate(RatioMeasure):
    """Computes cause-specific mortality rate in the population."""

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return population_weighted()

    def __init__(self, cause: str) -> None:
        super().__init__(
            entity_type="cause",
            entity=cause,
            measure="cause_specific_mortality_rate",
            numerator=Deaths(cause),  # Deaths due to specific cause
            denominator=StatePersonTime(),  # Total person time
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data


class ExcessMortalityRate(RatioMeasure):
    """Computes excess mortality rate among those with the disease compared to the general population."""

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return RateAggregationWeights(
            weight_keys={
                "population": "population.structure",
                "prevalence": f"cause.{self.entity}.prevalence",
            },
            formula=lambda population, prevalence: population * prevalence,
            description="Person-time × prevalence weighted average",
        )

    def __init__(self, cause: str) -> None:
        super().__init__(
            entity_type="cause",
            entity=cause,
            measure="excess_mortality_rate",
            numerator=Deaths(cause),  # Deaths due to specific cause
            denominator=StatePersonTime(
                cause, cause
            ),  # Person time among those with the disease
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data


class PopulationStructure(RatioMeasure):
    """Compares simulation population structure against artifact population structure.

    This measure aggregates person time data by age groups and sex to match
    the population structure format from the artifact. It's useful for validating
    that the simulation maintains realistic demographic distributions.
    """

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """This will be implemented when we refactor and implement DataBundle Mic-6241."""
        raise NotImplementedError

    def __init__(self, scenario_columns: list[str]):
        """Initialize PopulationStructure measure.

        Parameters
        ----------
        scenario_columns
            Column names for scenario stratification. Defaults to an empty list.
        """
        super().__init__(
            entity_type="",
            entity="population",
            measure="structure",
            numerator=StatePersonTime(),
            denominator=TotalPopulationPersonTime(scenario_columns),
        )

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data / data.sum()

    @utils.check_io(
        numerator_data=SimOutputData,
        denominator_data=SimOutputData,
    )
    def get_ratio_datasets_from_sim(
        self,
        numerator_data: pd.DataFrame,
        denominator_data: pd.DataFrame,
    ) -> dict[str, pd.DataFrame]:
        """Process raw simulation data and return numerator and denominator DataFrames separately."""
        numerator_data = self.numerator.format_dataset(numerator_data)
        denominator_data = self.denominator.format_dataset(denominator_data)
        return {"numerator_data": numerator_data, "denominator_data": denominator_data}


class RiskExposure(RatioMeasure):
    """Computes risk factor exposure levels in the population.

    This measure calculates exposure prevalence from state-specific person time data.
    For categorical risk factors (e.g., child wasting, stunting), exposure is computed
    as the proportion of person time spent in each risk state.

    Numerator: Person time in specific risk state
    Denominator: Total person time across all risk states
    """

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return population_weighted()

    def __init__(self, risk_factor: str) -> None:
        super().__init__(
            entity_type="risk_factor",
            entity=risk_factor,
            measure="exposure",
            numerator=RiskStatePersonTime(risk_factor),
            denominator=RiskStatePersonTime(risk_factor, sum_all=True),
        )
        self.risk_stratification_column = risk_factor

    @utils.check_io(data=SingleNumericColumn, out=SingleNumericColumn)
    def get_measure_data_from_sim_inputs(self, data: pd.DataFrame) -> pd.DataFrame:
        return data.rename_axis(index={"parameter": self.risk_stratification_column})


class CategoricalRelativeRisk(RatioMeasure):
    """Computes relative risk of a categorical variable."""

    def __init__(
        self,
        risk_factor: str,
        affected_entity: str,
        affected_measure: str,
        risk_stratification_column: str | None,
        risk_category_mapping: dict[str, str] | None,
    ) -> None:
        self.affected_entity = affected_entity
        self.affected_measure_name = affected_measure
        affected_measure_instance = MEASURE_KEY_MAPPINGS["cause"][affected_measure](
            affected_entity
        )

        if not isinstance(affected_measure_instance, RatioMeasure):
            raise TypeError(
                f"Expected affected_measure to be a RatioMeasure, got {type(affected_measure_instance)}"
            )
        self.affected_measure = affected_measure_instance
        self.risk_stratification_column = risk_stratification_column or risk_factor
        self.risk_category_mapping = risk_category_mapping
        super().__init__(
            entity_type="risk_factor",
            entity=risk_factor,
            measure="relative_risk",
            numerator=self.affected_measure.numerator,
            denominator=self.affected_measure.denominator,
        )

    @property
    def measure_key(self) -> str:
        """Return the measure key for this measure."""
        return ".".join(
            [
                self.entity_type,
                self.entity,
                self.measure,
                self.affected_entity,
                self.affected_measure_name,
            ]
        )

    @property
    def title(self) -> str:
        """Return a human-readable title for the measure."""
        format_str: Callable[[str], str] = lambda x: x.replace("_", " ").title()
        return f"Effect of {format_str(self.entity)} on {format_str(self.affected_entity)} {format_str(self.affected_measure_name)}"

    @property
    def sim_input_datasets(self) -> dict[str, str]:
        """Return a dictionary of required datasets for this measure."""
        return {
            "relative_risks": self.artifact_key,
            "affected_measure_data": self.affected_measure.artifact_key,
            "categories": f"risk_factor.{self.entity}.categories",
        }

    @property
    def rate_aggregation_weights(self) -> RateAggregationWeights:
        """Returns rate aggregated weights."""
        return self.affected_measure.rate_aggregation_weights

    @utils.check_io(
        relative_risks=SingleNumericColumn,
        affected_measure_data=SingleNumericColumn,
        out=SingleNumericColumn,
    )
    def get_measure_data_from_sim_inputs(
        self,
        relative_risks: pd.DataFrame,
        affected_measure_data: pd.DataFrame,
        categories: dict[str, str],
    ) -> pd.DataFrame:
        """Multiply relative risks by affected data to get final measure data."""
        relative_risks = calculations.filter_data(
            relative_risks,
            filter_cols={
                "affected_entity": self.affected_entity,
                "affected_measure": self.affected_measure_name,
            },
        )
        ## multiply relative risks by affected data being sure to broadcast unequal index levels
        risk_stratified_measure_data = relative_risks * affected_measure_data
        risk_category_mapping = (
            self.risk_category_mapping if self.risk_category_mapping else categories
        )

        # Map level 'parameter' values to risk states given by risk_state_mapping
        risk_stratified_measure_data = risk_stratified_measure_data.rename(
            index=risk_category_mapping, level="parameter"
        ).rename_axis(index={"parameter": self.risk_stratification_column})
        return risk_stratified_measure_data

    @utils.check_io(
        numerator_data=SimOutputData,
        denominator_data=SimOutputData,
    )
    def get_ratio_datasets_from_sim(
        self,
        numerator_data: pd.DataFrame,
        denominator_data: pd.DataFrame,
    ) -> dict[str, pd.DataFrame]:
        """Process raw simulation data and return numerator and denominator DataFrames separately."""
        ratio_datasets = self.affected_measure.get_ratio_datasets_from_sim(
            numerator_data=numerator_data,
            denominator_data=denominator_data,
        )
        for dataset in ratio_datasets.values():
            if not self.risk_stratification_column in dataset.index.names:
                raise ValueError(
                    f"Risk stratification column '{self.risk_stratification_column}' not found in dataset index names."
                )
        return ratio_datasets


MEASURE_KEY_MAPPINGS: dict[str, dict[str, Callable[..., Measure]]] = {
    "cause": {
        "incidence_rate": Incidence,
        "prevalence": Prevalence,
        "remission_rate": SIRemission,
        "cause_specific_mortality_rate": CauseSpecificMortalityRate,
        "excess_mortality_rate": ExcessMortalityRate,
    },
    "population": {
        "structure": PopulationStructure,
    },
    "risk_factor": {
        "exposure": RiskExposure,
    },
}


class MeasureMapper:
    """A class to manage measure mappings."""

    def __init__(self) -> None:
        self.mapper: defaultdict[str, dict[str, Callable[..., Measure]]] = defaultdict(
            dict, MEASURE_KEY_MAPPINGS
        )

    def add_new_measure(self, measure_key: str, measure_class: type[Measure]) -> None:
        """Add a new measure class to the context's measure mapper.

        Parameters
        ----------
        measure_key
            The measure key in format 'entity_type.entity.measure_key' or 'entity_type.measure_key'.
        measure_class
            The class implementing the measure.
        """

        parts = measure_key.split(".")
        if len(parts) not in (2, 3):
            raise ValueError(
                f"Measure key must be in format 'entity_type.entity.measure_key' or 'entity_type.measure_key'. "
                f"Got measure_key='{measure_key}'"
            )
        if len(parts) == 3:
            entity_type, _, measure_key = parts
        else:
            entity_type, measure_key = parts

        # NOTE: This will overwrite existing mappings
        self.mapper[entity_type][measure_key] = measure_class

    def get_measure_from_key(
        self,
        measure_key: str,
        scenario_columns: list[str],
    ) -> Measure:
        """Get a measure instance from a measure key string.

        Parameters
        ----------
        measure_key
            The measure key in format 'entity_type.entity.measure_key' or 'entity_type.measure_key'
        scenario_columns
            Column names for scenario stratification. Used by some measures like PopulationStructure.

        Returns
        -------
            The instantiated measure object
        """
        parts = measure_key.split(".")
        if len(parts) == 3:
            entity_type, entity, measure_key = parts
            return self.mapper[entity_type][measure_key](entity)
        elif len(parts) == 2:
            entity_type, measure_key = parts
            # Special case for PopulationStructure which needs scenario_columns
            if entity_type == "population" and measure_key == "structure":
                return self.mapper[entity_type][measure_key](scenario_columns)
            else:
                return self.mapper[entity_type][measure_key]()
        else:
            raise ValueError(
                f"Invalid measure key format: {measure_key}. Expected format is two or three period-delimited strings e.g. 'population.structure' or 'cause.deaths.excess_mortality_rate'."
            )


def _align_indexes(
    numerator: pd.DataFrame, denominator: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconcile indexes between numerator and denominator DataFrames. Dataframes can have unique columns given by the numerator_only_indexes and denominator_only_indexes.
    All other index levels must be summed over."""
    numerator_index_levels = set(numerator.index.names)
    denominator_index_levels = set(denominator.index.names)

    for level in numerator_index_levels - denominator_index_levels:
        numerator = calculations.marginalize(numerator, [level])
    for level in denominator_index_levels - numerator_index_levels:
        denominator = calculations.marginalize(denominator, [level])
    return (numerator, denominator)


def _format_title(measure_key: str) -> str:
    """Convert a measure key to a more readable format.

    For example, "cause.disease.incidence_rate" becomes "Disease Incidence Rate".
    """
    parts = measure_key.split(".")
    if len(parts) > 2:
        parts = parts[1:]
    title = " ".join(parts)
    title = title.replace("_", " ")
    title = " ".join([word.capitalize() for word in title.split()])
    return title
