from typing import Any

import numpy as np
import pandas as pd
import pytest
import vivarium.risk_distributions as rd
from vivarium.config_tree import ConfigTree
from vivarium.engine import Component, InteractiveContext
from vivarium.engine.framework.engine import Builder
from vivarium.engine.framework.lookup import LookupTable
from vivarium.fuzzy_checker import FuzzyChecker

from tests.test_utilities import build_table_with_age
from vivarium.public_health.causal_factor.calibration_constant import (
    get_calibration_constant_pipeline_name,
)
from vivarium.public_health.causal_factor.distributions import (
    EnsembleDistribution,
    PolytomousDistribution,
    clip,
)
from vivarium.public_health.disease import SIS
from vivarium.public_health.population import BasePopulation
from vivarium.public_health.risks import RiskEffect
from vivarium.public_health.risks.base_risk import Risk
from vivarium.public_health.utilities import EntityString


def test_validate_entity_type():
    """Test that Risk only accepts valid entity types."""
    # Valid entity types should not raise
    Risk("risk_factor.test_risk")
    Risk("alternative_risk_factor.test_risk")

    # Invalid entity type should raise ValueError
    with pytest.raises(ValueError, match="Entity type must be one of"):
        Risk("cause.some_cause")

    with pytest.raises(ValueError, match="Entity type must be one of"):
        Risk("intervention.some_intervention")


@pytest.fixture
def polytomous_risk() -> tuple[Risk, dict[str, Any]]:
    risk = "risk_factor.test_risk"
    risk_data = {}
    exposure_data = build_table_with_age(
        0.25, value_columns=["cat1", "cat2", "cat3", "cat4"]
    ).melt(
        id_vars=("age_start", "age_end", "year_start", "year_end", "sex"),
        var_name="parameter",
        value_name="value",
    )

    risk_data[f"{risk}.exposure"] = exposure_data
    risk_data[f"{risk}.categories"] = {
        "cat1": "severe",
        "cat2": "moderate",
        "cat3": "mild",
        "cat4": "unexposed",
    }
    risk_data[f"{risk}.distribution"] = "ordered_polytomous"
    risk_data[f"{risk}.relative_risk"] = pd.DataFrame(
        {
            "parameter": ["cat1", "cat2", "cat3", "cat4"],
            "affected_entity": "some_disease",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "value": [1.5, 1.2, 1.1, 1.0],
        }
    )
    risk_data[f"{risk}.population_attributable_fraction"] = pd.DataFrame(
        {
            "affected_entity": "some_disease",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "value": 0.5,
        },
        index=[0],
    )
    return Risk(risk), risk_data


def _setup_risk_simulation(
    config: ConfigTree,
    plugins: ConfigTree,
    risk: str | Risk,
    data: dict[str, Any],
    has_risk_effect: bool = True,
) -> InteractiveContext:
    if isinstance(risk, str):
        risk = Risk(risk)
    components = [BasePopulation(), risk]
    if has_risk_effect:
        components.append(SIS("some_disease"))
        components.append(RiskEffect(risk.name, "cause.some_disease.incidence_rate"))

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


# @pytest.mark.parametrize('propensity', [0.00001, 0.5, 0.99])
# def test_propensity_effect(propensity, mocker, continuous_risk, base_config, base_plugins):
#     population_size = 1000
#
#     rf, risk_data = continuous_risk
#     base_config.update({'population': {'population_size': population_size}}, **metadata(__file__))
#     sim = initialize_simulation([BasePopulation(), rf], input_config=base_config, plugin_config=base_plugins)
#     for key, value in risk_data.items():
#         sim.data.write(f'risk_factor.test_risk.{key}', value)
#
#     sim.setup()
#     propensity_pipeline = mocker.Mock()
#     sim.values.register_value_producer('test_risk.propensity', source=propensity_pipeline)
#     propensity_pipeline.side_effect = lambda index: pd.Series(propensity, index=index)
#
#     expected_value = norm(loc=130, scale=15).ppf(propensity)
#
#     assert np.allclose(rf.exposure(sim.get_population().index), expected_value)
#
#
# def test_Risk_config_data(base_config, base_plugins):
#     exposure_level = 0.8  # default is one
#     dummy_risk = Risk("risk_factor.test_risk")
#     base_config.update({'test_risk': {'exposure': exposure_level}}, layer='override')
#
#     simulation = initialize_simulation([BasePopulation(), dummy_risk],
#                                        input_config=base_config, plugin_config=base_plugins)
#     simulation.setup()
#
#     # Make sure dummy exposure is being used
#     exp = simulation.values.get_value('test_risk.exposure')(simulation.get_population().index)
#     exposed_proportion = (exp == 'cat1').sum() / len(exp)
#     assert np.isclose(exposed_proportion, exposure_level, atol=0.005)  # population is 1000
#
#     # Make sure value was correctly pulled from config
#     sim_exposure_level = simulation.values.get_value('test_risk.exposure_parameters')(simulation.get_population().index)
#     assert np.all(sim_exposure_level == exposure_level)


def test_polytomous_risk_lookup_configuration(polytomous_risk, base_config, base_plugins):
    risk, risk_data = polytomous_risk

    _setup_risk_simulation(base_config, base_plugins, risk, risk_data, has_risk_effect=False)

    # We have to get the distribution component's lookup tables. This is the distribution class
    # instantiated by the sub_component of the risk class

    assert isinstance(risk.exposure_distribution, PolytomousDistribution)


def _check_exposure_and_rr(
    simulation: InteractiveContext,
    risk: EntityString,
    expected_exposures: dict[str, float],
    expected_rrs: dict[str, float],
    fuzzy_checker: FuzzyChecker,
    name_additional: str = "",
) -> None:
    population = simulation.get_population(
        [f"{risk.name}.exposure", "some_disease.incidence_rate"]
    )
    exposure = population[f"{risk.name}.exposure"]
    incidence_rate = population["some_disease.incidence_rate"]
    unexposed_category = sorted(expected_exposures.keys())[-1]
    unexposed_incidence = incidence_rate[exposure == unexposed_category].iat[0]

    for category, expected_exposure in expected_exposures.items():
        relative_risk = expected_rrs[category]
        is_in_category = exposure == category
        fuzzy_checker.assert_proportion(
            int(is_in_category.sum()),
            len(is_in_category),
            expected_exposure,
            name=f"{risk.name}.exposure.{category}",
            name_additional=name_additional,
        )
        # TODO: MIC-7279 - assert_proportion only warns (doesn't fail) when the sample
        # is too small to be conclusive; fail loudly.
        assert (
            fuzzy_checker.proportion_test_diagnostics[-1].confidence == "Conclusive"
        ), f"fuzzy check '{risk.name}.exposure.{category}' was inconclusive at this population size"

        actual_incidence_rates = incidence_rate[is_in_category]
        expected_incidence_rates = unexposed_incidence * relative_risk
        assert np.isclose(actual_incidence_rates, expected_incidence_rates).all()


def test_polytomous_risk(polytomous_risk, base_config, base_plugins, fuzzy_checker):
    risk, risk_data = polytomous_risk
    rr_data = risk_data[f"{risk.name}.relative_risk"].set_index("parameter")
    exposure_data = risk_data[f"{risk.name}.exposure"].groupby("parameter")["value"].mean()

    base_config.update({"population": {"population_size": 10000}})

    simulation = _setup_risk_simulation(base_config, base_plugins, risk, risk_data)

    _check_exposure_and_rr(
        simulation,
        risk.causal_factor,
        exposure_data.to_dict(),
        rr_data["value"].to_dict(),
        fuzzy_checker,
        name_additional="pre_step",
    )

    simulation.step()

    _check_exposure_and_rr(
        simulation,
        risk.causal_factor,
        exposure_data.to_dict(),
        rr_data["value"].to_dict(),
        fuzzy_checker,
        name_additional="post_step",
    )


@pytest.mark.parametrize("scalar_exposure", [True, False])
def test_dichotomous_risk(base_config, base_plugins, scalar_exposure, fuzzy_checker):
    risk = Risk("risk_factor.test_risk")
    rr_data = pd.DataFrame(
        {
            "affected_entity": "some_disease",
            "affected_measure": "incidence_rate",
            "year_start": 1990,
            "year_end": 1991,
            "value": [1.5, 1.0],
        },
        index=pd.Index(["cat1", "cat2"], name="parameter"),
    )

    data = {
        f"{risk.name}.exposure": pd.DataFrame(
            {
                "year_start": 1990,
                "year_end": 1991,
                "sex": ["Male"] * 2 + ["Female"] * 2,
                "parameter": ["cat1", "cat2"] * 2,
                "value": [0.25, 0.75] * 2,
            }
        ),
        f"{risk.name}.relative_risk": rr_data.reset_index(),
        f"{risk.name}.population_attributable_fraction": pd.DataFrame(
            {
                "affected_entity": "some_disease",
                "affected_measure": "incidence_rate",
                "year_start": 1990,
                "year_end": 1991,
                "value": 0.5,
            },
            index=[0],
        ),
    }

    data_sources = {"data_sources": {"exposure": 0.25}} if scalar_exposure else {}
    base_config.update(
        {
            "population": {"population_size": 10000},
            "risk_factor.test_risk": {
                **data_sources,
                **{"distribution_type": "dichotomous"},
            },
        }
    )
    category_exposures = {"exposed": 0.25, "unexposed": 0.75}
    category_rrs = {"exposed": 1.5, "unexposed": 1.0}

    simulation = _setup_risk_simulation(base_config, base_plugins, risk, data)

    _check_exposure_and_rr(
        simulation,
        risk.causal_factor,
        category_exposures,
        category_rrs,
        fuzzy_checker,
        name_additional="pre_step",
    )

    simulation.step()

    _check_exposure_and_rr(
        simulation,
        risk.causal_factor,
        category_exposures,
        category_rrs,
        fuzzy_checker,
        name_additional="post_step",
    )


@pytest.fixture(scope="module")
def ensemble_distribution_weights() -> dict[str, float]:
    """Distribution weights for the ensemble test risk (glnorm forced to zero)."""
    return {
        "betasr": 0.055,
        "exp": 0.06,
        "gamma": 0.065,
        "glnorm": 0,
        "gumbel": 0.07,
        "invgamma": 0.075,
        "invweibull": 0.8,
        "llogis": 0.085,
        "lnorm": 0.09,
        "mgamma": 0.095,
        "mgumbel": 0.1,
        "norm": 0.105,
        "weibull": 0.12,
    }


def _build_ensemble_distribution_sim(
    config, base_plugins, distribution_weights
) -> tuple[InteractiveContext, EnsembleDistribution]:
    """Build (not step) a simulation with an ensemble-distributed risk, returning it
    alongside its configured ``EnsembleDistribution`` component."""
    risk = Risk("risk_factor.test_risk")

    data = {
        f"{risk.name}.exposure": pd.DataFrame(
            {
                "year_start": 1990,
                "year_end": 1991,
                "parameter": "continuous",
                "value": 5.0,
            },
            index=[0],
        ),
        f"{risk.name}.exposure_standard_deviation": pd.DataFrame(
            {
                "year_start": 1990,
                "year_end": 1991,
                "value": 0.5,
            },
            index=[0],
        ),
        f"{risk.name}.exposure_distribution_weights": pd.DataFrame(
            {
                "year_start": 1990,
                "year_end": 1991,
                "parameter": list(distribution_weights.keys()),
                "value": list(distribution_weights.values()),
            },
        ),
        f"{risk.name}.population_attributable_fraction": pd.DataFrame(
            {
                "affected_entity": "some_disease",
                "affected_measure": "incidence_rate",
                "year_start": 1990,
                "year_end": 1991,
                "value": 0.5,
            },
            index=[0],
        ),
    }

    config.update(
        {
            "risk_factor.test_risk": {
                "data_sources": {"exposure": 0.25},
                "distribution_type": "ensemble",
                "ensemble_members": 2,
            },
            "risk_effect.test_risk_on_some_disease.incidence_rate": {
                "distribution_args": {"relative_risk": 1.5}
            },
        }
    )

    simulation = _setup_risk_simulation(
        config, base_plugins, risk, data, has_risk_effect=False
    )
    distribution = risk.exposure_distribution
    assert isinstance(distribution, EnsembleDistribution)
    return simulation, distribution


@pytest.fixture(scope="module")
def ensemble_distribution_sim(
    base_config_factory, base_plugins, ensemble_distribution_weights
) -> tuple[InteractiveContext, EnsembleDistribution]:
    """One ensemble-distributed-risk sim, built and stepped once, shared read-only
    across the ensemble tests. The build-and-step also serves as the end-to-end
    smoke test that an ensemble risk runs without error."""
    simulation, distribution = _build_ensemble_distribution_sim(
        base_config_factory(), base_plugins, ensemble_distribution_weights
    )
    simulation.step()
    return simulation, distribution


def test_ensemble_builds_single_consolidated_parameters_table(ensemble_distribution_sim):
    """The ensemble builds a single consolidated ``parameters_table`` LookupTable."""
    _, distribution = ensemble_distribution_sim

    assert isinstance(distribution.parameters_table, LookupTable)


def test_ensemble_parameter_columns_cover_all_distributions(
    ensemble_distribution_sim, ensemble_distribution_weights
):
    """``parameter_columns`` maps every non-glnorm ensemble distribution to its parameter column names."""
    _, distribution = ensemble_distribution_sim

    expected_distributions = set(ensemble_distribution_weights) - {"glnorm"}
    assert set(distribution.parameter_columns) == expected_distributions

    for columns in distribution.parameter_columns.values():
        assert isinstance(columns, list)
        assert len(columns) > 0


def test_consolidated_parameters_table_columns_namespaced_without_collision(
    ensemble_distribution_sim,
):
    """The consolidated ``parameters_table`` value columns namespace each distribution's parameters so names shared across distributions (e.g. ``x_min``/``x_max``) stay distinct and unique."""
    _, distribution = ensemble_distribution_sim

    value_columns = list(distribution.parameters_table.value_columns)

    # Namespacing must keep every distribution's parameter column distinct, so the
    # consolidated table holds exactly one column per (distribution, original-column)
    # pair with no collisions/collapses -- even though parameters such as x_min/x_max
    # are shared across every distribution.
    expected_count = sum(len(cols) for cols in distribution.parameter_columns.values())
    assert len(value_columns) == expected_count
    assert len(set(value_columns)) == len(value_columns)


def test_split_parameters_round_trips_to_per_distribution_frames(ensemble_distribution_sim):
    """``_split_parameters`` inverts the consolidation: for a looked-up frame it returns per-distribution frames keyed by distribution name with original (un-namespaced) columns."""
    simulation, distribution = ensemble_distribution_sim
    index = simulation.get_population(distribution.causal_factor_propensity).index

    looked_up = distribution.parameters_table(index)
    split = distribution._split_parameters(looked_up)

    assert set(split) == set(distribution.parameter_columns)
    for dist, frame in split.items():
        # Each split frame carries the distribution's original (un-namespaced) columns
        # and is aligned to the same simulant index.
        assert list(frame.columns) == distribution.parameter_columns[dist]
        assert frame.index.equals(index)


def test_ensemble_owns_distribution_weights_table(ensemble_distribution_sim):
    """The ensemble owns the distribution weights LookupTable."""
    _, distribution = ensemble_distribution_sim

    assert isinstance(distribution.distribution_weights_table, LookupTable)


def test_ensemble_exposure_ppf_matches_direct_ensemble_computation(ensemble_distribution_sim):
    """``exposure_ppf`` equals ``rd.EnsembleDistribution`` evaluated on the looked-up weights and the split consolidated parameters for the same propensities."""
    simulation, distribution = ensemble_distribution_sim
    index = simulation.get_population(distribution.causal_factor_propensity).index

    # Reproduce the documented composition of exposure_ppf to build an independent
    # oracle: clip the risk propensity, look up the ensemble weights, split the
    # consolidated parameter table, then evaluate the risk-distributions ensemble at
    # the clipped quantiles and the ensemble propensity, with null exposures -> 0.
    pop = distribution.population_view.get(
        index,
        [distribution.causal_factor_propensity, distribution.ensemble_propensity],
    )
    quantiles = clip(pop[distribution.causal_factor_propensity].copy())
    ensemble_propensity = pop[distribution.ensemble_propensity]

    weights = distribution.distribution_weights_table(quantiles.index)
    params = distribution._split_parameters(distribution.parameters_table(quantiles.index))
    expected = rd.EnsembleDistribution(weights, params).ppf(quantiles, ensemble_propensity)
    expected[expected.isnull()] = 0

    actual = distribution.exposure_ppf(index)

    np.testing.assert_allclose(
        np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
    )


def test_ensemble_exposure_ppf_returns_empty_for_empty_index(ensemble_distribution_sim):
    """``exposure_ppf`` returns an empty series when given an empty index."""
    _, distribution = ensemble_distribution_sim

    result = distribution.exposure_ppf(pd.Index([], dtype=int))

    assert isinstance(result, pd.Series)
    assert result.empty


def test_consolidate_split_round_trips_parameter_values():
    """Consolidating then splitting preserves each distribution's parameter columns and values, even across distributions that share parameter names (e.g. ``x_min``/``x_max``)."""
    idx = pd.RangeIndex(3)
    synthetic = {
        "norm": pd.DataFrame(
            {
                "loc": [1.0, 2.0, 3.0],
                "scale": [4.0, 5.0, 6.0],
                "x_min": [-1.0, -1.0, -1.0],
                "x_max": [9.0, 9.0, 9.0],
            },
            index=idx,
        ),
        "gamma": pd.DataFrame(
            {
                "a": [7.0, 8.0, 9.0],
                "scale": [1.0, 1.0, 1.0],
                "x_min": [0.0, 0.0, 0.0],
                "x_max": [5.0, 5.0, 5.0],
            },
            index=idx,
        ),
    }

    # The two helpers only depend on self.parameter_columns, which we set from the
    # consolidation result -- no simulation setup is required.
    distribution = EnsembleDistribution(EntityString("risk_factor.test_risk"))
    combined, cols = distribution._consolidate_parameter_tables(synthetic)
    distribution.parameter_columns = cols
    split = distribution._split_parameters(combined)

    assert set(split) == set(synthetic)
    for dist in synthetic:
        pd.testing.assert_frame_equal(split[dist], synthetic[dist])


class _CalibrationConstantModifier(Component):
    """Test helper that registers a calibration constant modifier on the
    exposure PPF pipeline to scale exposure values by (1 - calibration_value)."""

    def __init__(self, risk: str, calibration_value: float):
        super().__init__()
        self._risk = EntityString(risk)
        self._calibration_value = calibration_value

    def setup(self, builder: Builder) -> None:
        exposure_ppf_pipeline = f"{self._risk.name}.exposure_distribution.ppf"
        data = build_table_with_age(self._calibration_value)
        builder.value.register_value_modifier(
            get_calibration_constant_pipeline_name(exposure_ppf_pipeline),
            modifier=lambda: data,
        )


def test_risk_calibration_constant(base_config_factory, base_plugins):
    """Test that when a calibration constant modifier is registered on the
    exposure PPF pipeline, Risk exposures are scaled by (1 - calibration_value)."""
    population_size = 1000
    calibration_value = 0.75

    exposure_data = pd.DataFrame(
        {
            "year_start": 1990,
            "year_end": 1991,
            "parameter": "continuous",
            "value": 130.0,
        },
        index=[0],
    )
    exposure_sd_data = pd.DataFrame(
        {
            "year_start": 1990,
            "year_end": 1991,
            "value": 15.0,
        },
        index=[0],
    )

    data = {
        "risk_factor.test_risk.exposure": exposure_data,
        "risk_factor.test_risk.exposure_standard_deviation": exposure_sd_data,
    }

    config_updates = {
        "population": {"population_size": population_size},
        "risk_factor.test_risk": {"distribution_type": "normal"},
    }

    # Build a Risk simulation with no calibration constant modifier (baseline)
    config_base = base_config_factory()
    config_base.update(config_updates)
    base_risk = Risk("risk_factor.test_risk")
    sim_base = _setup_risk_simulation(
        config_base, base_plugins, base_risk, data, has_risk_effect=False
    )
    uncalibrated_exposure = sim_base.get_population(["test_risk.exposure"])[
        "test_risk.exposure"
    ]

    # Build a Risk simulation with a calibration constant modifier
    config_cal = base_config_factory()
    config_cal.update(config_updates)
    risk = Risk("risk_factor.test_risk")
    modifier = _CalibrationConstantModifier("risk_factor.test_risk", calibration_value)

    components = [BasePopulation(), risk, modifier]
    simulation = InteractiveContext(
        components=components,
        configuration=config_cal,
        plugin_configuration=base_plugins,
        setup=False,
    )
    for key, value in data.items():
        simulation._data.write(key, value)
    simulation.setup()

    calibrated_exposure = simulation.get_population(["test_risk.exposure"])[
        "test_risk.exposure"
    ]

    expected_exposure = uncalibrated_exposure * (1 - calibration_value)
    pd.testing.assert_series_equal(calibrated_exposure, expected_exposure, check_names=False)
