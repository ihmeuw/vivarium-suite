from typing import Any

import numpy as np
import pandas as pd
import pytest
from vivarium.config_tree import ConfigTree
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.event import Event
from vivarium.engine.framework.population import SimulantData

from tests.test_utilities import make_uniform_pop_data
from vivarium.public_health.disease import SI
from vivarium.public_health.population import BasePopulation
from vivarium.public_health.risks import RiskEffect
from vivarium.public_health.risks.base_risk import Risk

#
# from vivarium.engine.framework.utilities import from_yearly
# from vivarium.engine.testing_utilities import build_table, BasePopulation
# from vivarium.interface.interactive import initialize_simulation
#
# from vivarium.public_health.disease import RateTransition
from vivarium.public_health.risks.effect import NonLogLinearRiskEffect, RiskEffect
from vivarium.public_health.utilities import EntityString

#
#
# def test_incidence_rate_risk_effect(base_config, base_plugins, mocker):
#     year_start = base_config.time.start.year
#     year_end = base_config.time.end.year
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#     test_exposure = [0]
#
#     def test_function(rates_, rr):
#         return rates_ * (rr.values**test_exposure[0])
#
#     r = 'test_risk'
#     d = 'test_cause'
#     rf = Risk(f'risk_factor.{r}')
#     effect_data_functions = {
#         'rr': lambda *args: build_table([1.01, 'per_unit', d, 'incidence_rate'], year_start, year_end,
#                                         ('age', 'year', 'sex', 'value', 'parameter', 'cause', 'affected_measure')),
#         'paf': lambda *args: build_table([0.01, d, 'incidence_rate'], year_start, year_end,
#                                          ('age', 'year', 'sex', 'value', 'cause', 'affected_measure')),
#     }
#
#     effect = RiskEffect(f'risk_factor.{r}', f'cause.{d}.incidence_rate', effect_data_functions)
#
#     simulation = initialize_simulation([BasePopulation(), effect], input_config=base_config, plugin_config=base_plugins)
#
#     simulation.data.write("risk_factor.test_risk.distribution", "dichotomous")
#     simulation.values.register_value_producer("test_risk.exposure", mocker.Mock())
#
#     simulation.setup()
#
#     effect.exposure_effect = test_function
#
#     # This one should be affected by our RiskEffect
#     rates = simulation.values.register_rate_producer('test_cause.incidence_rate')
#     rates.source = simulation.tables.build_table(build_table(0.01, year_start, year_end),
#                                                  key_columns=('sex',),
#                                                  parameter_columns=[('age', 'age_start', 'age_end'),
#                                                                     ('year', 'year_start', 'year_end')],
#                                                  value_columns=None)
#
#     # This one should not
#     other_rates = simulation.values.register_rate_producer('some_other_cause.incidence_rate')
#     other_rates.source = simulation.tables.build_table(build_table(0.01, year_start, year_end),
#                                                        key_columns=('sex',),
#                                                        parameter_columns=[('age', 'age_start', 'age_end'),
#                                                                           ('year', 'year_start', 'year_end')],
#                                                        value_columns=None)
#
#     assert np.allclose(rates(simulation.get_population().index), from_yearly(0.01, time_step))
#     assert np.allclose(other_rates(simulation.get_population().index), from_yearly(0.01, time_step))
#
#     test_exposure[0] = 1
#
#     assert np.allclose(rates(simulation.get_population().index), from_yearly(0.0101, time_step))
#     assert np.allclose(other_rates(simulation.get_population().index), from_yearly(0.01, time_step))
#
#
# def test_risk_deletion(base_config, base_plugins, mocker):
#     year_start = base_config.time.start.year
#     year_end = base_config.time.end.year
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#
#     base_rate = 0.01
#     risk_paf = 0.5
#     risk_rr = 1
#
#     rate_data_functions = {
#         'incidence_rate': lambda *args: build_table(0.01, year_start, year_end, ('age', 'year', 'sex', 'value'))
#     }
#
#     effect_data_functions = {
#         'rr': lambda *args: build_table([risk_rr, 'per_unit', 'infected','incidence_rate'], year_start, year_end,
#                                         ('age', 'year', 'sex', 'value', 'parameter', 'cause', 'affected_measure')),
#         'paf': lambda *args: build_table([risk_paf, 'infected', 'incidence_rate'], year_start, year_end,
#                                          ('age', 'year', 'sex', 'value', 'cause', 'affected_measure')),
#     }
#
#     def effect_function(rates, _):
#         return rates
#
#     transition = RateTransition(mocker.MagicMock(state_id='susceptible'),
#                                 mocker.MagicMock(state_id='infected'), rate_data_functions)
#
#     base_simulation = initialize_simulation([BasePopulation(), transition],
#                                             input_config=base_config, plugin_config=base_plugins)
#     base_simulation.setup()
#
#     incidence = base_simulation.get_value('infected.incidence_rate')
#     joint_paf = base_simulation.get_value('infected.incidence_rate.paf')
#
#     # Validate the base case
#     assert np.allclose(incidence(base_simulation.get_population().index), from_yearly(base_rate, time_step))
#     assert np.allclose(joint_paf(base_simulation.get_population().index), 0)
#
#     transition = RateTransition(mocker.MagicMock(state_id='susceptible'),
#                                 mocker.MagicMock(state_id='infected'), rate_data_functions)
#     effect = RiskEffect(f'risk_factor.bad_risk', f'cause.infected.incidence_rate', effect_data_functions)
#
#     rf_simulation = initialize_simulation([BasePopulation(), transition, effect],
#                                           input_config=base_config, plugin_config=base_plugins)
#
#     rf_simulation.data.write("risk_factor.bad_risk.distribution", "dichotomuous")
#     rf_simulation.values.register_value_producer("bad_risk.exposure", mocker.Mock())
#
#     rf_simulation.setup()
#     effect.exposure_effect = effect_function
#
#     incidence = rf_simulation.get_value('infected.incidence_rate')
#     joint_paf = rf_simulation.get_value('infected.incidence_rate.paf')
#
#     assert np.allclose(incidence(rf_simulation.get_population().index),
#                        from_yearly(base_rate * (1 - risk_paf), time_step))
#     assert np.allclose(joint_paf(rf_simulation.get_population().index), risk_paf)
#
#
# def test_continuous_exposure_effect(mocker, base_config, base_plugins, continuous_risk):
#     risk, risk_data = continuous_risk
#
#     class exposure_function_wrapper:
#
#         def setup(self, builder):
#             self.exposure_function = RiskEffect.get_exposure_effect(builder, 'test_risk', 'risk_factor',
#                                                                     risk_data['distribution'])
#
#         def __call__(self, *args, **kwargs):
#             return self.exposure_function(*args, **kwargs)
#
#     exposure_function = exposure_function_wrapper()
#
#     components = [BasePopulation(), exposure_function]
#     simulation = initialize_simulation(components, input_config=base_config, plugin_config=base_plugins)
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     risk_exposure_pipeline = mocker.Mock()
#     simulation.values.register_value_producer('test_risk.exposure', source=risk_exposure_pipeline)
#     risk_exposure_pipeline.side_effect = lambda index: pd.Series(risk_data['tmrel'], index=index)
#
#     simulation.setup()
#
#     rates = pd.Series(0.01, index=simulation.get_population().index)
#     rr = pd.Series(1.01, index=simulation.get_population().index)
#
#     assert np.all(exposure_function(rates, rr) == 0.01)
#
#     simulation.values.register_value_producer('test_risk.exposure', source=risk_exposure_pipeline)
#     risk_exposure_pipeline.side_effect = lambda index: pd.Series(risk_data['tmrel']+50, index=index)
#
#     expected_value = 0.01 * (1.01 ** (((risk_data['tmrel'] + 50) - risk_data['tmrel'])
#                                       / risk_data['exposure_parameters']["scale"]))
#
#     assert np.allclose(exposure_function(rates, rr), expected_value)
#
#
# def test_categorical_exposure_effect(base_config, base_plugins, mocker):
#     risk_effect = mocker.Mock()
#     risk_effect.risk = 'test_risk'
#
#     class exposure_function_wrapper:
#         def setup(self, builder):
#             self.exposure_function = RiskEffect.get_exposure_effect(builder, 'test_risk', 'risk_factor', 'dichotomous')
#
#         def __call__(self, *args, **kwargs):
#             return self.exposure_function(*args, **kwargs)
#
#     exposure_function = exposure_function_wrapper()
#     components = [BasePopulation(), exposure_function]
#
#     simulation = initialize_simulation(components, input_config=base_config, plugin_config=base_plugins)
#
#     test_risk_exposure = mocker.Mock()
#     simulation.values.register_value_producer('test_risk.exposure', test_risk_exposure)
#     test_risk_exposure.side_effect = lambda index: pd.Series(['cat2'] * len(index), index=index)
#     simulation.data.write("risk_factor.test_risk.distribution", "dichotomous")
#     simulation.setup()
#
#     rates = pd.Series(0.01, index=simulation.get_population().index)
#     rr = pd.DataFrame({'cat1': 1.01, 'cat2': 1}, index=simulation.get_population().index)
#
#     assert np.all(exposure_function(rates, rr) == 0.01)
#
#     test_risk_exposure.side_effect = lambda index: pd.Series(['cat1'] * len(index), index=index)
#     simulation.step()
#
#     rates = pd.Series(0.01, index=simulation.get_population().index)
#     rr = pd.DataFrame({'cat1': 1.01, 'cat2': 1}, index=simulation.get_population().index)
#
#     assert np.allclose(exposure_function(rates, rr), 0.0101)
#
#
# def test_CategoricalRiskComponent_dichotomous_case(base_config, base_plugins, dichotomous_risk):
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#     risk, risk_data = dichotomous_risk
#     affected_causes = risk_data['affected_causes']
#     risk_effects = [RiskEffect(f'risk_factor.{risk._risk}', f'cause.{ac}.incidence_rate') for ac in affected_causes]
#
#     base_config.update({'population': {'population_size': 100000}}, layer='override')
#
#     simulation = initialize_simulation([BasePopulation(), risk] + risk_effects,
#                                        input_config=base_config, plugin_config=base_plugins)
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     simulation.setup()
#
#     incidence_rate = simulation.values.register_rate_producer(affected_causes[0]+'.incidence_rate')
#     incidence_rate.source = simulation.tables.build_table(risk_data['incidence_rate'], key_columns=('sex',),
#                                                           parameter_columns=[('age', 'age_start', 'age_end'),
#                                                                              ('year', 'year_start', 'year_end')],
#                                                           value_columns=None)
#
#     categories = simulation.values.get_value('test_risk.exposure')(simulation.get_population().index)
#     assert np.isclose(categories.value_counts()['cat1'] / len(simulation.get_population()), 0.5, rtol=0.01)
#
#     expected_exposed_value = 0.01 * 1.01
#     expected_unexposed_value = 0.01
#
#     exposed_index = categories[categories == 'cat1'].index
#     unexposed_index = categories[categories == 'cat2'].index
#
#     assert np.allclose(incidence_rate(exposed_index), from_yearly(expected_exposed_value, time_step))
#     assert np.allclose(incidence_rate(unexposed_index), from_yearly(expected_unexposed_value, time_step))
#
#
# def test_CategoricalRiskComponent_polytomous_case(base_config, base_plugins, polytomous_risk):
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#     risk, risk_data = polytomous_risk
#     affected_causes = risk_data['affected_causes']
#
#     risk_effects = [RiskEffect(f'risk_factor.{risk._risk}', f'cause.{ac}.incidence_rate') for ac in affected_causes]
#
#     base_config.update({'population': {'population_size': 100000}}, layer='override')
#     simulation = initialize_simulation([BasePopulation(), risk] + risk_effects,
#                                        input_config=base_config, plugin_config=base_plugins)
#
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     simulation.setup()
#
#     incidence_rate = simulation.values.register_rate_producer(affected_causes[0]+'.incidence_rate')
#     incidence_rate.source = simulation.tables.build_table(risk_data['incidence_rate'],
#                                                           key_columns=('sex',),
#                                                           parameter_columns=[('age', 'age_start', 'age_end'),
#                                                                              ('year', 'year_start', 'year_end')],
#                                                           value_columns=None)
#
#     categories = simulation.values.get_value('test_risk.exposure')(simulation.get_population().index)
#
#     for category in ['cat1', 'cat2', 'cat3', 'cat4']:
#         assert np.isclose(categories.value_counts()[category] / len(simulation.get_population()), 0.25, rtol=0.02)
#
#     expected_exposed_value = 0.01 * np.array([1.02, 1.03, 1.01])
#
#     for cat, expected in zip(['cat1', 'cat2', 'cat3', 'cat4'], expected_exposed_value):
#         exposed_index = categories[categories == cat].index
#         assert np.allclose(incidence_rate(exposed_index), from_yearly(expected, time_step), rtol=0.01)
#
#
# def test_ContinuousRiskComponent(continuous_risk, base_config, base_plugins):
#     year_start, year_end = base_config.time.start.year, base_config.time.end.year
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#     risk, risk_data = continuous_risk
#     risk_data['exposure_standard_deviation'] = build_table(0.0001, year_start, year_end, ('age', 'year', 'sex', 'value'))
#     risk_effects = [RiskEffect(f'risk_factor.{risk._risk}', f'cause.{ac}.incidence_rate') for ac in risk_data['affected_causes']]
#
#     base_config.update({'population': {'population_size': 100000}}, layer='override')
#     simulation = initialize_simulation([BasePopulation(), risk] + risk_effects,
#                                        input_config=base_config, plugin_config=base_plugins)
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     simulation.setup()
#     affected_causes = risk_data['affected_causes']
#
#     incidence_rate = simulation.values.register_rate_producer(affected_causes[0]+'.incidence_rate',
#                                                               source=lambda index: pd.Series(0.01, index=index))
#
#     exposure = simulation.values.get_value('test_risk.exposure')
#
#     assert np.allclose(exposure(simulation.get_population().index), 130, rtol=0.001)
#
#     expected_value = 0.01 * (1.01**((130 - 112) / 10))
#
#     assert np.allclose(incidence_rate(simulation.get_population().index),
#                        from_yearly(expected_value, time_step), rtol=0.001)
#
#
# def test_exposure_params_risk_effect_dichotomous(base_config, base_plugins, dichotomous_risk, coverage_gap):
#     affected_risk, risk_data = dichotomous_risk
#     coverage_gap, cg_data = coverage_gap
#     rf_exposed = 0.5
#     rr = 2 # rr between cg/affected_risk
#
#     base_config.update({'population': {'population_size': 100000}}, layer='override')
#     affected_risk = Risk('risk_factor.test_risk')
#
#     # start with the only risk factor without indirect effect from coverage_gap
#     simulation = initialize_simulation([BasePopulation(), affected_risk],
#                                        input_config=base_config, plugin_config=base_plugins)
#
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     simulation.setup()
#
#     pop = simulation.get_population()
#     exposure = simulation.values.get_value('test_risk.exposure')
#     assert np.isclose(rf_exposed, exposure(pop.index).value_counts()['cat1']/len(pop), rtol=0.01)
#
#     # add the coverage gap which should change the exposure of test risk
#     risk_effects = [RiskEffect(f'coverage_gap.{coverage_gap._risk}',
#                                f'risk_factor.{rf}.exposure_parameters') for rf in cg_data['affected_risk_factors']]
#
#     simulation = initialize_simulation([BasePopulation(), affected_risk, coverage_gap] + risk_effects,
#                                        input_config=base_config, plugin_config=base_plugins)
#
#     for key, value in risk_data.items():
#         simulation.data.write(f'risk_factor.test_risk.{key}', value)
#
#     for key, value in cg_data.items():
#         simulation.data.write(f'coverage_gap.test_coverage_gap.{key}', value)
#
#     simulation.setup()
#
#     pop = simulation.get_population()
#     rf_exposure = simulation.values.get_value('test_risk.exposure')(pop.index)
#
#     # proportion of simulants exposed to each category of affected risk stays same
#     assert np.isclose(rf_exposed, rf_exposure.value_counts()['cat1']/len(pop), rtol=0.01)
#
#     # compute relative risk to test whether it matches with the given relative risk
#     cg_exposure = simulation.values.get_value('test_coverage_gap.exposure')(pop.index)
#
#     cg_exposed = cg_exposure == 'cat1'
#     rf_exposed = rf_exposure == 'cat1'
#
#     affected_by_cg = rf_exposed & cg_exposed
#     not_affected_by_cg = rf_exposed & ~cg_exposed
#
#     computed_rr = (len(pop[affected_by_cg])/len(pop[cg_exposed])) / (len(pop[not_affected_by_cg])/len(pop[~cg_exposed]))
#     assert np.isclose(computed_rr, rr, rtol=0.01)
#
#
# def test_RiskEffect_config_data(base_config, base_plugins):
#     dummy_risk = Risk("risk_factor.test_risk")
#     dummy_effect = RiskEffect("risk_factor.test_risk", "cause.test_cause.incidence_rate")
#     year_start = base_config.time.start.year
#     year_end = base_config.time.end.year
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#
#     base_config.update({'test_risk': {'exposure': 1}}, layer='override')
#     base_config.update({'effect_of_test_risk_on_test_cause': {'incidence_rate': 50}})
#     simulation = initialize_simulation([BasePopulation(), dummy_risk, dummy_effect],
#                                        input_config=base_config, plugin_config=base_plugins)
#
#     simulation.setup()
#
#     # make sure our dummy exposure value is being properly used
#     exp = simulation.values.get_value('test_risk.exposure')(simulation.get_population().index)
#     assert((exp == 'cat1').all())
#
#     # This one should be affected by our DummyRiskEffect
#     rates = simulation.values.register_rate_producer('test_cause.incidence_rate',
#                                                      source=lambda index: pd.Series(0.01, index=index))
#
#     # This one should not
#     other_rates = simulation.values.register_rate_producer('some_other_cause.incidence_rate',
#                                                            source=lambda index: pd.Series(0.01, index=index))
#
#     assert np.allclose(rates(simulation.get_population().index), from_yearly(0.01, time_step)*50)
#     assert np.allclose(other_rates(simulation.get_population().index), from_yearly(0.01, time_step))
#
#
# def test_RiskEffect_excess_mortality(base_config, base_plugins):
#     dummy_risk = Risk("risk_factor.test_risk")
#     dummy_effect = RiskEffect("risk_factor.test_risk", "cause.test_cause.excess_mortality_rate")
#     time_step = pd.Timedelta(days=base_config.time.step_size)
#
#     base_config.update({'test_risk': {'exposure': 1}}, layer='override')
#     base_config.update({'effect_of_test_risk_on_test_cause': {'excess_mortality_rate': 50}})
#
#     simulation = initialize_simulation([BasePopulation(), dummy_risk, dummy_effect],
#                                        input_config=base_config, plugin_config=base_plugins)
#     simulation.setup()
#
#     em = simulation.values.register_rate_producer('test_cause.excess_mortality_rate',
#                                                   source=lambda index: pd.Series(0.1, index=index))
#
#     assert np.allclose(from_yearly(0.1, time_step)*50, em(simulation.get_population().index))


def _setup_risk_effect_simulation(
    config: ConfigTree,
    plugins: ConfigTree,
    risk: str | Risk,
    risk_effect: RiskEffect,
    data: dict[str, Any],
) -> InteractiveContext:
    components = [
        BasePopulation(),
        risk,
        SI("test_cause"),
        risk_effect,
    ]

    simulation = InteractiveContext(
        components=components,
        configuration=config,
        plugin_configuration=plugins,
        setup=False,
    )

    for key, value in data.items():
        simulation._data.write(key, value)

    simulation.setup()
    return simulation


def build_dichotomous_risk_effect_data(rr_value: float) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "affected_entity": "test_cause",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "value": [rr_value, 1.0],
        },
        index=pd.Index(["cat1", "cat2"], name="parameter"),
    )
    return df


@pytest.mark.parametrize(
    "rr_source, rr_value",
    [("str", 2.0), ("float", 0.9), ("DataFrame", 0.5)],
)
def test_rr_sources(rr_source, rr_value, dichotomous_risk, base_config, base_plugins):
    risk = dichotomous_risk[0]
    effect = RiskEffect(risk.name, "cause.test_cause.incidence_rate")
    base_config.update({"risk_factor.test_risk": {"data_sources": {"exposure": 1.0}}})

    # TMREL of 1
    tmred = {"distribution": "uniform", "min": 1, "max": 1, "inverted": False}

    data = {
        f"{risk.name}.tmred": tmred,
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }

    if rr_source == "DataFrame":
        rr_data = build_dichotomous_risk_effect_data(rr_value)
        base_config.update(
            {
                "risk_effect.test_risk_on_cause.test_cause.incidence_rate": {
                    "data_sources": {"relative_risk": rr_data}
                }
            }
        )
    elif rr_source == "float":
        base_config.update(
            {
                "risk_effect.test_risk_on_cause.test_cause.incidence_rate": {
                    "data_sources": {"relative_risk": rr_value}
                }
            }
        )
    else:  # rr_source is a string because it gets read from RiskEffect's configuration defaults
        rr_data = build_dichotomous_risk_effect_data(rr_value)
        data[f"{risk.name}.relative_risk"] = rr_data

    base_config.update({"risk_factor.test_risk": {"distribution_type": "dichotomous"}})
    simulation = _setup_risk_effect_simulation(base_config, base_plugins, risk, effect, data)

    pop_idx = simulation.get_population_index()
    # We skip post processor here so cannot just use `simulation.get_population`
    rate = simulation._values.get_attribute("test_cause.incidence_rate")(
        pop_idx, mode="skip_post_processor"
    )
    assert set(rate.unique()) == {rr_value}


##############################
# Non Log-Linear Risk Effect #
##############################

custom_exposure_values = [0.5, 1, 1.5, 1.75, 2, 3, 4, 5, 5.5, 10]


class CustomExposureRisk(Risk):
    """Risk where we define the exposure manually."""

    def __init__(self, risk: str):
        super().__init__(risk)
        self.exposure_column_name = (
            f"{self.causal_factor.name}_exposure_for_non_loglinear_riskeffect"
        )

    def initialize_exposure(self, pop_data: SimulantData) -> None:
        exposure_col = pd.Series(custom_exposure_values, name=self.exposure_column_name)
        self.population_view.initialize(exposure_col)

    def on_time_step_prepare(self, event: Event) -> None:
        self.population_view.update(
            self.exposure_column_name,
            lambda _: pd.Series(custom_exposure_values, name=self.exposure_column_name),
        )

    # noinspection PyAttributeOutsideInit
    def setup(self, builder: Builder):
        self.distribution_type = None
        builder.value.register_attribute_producer(
            f"{self.causal_factor.name}.exposure", source=self.get_exposure
        )
        builder.population.register_initializer(
            initializer=self.initialize_exposure, columns=self.exposure_column_name
        )

    def get_exposure(self, index: pd.Index) -> pd.Series:
        data = pd.Series(custom_exposure_values, index=index)
        return data


@pytest.mark.parametrize(
    "rr_parameter_data, error_message",
    [
        ([1, 2, 5], None),
        ([2, 1, 5], "monotonic"),
        (["cat1", "cat2", "cat3"], "numeric"),
        (["per unit", "per unit", "per unit"], "numeric"),
    ],
)
def test_non_loglinear_effect(rr_parameter_data, error_message, base_config, base_plugins):
    risk = CustomExposureRisk("risk_factor.test_risk")
    effect = NonLogLinearRiskEffect(risk.name, "cause.test_cause.incidence_rate")

    risk_effect_rrs = [2.0, 2.4, 4.0]
    rr_data = pd.DataFrame(
        {
            "affected_entity": "test_cause",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "parameter": rr_parameter_data,
            "value": risk_effect_rrs,
        },
    )
    # enforce TMREL of 1
    tmred = {"distribution": "uniform", "min": 1, "max": 1, "inverted": False}

    data = {
        f"{risk.name}.relative_risk": rr_data,
        f"{risk.name}.tmred": tmred,
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }

    base_config.update({"population": {"population_size": 10}})

    if error_message:
        with pytest.raises(ValueError, match=error_message):
            simulation = _setup_risk_effect_simulation(
                base_config, base_plugins, risk, effect, data
            )
        return
    else:
        simulation = _setup_risk_effect_simulation(
            base_config, base_plugins, risk, effect, data
        )

    pop_idx = simulation.get_population_index()
    # We skip post processor here so cannot just use `simulation.get_population`
    rate = simulation._values.get_attribute("test_cause.incidence_rate")(
        pop_idx, mode="skip_post_processor"
    )
    expected_values = np.interp(
        custom_exposure_values,
        rr_parameter_data,
        np.array(risk_effect_rrs) / 2,  # RRs get divided by RR at TMREL
    )

    assert np.isclose(rate.values, expected_values, rtol=0.0000001).all()


def test_non_loglinear_effect_empty_rr_data(base_config, base_plugins):
    """Empty relative risk data (e.g. after filtering to the target entity
    and measure) should raise an intuitive error rather than failing
    downstream when building the lookup table."""
    risk = CustomExposureRisk("risk_factor.test_risk")
    effect = NonLogLinearRiskEffect(risk.name, "cause.test_cause.incidence_rate")

    # affected_entity does not match the target ("test_cause"), so filtering
    # to the target entity and measure leaves no rows.
    rr_data = pd.DataFrame(
        {
            "affected_entity": "some_other_cause",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "parameter": [1, 2, 5],
            "value": [2.0, 2.4, 4.0],
        },
    )
    # enforce TMREL of 1
    tmred = {"distribution": "uniform", "min": 1, "max": 1, "inverted": False}

    data = {
        f"{risk.name}.relative_risk": rr_data,
        f"{risk.name}.tmred": tmred,
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }

    base_config.update({"population": {"population_size": 10}})

    with pytest.raises(ValueError, match="empty"):
        _setup_risk_effect_simulation(base_config, base_plugins, risk, effect, data)


def _setup_non_loglinear_simulation(
    base_config: ConfigTree,
    base_plugins: ConfigTree,
    effect: NonLogLinearRiskEffect,
    rr_parameters: list[float],
    rr_values: list[float],
    tmrel: float,
) -> pd.Series:
    """Run a non-log-linear effect and return the resulting per-simulant rate.

    The target rate is 1 and the PAF is 0, so the returned rate is the
    relative risk each simulant's exposure resolves to.
    """
    risk = CustomExposureRisk("risk_factor.test_risk")
    rr_data = pd.DataFrame(
        {
            "affected_entity": "test_cause",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "parameter": rr_parameters,
            "value": rr_values,
        },
    )
    data = {
        f"{risk.name}.relative_risk": rr_data,
        f"{risk.name}.tmred": {
            "distribution": "uniform",
            "min": tmrel,
            "max": tmrel,
            "inverted": False,
        },
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }
    base_config.update({"population": {"population_size": len(custom_exposure_values)}})
    simulation = _setup_risk_effect_simulation(base_config, base_plugins, risk, effect, data)
    return simulation._values.get_attribute("test_cause.incidence_rate")(
        simulation.get_population_index(), mode="skip_post_processor"
    )


class UnboundedRiskEffect(NonLogLinearRiskEffect):
    """A ``NonLogLinearRiskEffect`` whose RRs may fall below 1, as
    ``HemoglobinRiskEffect`` needs."""

    MINIMUM_RELATIVE_RISK = None


@pytest.mark.parametrize("clips_relative_risk", [True, False])
def test_minimum_relative_risk_bounds_normalized_rrs(
    clips_relative_risk, base_config, base_plugins
):
    """``MINIMUM_RELATIVE_RISK`` is the only override needed to let RRs fall
    below 1, as risks that are protective above the TMREL require."""
    effect_class = NonLogLinearRiskEffect if clips_relative_risk else UnboundedRiskEffect
    # A TMREL at the top of the curve normalizes every RR to <= 1, so the
    # default clip flattens the whole curve to exactly 1.
    rr_parameters, rr_values = [1, 2, 5], [2.0, 2.4, 4.0]

    rate = _setup_non_loglinear_simulation(
        base_config,
        base_plugins,
        effect_class("risk_factor.test_risk", "cause.test_cause.incidence_rate"),
        rr_parameters=rr_parameters,
        rr_values=rr_values,
        tmrel=5,
    )

    if clips_relative_risk:
        assert (rate == 1.0).all()
    else:
        # Exposures below the TMREL stay protective rather than being clipped.
        assert (rate < 1.0).any()
        below_tmrel = np.array(custom_exposure_values) < 5
        assert np.isclose(
            rate[below_tmrel],
            np.interp(
                np.array(custom_exposure_values)[below_tmrel],
                rr_parameters,
                np.array(rr_values) / 4.0,  # RRs get divided by RR at TMREL
            ),
        ).all()


@pytest.mark.parametrize(
    "lowest_bin_left_rr, expected_rr_at_lowest_exposure",
    [
        # The lowest bin spans [0, 1) and its right RR is 1.0, so a simulant at
        # exposure 0.5 lands halfway between the chosen left RR and 1.0.
        (None, 0.9166667),  # default: min of the curve (0.8333)
        ("max", 1.3333333),  # as HemoglobinRiskEffect
        ("first", 1.0),  # as NeonatalSepsisHemoglobinRiskEffect
    ],
)
def test_get_lowest_bin_left_rr_hook(
    lowest_bin_left_rr, expected_rr_at_lowest_exposure, base_config, base_plugins
):
    """``get_lowest_bin_left_rr`` is the only override needed to change how the
    RR is extrapolated below the lowest exposure threshold."""

    class Effect(NonLogLinearRiskEffect):
        MINIMUM_RELATIVE_RISK = None  # isolate the hook from the RR clip

        def get_lowest_bin_left_rr(self, rr_values: pd.Series) -> float:
            if lowest_bin_left_rr == "max":
                return float(rr_values.max())
            if lowest_bin_left_rr == "first":
                return float(rr_values.iloc[0])
            return super().get_lowest_bin_left_rr(rr_values)

    # A non-monotonic RR curve, so min/max/first are three distinct values.
    # Normalized by RR at the TMREL of 1 (2.4), the curve is [1.0, 0.8333, 1.6667].
    rate = _setup_non_loglinear_simulation(
        base_config,
        base_plugins,
        Effect("risk_factor.test_risk", "cause.test_cause.incidence_rate"),
        rr_parameters=[1, 2, 5],
        rr_values=[2.4, 2.0, 4.0],
        tmrel=1,
    )

    assert custom_exposure_values[0] == 0.5  # in the lowest bin
    assert np.isclose(rate.iloc[0], expected_rr_at_lowest_exposure)


def test_non_loglinear_exposure_column_name(base_config, base_plugins):
    """The effect reads exposure from the same state table column the ``Risk``
    writes it to."""
    risk = Risk("risk_factor.test_risk")
    effect = NonLogLinearRiskEffect(risk.name, "cause.test_cause.incidence_rate")
    assert effect.exposure_column_name == risk.exposure_column_name


def test_relative_risk_pipeline(dichotomous_risk, base_config, base_plugins):
    risk = dichotomous_risk[0]
    effect = RiskEffect(risk.name, "cause.test_cause.incidence_rate")
    base_config.update({"risk_factor.test_risk": {"data_sources": {"exposure": 0.75}}})

    # TMREL of 1
    tmred = {"distribution": "uniform", "min": 1, "max": 1, "inverted": False}

    data = {
        f"{risk.name}.tmred": tmred,
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }
    rr_value = 1.4
    base_config.update(
        {
            "risk_effect.test_risk_on_cause.test_cause.incidence_rate": {
                "data_sources": {"relative_risk": rr_value}
            }
        }
    )

    base_config.update({"risk_factor.test_risk": {"distribution_type": "dichotomous"}})
    sim = _setup_risk_effect_simulation(base_config, base_plugins, risk, effect, data)
    pop_idx = sim.get_population_index()

    expected_pipeline = f"{effect.causal_factor.name}_on_{effect.target_name}.relative_risk"
    assert expected_pipeline in sim.get_attribute_names()

    rr_mapper = {
        "exposed": 1.4,
        "unexposed": 1.0,
    }
    for exposure in rr_mapper:
        exposures = sim.get_population(f"{effect.causal_factor.name}.exposure").squeeze()
        exposure_idx = exposures.loc[exposures == exposure].index
        relative_risk = sim.get_population(expected_pipeline).squeeze().loc[exposure_idx]
        assert (relative_risk == rr_mapper[exposure]).all()


def _loaded_artifact_keys(
    config: ConfigTree,
    plugins: ConfigTree,
    risk: Risk,
    risk_effect: RiskEffect,
    data: dict[str, Any],
    mocker,
) -> list[str]:
    """Set up a risk-effect simulation and return the artifact keys it loaded.

    Mirrors :func:`_setup_risk_effect_simulation` but spies on ``_data.load``,
    so a test can assert whether a given key was loaded from the artifact or
    supplied through a configuration data source.
    """
    simulation = InteractiveContext(
        components=[BasePopulation(), risk, SI("test_cause"), risk_effect],
        configuration=config,
        plugin_configuration=plugins,
        setup=False,
    )
    for key, value in data.items():
        simulation._data.write(key, value)
    load_spy = mocker.patch.object(simulation._data, "load", wraps=simulation._data.load)
    simulation.setup()
    return [call.args[0] for call in load_spy.call_args_list if call.args]


def test_continuous_effect_tmred_and_scalar_from_config(base_config, base_plugins, mocker):
    """A continuous ``RiskEffect`` sources ``tmred`` and ``relative_risk_scalar``
    from its config data sources without consulting the artifact.
    """
    risk = CustomExposureRisk("risk_factor.test_risk")
    effect = RiskEffect(risk.name, "cause.test_cause.incidence_rate")
    tmred_key = f"{risk.name}.tmred"
    scalar_key = f"{risk.name}.relative_risk_scalar"

    # CustomExposureRisk initializes exactly len(custom_exposure_values) simulants.
    base_config.update({"population": {"population_size": len(custom_exposure_values)}})
    base_config.update(
        {
            effect.name: {
                "data_sources": {
                    "relative_risk": 2.0,
                    "tmred": pd.DataFrame(
                        {"distribution": ["uniform"], "min": [1.0], "max": [1.0]}
                    ),
                    "relative_risk_scalar": 50.0,
                }
            }
        }
    )
    data = {
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }

    loaded_keys = _loaded_artifact_keys(base_config, base_plugins, risk, effect, data, mocker)

    assert tmred_key not in loaded_keys
    assert scalar_key not in loaded_keys


def test_dichotomous_effect_demographic_dimensions_from_config(
    dichotomous_risk, base_config, base_plugins, mocker
):
    """A dichotomous scalar-RR ``RiskEffect`` sources ``demographic_dimensions``
    from its config data source without consulting the artifact.
    """
    risk = dichotomous_risk[0]
    effect = RiskEffect(risk.name, "cause.test_cause.incidence_rate")
    grid_key = "population.demographic_dimensions"

    base_config.update(
        {
            "risk_factor.test_risk": {
                "data_sources": {"exposure": 1.0},
                "distribution_type": "dichotomous",
            }
        }
    )
    base_config.update(
        {
            effect.name: {
                "data_sources": {
                    "relative_risk": 2.5,
                    "demographic_dimensions": make_uniform_pop_data().drop(
                        columns=["location", "value"]
                    ),
                }
            }
        }
    )
    data = {
        f"{risk.name}.tmred": {"distribution": "uniform", "min": 1, "max": 1},
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }

    loaded_keys = _loaded_artifact_keys(base_config, base_plugins, risk, effect, data, mocker)

    assert grid_key not in loaded_keys


def test_non_loglinear_effect_tmred_from_config(base_config, base_plugins, mocker):
    """A ``NonLogLinearRiskEffect`` sources ``tmred`` from its config data source
    without consulting the artifact.
    """
    risk = CustomExposureRisk("risk_factor.test_risk")
    effect = NonLogLinearRiskEffect(risk.name, "cause.test_cause.incidence_rate")
    tmred_key = f"{risk.name}.tmred"

    rr_data = pd.DataFrame(
        {
            "affected_entity": "test_cause",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "parameter": [1, 2, 5],
            "value": [2.0, 2.4, 4.0],
        },
    )
    data = {
        f"{risk.name}.relative_risk": rr_data,
        f"{risk.name}.population_attributable_fraction": 0,
        "cause.test_cause.incidence_rate": 1,
    }
    base_config.update({"population": {"population_size": 10}})
    base_config.update(
        {
            effect.name: {
                "data_sources": {
                    "tmred": pd.DataFrame(
                        {"distribution": ["uniform"], "min": [1.0], "max": [1.0]}
                    )
                }
            }
        }
    )

    loaded_keys = _loaded_artifact_keys(base_config, base_plugins, risk, effect, data, mocker)

    assert tmred_key not in loaded_keys
