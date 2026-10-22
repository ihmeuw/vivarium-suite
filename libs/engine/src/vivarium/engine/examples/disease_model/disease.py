from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from vivarium.engine import Component
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.state_machine import Machine, State, Transition, Trigger
from vivarium.engine.framework.utilities import rate_to_probability
from vivarium.engine.framework.values import list_combiner, union_post_processor
from vivarium.engine.types import DataInput


class DiseaseTransition(Transition):

    ##############
    # Properties #
    ##############

    @property
    def name(self) -> str:
        # The generated name repeats both state names, which makes an unwieldy config key.
        return f"disease_transition.{self.rate_name}"

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        return {self.name: {"data_sources": {self.measure: self._transition_rate}}}

    #####################
    # Lifecycle methods #
    #####################
    def __init__(
        self,
        input_state: "DiseaseState",
        output_state: "DiseaseState",
        cause_key: str,
        measure: str,
        rate_name: str,
        transition_rate: DataInput,
        triggered: Trigger = Trigger.NOT_TRIGGERED,
    ):
        super().__init__(
            input_state, output_state, probability_func=self._probability, triggered=triggered
        )
        self.cause_key = cause_key
        self.measure = measure
        self.rate_name = rate_name
        self._transition_rate = transition_rate
        self.joint_paf_pipeline = f"{self.rate_name}.population_attributable_fraction"

    # noinspection PyAttributeOutsideInit
    def setup(self, builder: Builder) -> None:
        super().setup(builder)

        self.base_rate_table = self.build_lookup_table(builder, self.measure)
        builder.value.register_attribute_producer(
            self.joint_paf_pipeline,
            source=lambda index: [pd.Series(0.0, index=index)],
            preferred_combiner=list_combiner,
            preferred_post_processor=union_post_processor,
            description="The share of this transition rate attributable to its risks",
        )
        builder.value.register_rate_producer(
            self.rate_name,
            source=self._risk_deleted_rate,
            required_resources=[self.joint_paf_pipeline],
            description="The transition rate with its attributable risk burden removed",
        )

    ##################################
    # Pipeline sources and modifiers #
    ##################################

    def _risk_deleted_rate(self, index: pd.Index[int]) -> pd.Series[float]:
        joint_paf = self.population_view.get(index, self.joint_paf_pipeline)
        return self.base_rate_table(index) * (1 - joint_paf)

    ##################
    # Helper methods #
    ##################

    def _probability(self, index: pd.Index[int]) -> pd.Series[float]:
        effective_rate = self.population_view.get(index, self.rate_name)
        return pd.Series(rate_to_probability(effective_rate))


class DiseaseState(State):

    ##############
    # Properties #
    ##############

    @property
    def configuration_defaults(self) -> dict[str, Any]:
        configuration_defaults = super().configuration_defaults
        configuration_defaults[self.name]["data_sources"][
            "excess_mortality_rate"
        ] = self._excess_mortality_rate
        return configuration_defaults

    #####################
    # Lifecycle methods #
    #####################

    def __init__(
        self, state_id: str, cause_key: str, excess_mortality_rate: DataInput = 0.0
    ):
        super().__init__(state_id)
        self._cause_key = cause_key
        # Stored privately so the default rate does not become part of the component name.
        self._excess_mortality_rate = excess_mortality_rate
        self.emr_paf_pipeline = (
            f"{self.state_id}.excess_mortality_rate.population_attributable_fraction"
        )
        self.emr_pipeline = f"{self.state_id}.excess_mortality_rate"

    # noinspection PyAttributeOutsideInit
    def setup(self, builder: Builder) -> None:
        """Performs this component's simulation setup.

        Parameters
        ----------
        builder
            Interface to several simulation tools.
        """
        super().setup(builder)

        self.clock = builder.time.clock()
        self.emr_table = self.build_lookup_table(builder, "excess_mortality_rate")
        builder.value.register_attribute_producer(
            self.emr_paf_pipeline,
            source=lambda index: [pd.Series(0.0, index=index)],
            preferred_combiner=list_combiner,
            preferred_post_processor=union_post_processor,
            description="The share of this cause's excess mortality due to its risks",
        )

        builder.value.register_rate_producer(
            self.emr_pipeline,
            source=self.risk_deleted_excess_mortality_rate,
            required_resources=[self.emr_paf_pipeline],
            description="This state's excess mortality with its risk burden removed",
        )

        builder.value.register_attribute_modifier(
            "mortality_rate",
            modifier=self.add_in_excess_mortality,
            required_resources=[self.emr_pipeline],
            description="Add this state's excess mortality to the all-cause rate",
        )

    ##################
    # Public methods #
    ##################

    def add_disease_transition(
        self,
        output: "DiseaseState",
        measure: str,
        rate_name: str,
        transition_rate: DataInput,
        triggered: Trigger = Trigger.NOT_TRIGGERED,
    ) -> DiseaseTransition:
        t = DiseaseTransition(
            self,
            output,
            self._cause_key,
            measure,
            rate_name,
            transition_rate,
            triggered=triggered,
        )
        self.add_transition(t)
        return t

    ##################################
    # Pipeline sources and modifiers #
    ##################################

    def risk_deleted_excess_mortality_rate(self, index: pd.Index[int]) -> pd.Series[float]:
        emr = pd.Series(0.0, index=index)
        in_state = self.population_view.get_filtered_index(
            index, query=f"{self.model} == '{self.state_id}'"
        )
        base_emr = self.emr_table(in_state)
        emr_paf = self.population_view.get(in_state, self.emr_paf_pipeline)
        emr.loc[in_state] = base_emr * (1 - emr_paf)
        return emr

    def add_in_excess_mortality(
        self, index: pd.Index[int], mortality_rates: pd.Series[float]
    ) -> pd.Series[float]:
        # The annual EMR is added to the annual all-cause rate, which mortality_rate
        # rescales to the time step once in its own post-processor.
        mortality_rates.loc[index] += self.population_view.get(
            index, self.emr_pipeline, mode="no-post-processors"
        )
        return mortality_rates


class DiseaseModel(Machine):

    #####################
    # Lifecycle methods #
    #####################

    def __init__(
        self,
        state_column: str,
        states: Iterable[State] = (),
        initial_state: State | None = None,
    ) -> None:
        super().__init__(state_column, states, initial_state)
        self.csmr_pipeline = f"{self.state_column}.cause_specific_mortality_rate"

    # noinspection PyAttributeOutsideInit
    def setup(self, builder: Builder) -> None:
        super().setup(builder)
        cause_specific_mortality_rate = self._approximate_csmr(builder)

        builder.value.register_rate_producer(
            self.csmr_pipeline,
            source=lambda index: pd.Series(cause_specific_mortality_rate, index=index),
            description="This cause's contribution to the all-cause mortality rate",
        )
        builder.value.register_attribute_modifier(
            "mortality_rate",
            modifier=self.delete_cause_specific_mortality,
            required_resources=[self.csmr_pipeline],
            description="Subtract this cause's mortality so each state can add its own",
        )

    ##################################
    # Pipeline sources and modifiers #
    ##################################

    def delete_cause_specific_mortality(
        self, index: pd.Index[int], rates: pd.Series[float]
    ) -> pd.Series[float]:
        # The annual CSMR is subtracted from the annual all-cause rate, which
        # mortality_rate rescales to the time step once in its own post-processor.
        csmr: pd.Series[float] = self.population_view.get(
            index, self.csmr_pipeline, mode="no-post-processors"
        )
        return rates - csmr

    ##################
    # Helper methods #
    ##################

    def _approximate_csmr(self, builder: Builder) -> float:
        """Approximate the CSMR as incidence times case fatality, which suits short diseases."""
        config = builder.configuration
        rates: dict[str, float] = {}
        excess_mortality_rate = 0.0
        for state in self.states:
            for transition in state.transition_set.transitions:
                if isinstance(transition, DiseaseTransition):
                    rates[transition.measure] = config[transition.name].data_sources[
                        transition.measure
                    ]
                    if transition.measure == "remission_rate":
                        excess_mortality_rate = config[state.name].data_sources[
                            "excess_mortality_rate"
                        ]
        case_fatality_rate = excess_mortality_rate / (
            excess_mortality_rate + rates["remission_rate"]
        )
        return float(rates["incidence_rate"] * case_fatality_rate)


class SISDiseaseModel(Component):
    def __init__(self, disease_name: str):
        super().__init__()
        self._name = disease_name

        susceptible_state = DiseaseState(f"susceptible_to_{self._name}", self._name)
        infected_state = DiseaseState(
            f"infected_with_{self._name}", self._name, excess_mortality_rate=0.01
        )

        susceptible_state.add_disease_transition(
            infected_state,
            measure="incidence_rate",
            rate_name=f"{infected_state.state_id}.incidence_rate",
            transition_rate=0.005,
        )
        infected_state.add_disease_transition(
            susceptible_state,
            measure="remission_rate",
            rate_name=f"{infected_state.state_id}.remission_rate",
            transition_rate=0.05,
        )

        model = DiseaseModel(
            self._name,
            initial_state=susceptible_state,
            states=[susceptible_state, infected_state],
        )
        self._sub_components = [model]
