from collections import defaultdict

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from vivarium_testing_utils.automated_validation.constants import DRAW_INDEX
from vivarium_testing_utils.automated_validation.data_transformation.formatting import (
    TotalPopulationPersonTime,
)
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    CategoricalRelativeRisk,
    CauseSpecificMortalityRate,
    ExcessMortalityRate,
    Incidence,
    MeasureMapper,
    PopulationStructure,
    Prevalence,
    RatioMeasure,
    RiskExposure,
    SIRemission,
    _format_title,
)


def get_expected_dataframe(value_1: float, value_2: float) -> pd.DataFrame:
    """Create the expected dataframe by passing in two values to a reliable index."""
    return pd.DataFrame(
        {
            "value": [value_1, value_2],
        },
        index=pd.MultiIndex.from_tuples(
            [("A", "baseline"), ("B", "baseline")],
            names=["common_stratify_column", "scenario"],
        ),
    )


def test_incidence(
    transition_count_data: pd.DataFrame, person_time_data: pd.DataFrame
) -> None:
    """Test the Incidence measure."""
    cause = "disease"
    measure = Incidence(cause)
    assert measure.measure_key == f"cause.{cause}.incidence_rate"
    assert measure.title == "Disease Incidence Rate"
    assert measure.sim_output_datasets == {
        "numerator_data": f"transition_count_{cause}",
        "denominator_data": f"person_time_{cause}",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=transition_count_data,
        denominator_data=person_time_data,
    )
    assert ratio_datasets["numerator_data"].equals(get_expected_dataframe(3.0, 5.0))
    assert ratio_datasets["denominator_data"].equals(get_expected_dataframe(17.0, 29.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)

    measure_data = measure.get_measure_data_from_sim(
        numerator_data=transition_count_data, denominator_data=person_time_data
    )
    expected_measure_data = get_expected_dataframe(3 / 17.0, 5 / 29.0)

    assert measure_data.equals(expected_measure_data)
    assert measure_data_from_ratio.equals(expected_measure_data)


def test_prevalence(person_time_data: pd.DataFrame) -> None:
    """Test the Prevalence measure."""
    cause = "disease"
    measure = Prevalence(cause)
    assert measure.measure_key == f"cause.{cause}.prevalence"
    assert measure.title == "Disease Prevalence"
    assert measure.sim_output_datasets == {
        "numerator_data": f"person_time_{cause}",
        "denominator_data": f"person_time_{cause}",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=person_time_data,
        denominator_data=person_time_data,
    )
    expected_numerator = pd.DataFrame(
        {
            "value": [9.0, 14.0, 15.0, 22.0],
        },
        index=pd.MultiIndex.from_product(
            [
                ["A", "B"],
                ["baseline"],
                ["foo", "bar"],
            ],
            names=[
                "common_stratify_column",
                "scenario",
                "pt_unique_stratification",
            ],
        ),
    )

    assert ratio_datasets["numerator_data"].equals(expected_numerator)

    expected_denominator = pd.DataFrame(
        {
            "value": [7.0 + 9.0, 10.0 + 14.0, 12.0 + 15.0, 17.0 + 22.0],
        },
        index=pd.MultiIndex.from_product(
            [
                ["A", "B"],
                ["baseline"],
                ["foo", "bar"],
            ],
            names=[
                "common_stratify_column",
                "scenario",
                "pt_unique_stratification",
            ],
        ),
    )
    assert ratio_datasets["denominator_data"].equals(expected_denominator)

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)
    measure_data = measure.get_measure_data_from_sim(
        numerator_data=person_time_data, denominator_data=person_time_data
    )
    expected_measure_data = pd.DataFrame(
        {
            "value": [
                9.0 / (7.0 + 9.0),
                14.0 / (10.0 + 14.0),
                15.0 / (12.0 + 15.0),
                22.0 / (17.0 + 22.0),
            ],
        },
        index=pd.MultiIndex.from_product(
            [
                ["A", "B"],
                ["baseline"],
                ["foo", "bar"],
            ],
            names=[
                "common_stratify_column",
                "scenario",
                "pt_unique_stratification",
            ],
        ),
    )

    assert measure_data.equals(expected_measure_data)
    assert measure_data_from_ratio.equals(expected_measure_data)


def test_si_remission(
    transition_count_data: pd.DataFrame, person_time_data: pd.DataFrame
) -> None:
    """Test the SIRemission measure."""
    cause = "disease"
    measure = SIRemission(cause)
    assert measure.measure_key == f"cause.{cause}.remission_rate"
    assert measure.title == "Disease Remission Rate"
    assert measure.sim_output_datasets == {
        "numerator_data": f"transition_count_{cause}",
        "denominator_data": f"person_time_{cause}",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=transition_count_data,
        denominator_data=person_time_data,
    )

    assert ratio_datasets["numerator_data"].equals(get_expected_dataframe(7.0, 13.0))
    assert ratio_datasets["denominator_data"].equals(get_expected_dataframe(23.0, 37.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)
    measure_data = measure.get_measure_data_from_sim(
        numerator_data=transition_count_data, denominator_data=person_time_data
    )
    expected_measure_data = get_expected_dataframe(7.0 / 23.0, 13.0 / 37.0)
    assert measure_data.equals(expected_measure_data)
    assert measure_data_from_ratio.equals(expected_measure_data)


def test_all_cause_mortality_rate(
    deaths_data: pd.DataFrame, total_person_time_data: pd.DataFrame
) -> None:
    """Test the CauseMortalityRate measurefor all causes."""
    measure = CauseSpecificMortalityRate("all_causes")
    assert measure.measure_key == "cause.all_causes.cause_specific_mortality_rate"
    assert measure.title == "All Causes Cause Specific Mortality Rate"
    assert measure.sim_output_datasets == {
        "numerator_data": "deaths",
        "denominator_data": "person_time_total",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=deaths_data,
        denominator_data=total_person_time_data,
    )

    # Expected dataframe for the numerator and denominator data
    # The Deaths formatter with no cause will marginalize over entity and sub_entity
    # to get total deaths by stratify_column
    assert_frame_equal(ratio_datasets["numerator_data"], get_expected_dataframe(5.0, 9.0))
    assert_frame_equal(ratio_datasets["denominator_data"], get_expected_dataframe(40.0, 66.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)
    measure_data = measure.get_measure_data_from_sim(
        numerator_data=deaths_data, denominator_data=total_person_time_data
    )

    expected_measure_data = get_expected_dataframe(5.0 / 40.0, 9.0 / 66.0)
    assert_frame_equal(measure_data, expected_measure_data)
    assert_frame_equal(measure_data_from_ratio, expected_measure_data)


def test_cause_specific_mortality_rate(
    deaths_data: pd.DataFrame,
    total_person_time_data: pd.DataFrame,
) -> None:
    """Test the CauseSpecificMortalityRate measure."""
    cause = "disease"
    measure = CauseSpecificMortalityRate(cause)
    assert measure.measure_key == f"cause.{cause}.cause_specific_mortality_rate"
    assert measure.title == "Disease Cause Specific Mortality Rate"
    assert measure.sim_output_datasets == {
        "numerator_data": f"deaths",
        "denominator_data": "person_time_total",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=deaths_data,
        denominator_data=total_person_time_data,
    )

    # Expected dataframe for the numerator and denominator data
    # The Deaths formatter with a specific cause will filter for that cause
    # The TotalPersonTime formatter will marginalize person_time over all states
    assert_frame_equal(ratio_datasets["numerator_data"], get_expected_dataframe(2.0, 4.0))
    assert_frame_equal(ratio_datasets["denominator_data"], get_expected_dataframe(40.0, 66.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)
    measure_data = measure.get_measure_data_from_sim(
        numerator_data=deaths_data, denominator_data=total_person_time_data
    )

    expected_measure_data = get_expected_dataframe(2.0 / 40.0, 4.0 / 66.0)
    assert_frame_equal(measure_data, expected_measure_data)
    assert_frame_equal(measure_data_from_ratio, expected_measure_data)


def test_excess_mortality_rate(
    deaths_data: pd.DataFrame, person_time_data: pd.DataFrame
) -> None:
    """Test the ExcessMortalityRate measure."""
    cause = "disease"
    measure = ExcessMortalityRate(cause)
    assert measure.measure_key == f"cause.{cause}.excess_mortality_rate"
    assert measure.title == "Disease Excess Mortality Rate"
    assert measure.sim_output_datasets == {
        "numerator_data": f"deaths",
        "denominator_data": f"person_time_{cause}",
    }

    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=deaths_data,
        denominator_data=person_time_data,
    )

    # Expected dataframe for the numerator and denominator data
    # The Deaths formatter with a specific cause will filter for that cause
    # The PersonTime formatter with a specific state will filter for that state
    assert_frame_equal(ratio_datasets["numerator_data"], get_expected_dataframe(2.0, 4.0))
    assert_frame_equal(ratio_datasets["denominator_data"], get_expected_dataframe(23.0, 37.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)

    measure_data = measure.get_measure_data_from_sim(
        numerator_data=deaths_data, denominator_data=person_time_data
    )

    expected_measure_data = get_expected_dataframe(2.0 / 23.0, 4.0 / 37.0)
    assert_frame_equal(measure_data, expected_measure_data)
    assert_frame_equal(measure_data_from_ratio, expected_measure_data)


def test_risk_exposure(risk_state_person_time_data: pd.DataFrame) -> None:
    """Test the RiskExposure measure."""
    risk_factor = "child_stunting"
    measure = RiskExposure(risk_factor)
    assert measure.measure_key == f"risk_factor.{risk_factor}.exposure"
    assert measure.title == "Child Stunting Exposure"
    assert measure.sim_output_datasets == {
        "numerator_data": f"person_time_{risk_factor}",
        "denominator_data": f"person_time_{risk_factor}",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=risk_state_person_time_data,
        denominator_data=risk_state_person_time_data,
    )

    # Expected ratio data:
    # Numerator: person time in each specific risk state (cat1, cat2, cat3)
    # Denominator: total person time across all risk states for each stratification
    # Total person time per stratification: A = 8+12+15 = 35, B = 20+6+10 = 36
    expected_index = pd.MultiIndex.from_tuples(
        [
            ("cat1", "A"),
            ("cat2", "A"),
            ("cat3", "A"),
            ("cat1", "B"),
            ("cat2", "B"),
            ("cat3", "B"),
        ],
        names=["parameter", "common_stratify_column"],
    )
    expected_numerator_data = pd.DataFrame(
        {
            "value": [8.0, 12.0, 15.0, 20.0, 6.0, 10.0],
        },
        index=expected_index,
    )
    expected_denominator_data = pd.DataFrame(
        {
            "value": [35.0, 35.0, 35.0, 36.0, 36.0, 36.0],
        },
        index=expected_index,
    )

    assert_frame_equal(ratio_datasets["numerator_data"], expected_numerator_data)
    assert_frame_equal(ratio_datasets["denominator_data"], expected_denominator_data)

    measure_data = measure.get_measure_data_from_sim(
        numerator_data=risk_state_person_time_data,
        denominator_data=risk_state_person_time_data,
    )

    expected_measure_data = pd.DataFrame(
        {
            "value": [
                8.0 / 35.0,
                12.0 / 35.0,
                15.0 / 35.0,
                20.0 / 36.0,
                6.0 / 36.0,
                10.0 / 36.0,
            ]
        },
        index=expected_index,
    )
    assert_frame_equal(measure_data, expected_measure_data)


def test_population_structure(person_time_data: pd.DataFrame) -> None:
    """Test the PopulationStructure measure."""
    scenario_columns = ["scenario"]
    measure = PopulationStructure(scenario_columns)

    assert measure.measure_key == "population.structure"
    assert measure.title == "Population Structure"
    assert measure.sim_output_datasets == {
        "numerator_data": "person_time_total",
        "denominator_data": "person_time_total",
    }
    assert measure.sim_input_datasets == {"data": measure.measure_key}

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=person_time_data,
        denominator_data=person_time_data,
    )
    expected_numerator_data = pd.DataFrame(
        {
            "value": [16.0, 24.0, 27.0, 39.0],
        },
        index=pd.MultiIndex.from_product(
            [
                ["A", "B"],
                ["baseline"],
                ["foo", "bar"],
            ],
            names=[
                "common_stratify_column",
                "scenario",
                "pt_unique_stratification",
            ],
        ),
    )

    expected_denominator_data = pd.DataFrame(
        {
            "value": [17.0 + 23.0 + 29.0 + 37.0],
        },
        index=pd.Index(
            ["baseline"],
            name="scenario",
        ),
    )

    assert_frame_equal(ratio_datasets["numerator_data"], expected_numerator_data)
    assert_frame_equal(ratio_datasets["denominator_data"], expected_denominator_data)

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)
    measure_data = measure.get_measure_data_from_sim(
        numerator_data=person_time_data, denominator_data=person_time_data
    )

    expected_measure_data = pd.DataFrame(
        {
            "value": [
                16.0 / (17.0 + 23.0 + 29.0 + 37.0),
                24.0 / (17.0 + 23.0 + 29.0 + 37.0),
                27.0 / (17.0 + 23.0 + 29.0 + 37.0),
                39.0 / (17.0 + 23.0 + 29.0 + 37.0),
            ],
        },
        index=pd.MultiIndex.from_product(
            [
                ["A", "B"],
                ["baseline"],
                ["foo", "bar"],
            ],
            names=[
                "common_stratify_column",
                "scenario",
                "pt_unique_stratification",
            ],
        ),
    )
    assert_frame_equal(measure_data, expected_measure_data)
    assert_frame_equal(measure_data_from_ratio, expected_measure_data)


@pytest.mark.parametrize("use_base_categories", [True, False])
def test_categorical_relative_risk(
    deaths_data: pd.DataFrame,
    person_time_data: pd.DataFrame,
    artifact_relative_risk: pd.DataFrame,
    artifact_excess_mortality_rate: pd.DataFrame,
    risk_categories: dict[str, str],
    use_base_categories: bool,
) -> None:
    """Test the CategoricalRelativeRisk measure."""
    risk_factor = "risky_risk"
    affected_entity = "disease"
    measure = CategoricalRelativeRisk(
        risk_factor=risk_factor,
        affected_entity="disease",
        affected_measure="excess_mortality_rate",
        risk_stratification_column="common_stratify_column",
        risk_category_mapping={"cat1": "A", "cat2": "C"} if not use_base_categories else None,
    )
    assert (
        measure.measure_key
        == f"risk_factor.{risk_factor}.relative_risk.{affected_entity}.excess_mortality_rate"
    )
    assert measure.entity == risk_factor
    assert measure.title == "Effect of Risky Risk on Disease Excess Mortality Rate"
    assert measure.affected_entity == affected_entity
    assert measure.affected_measure_name == "excess_mortality_rate"
    assert measure.sim_output_datasets == {
        "numerator_data": "deaths",
        "denominator_data": f"person_time_{affected_entity}",
    }
    assert measure.sim_input_datasets == {
        "relative_risks": f"risk_factor.{risk_factor}.relative_risk",
        "affected_measure_data": f"cause.{affected_entity}.excess_mortality_rate",
        "categories": f"risk_factor.{risk_factor}.categories",
    }

    artifact_data = measure.get_measure_data_from_sim_inputs(
        relative_risks=artifact_relative_risk,
        affected_measure_data=artifact_excess_mortality_rate,
        categories=risk_categories,
    )
    if use_base_categories:
        index_tuples = [
            ("B", 0, "high"),
            ("B", 1, "high"),
            ("D", 0, "medium"),
            ("D", 1, "medium"),
        ]
    else:
        index_tuples = [
            ("B", 0, "A"),
            ("B", 1, "A"),
            ("D", 0, "C"),
            ("D", 1, "C"),
        ]
    expected_artifact_data = pd.DataFrame(
        {
            "value": [1.5 * 0.02, 2.0 * 0.03, 1.8 * 0.01, 1.2 * 0.04],
        },
        index=pd.MultiIndex.from_tuples(
            index_tuples,
            names=["other_stratify_column", DRAW_INDEX, "common_stratify_column"],
        ),
    )

    assert_frame_equal(artifact_data, expected_artifact_data)

    ratio_datasets = measure.get_ratio_datasets_from_sim(
        numerator_data=deaths_data,
        denominator_data=person_time_data,
    )
    assert_frame_equal(ratio_datasets["numerator_data"], get_expected_dataframe(2.0, 4.0))
    assert_frame_equal(ratio_datasets["denominator_data"], get_expected_dataframe(23.0, 37.0))

    measure_data_from_ratio = measure.get_measure_data_from_ratio(**ratio_datasets)

    measure_data = measure.get_measure_data_from_sim(
        numerator_data=deaths_data, denominator_data=person_time_data
    )

    expected_measure_data = get_expected_dataframe(2.0 / 23.0, 4.0 / 37.0)
    assert_frame_equal(measure_data, expected_measure_data)
    assert_frame_equal(measure_data_from_ratio, expected_measure_data)


@pytest.mark.parametrize(
    "measure_key,expected_class",
    [
        ("cause.heart_disease.incidence_rate", Incidence),
        ("cause.diabetes.prevalence", Prevalence),
        ("cause.tuberculosis.remission_rate", SIRemission),
        ("cause.cancer.cause_specific_mortality_rate", CauseSpecificMortalityRate),
        ("cause.stroke.excess_mortality_rate", ExcessMortalityRate),
        ("risk_factor.child_wasting.exposure", RiskExposure),
        ("population.structure", PopulationStructure),
    ],
)
def test_get_measure_from_key(measure_key: str, expected_class: type[RatioMeasure]) -> None:
    """Test get_measure_from_key for 3-part measure keys."""
    scenario_columns = ["scenario"]

    mapper = MeasureMapper()
    measure = mapper.get_measure_from_key(measure_key, scenario_columns)
    assert isinstance(measure, expected_class)
    assert measure.measure_key == measure_key
    if measure_key == "population.structure":
        assert isinstance(measure.denominator, TotalPopulationPersonTime)
        assert measure.denominator.scenario_columns == scenario_columns


@pytest.mark.parametrize(
    "invalid_key,expected_error",
    [
        ("invalid", ValueError),
        ("too.many.parts.here", ValueError),
        ("", ValueError),
        ("invalid_entity.something.measure", KeyError),
        ("cause.heart_disease.invalid_measure", KeyError),
        ("risk_factor.child_wasting.invalid_measure", KeyError),
        ("population.invalid_measure", KeyError),
    ],
)
def test_get_measure_from_key_invalid_inputs(
    invalid_key: str, expected_error: type[Exception]
) -> None:
    """Test get_measure_from_key with invalid inputs."""
    scenario_columns = ["scenario"]
    mapper = MeasureMapper()

    with pytest.raises(expected_error):
        mapper.get_measure_from_key(invalid_key, scenario_columns)


def test_format_title() -> None:
    assert _format_title("measure_type.measure.entity") == "Measure Entity"
    assert (
        _format_title("measure_type.measure.compound_name_example")
        == "Measure Compound Name Example"
    )
    assert _format_title("measure.entity") == "Measure Entity"


@pytest.mark.parametrize(
    "measure_class,measure_args,expected_weights_config,expected_description",
    [
        (
            Incidence,
            ("disease",),
            {
                "population": "population.structure",
                "prevalence": "cause.disease.prevalence",
            },
            "Person-time × (1 - prevalence) weighted average",
        ),
        (
            Prevalence,
            ("disease",),
            {"population": "population.structure"},
            "Population-weighted average",
        ),
        (
            SIRemission,
            ("disease",),
            {
                "population": "population.structure",
                "prevalence": "cause.disease.prevalence",
            },
            "Person-time × prevalence weighted average",
        ),
        (
            CauseSpecificMortalityRate,
            ("disease",),
            {"population": "population.structure"},
            "Population-weighted average",
        ),
        (
            ExcessMortalityRate,
            ("disease",),
            {
                "population": "population.structure",
                "prevalence": "cause.disease.prevalence",
            },
            "Person-time × prevalence weighted average",
        ),
        (
            RiskExposure,
            ("child_stunting",),
            {"population": "population.structure"},
            "Population-weighted average",
        ),
        (
            CategoricalRelativeRisk,
            (
                "risky_risk",
                "disease",
                "excess_mortality_rate",
                "common_stratify_column",
                None,
            ),
            {
                "population": "population.structure",
                "prevalence": "cause.disease.prevalence",
            },
            "Person-time × prevalence weighted average",
        ),
        (
            PopulationStructure,
            (["scenario"],),
            None,  # Not used since it raises NotImplementedError
            None,  # Not used since it raises NotImplementedError
        ),
    ],
)
def test_rate_aggregation_weights(
    measure_class: type[RatioMeasure],
    measure_args: tuple[str],
    expected_weights_config: dict[str, str] | None,
    expected_description: str | None,
) -> None:
    """Test the rate_aggregation_weights property of various RatioMeasure subclasses."""
    # Create the measure instance
    measure = measure_class(*measure_args)  # type: ignore[call-arg]

    if isinstance(measure, PopulationStructure):
        # Test that PopulationStructure raises NotImplementedError
        with pytest.raises(NotImplementedError):
            _ = measure.rate_aggregation_weights
        return

    assert expected_weights_config is not None
    assert expected_description is not None
    # Get the rate aggregation weights
    rate_agg_weights = measure.rate_aggregation_weights
    # Verify the configuration
    assert rate_agg_weights.weight_keys == expected_weights_config
    assert rate_agg_weights.description == expected_description

    # Create test data matching expected format
    test_index = pd.MultiIndex.from_tuples(
        [("A", "baseline"), ("B", "baseline")], names=["common_stratify_column", "scenario"]
    )
    # Population structure data (proportions summing to 1)
    population_data = get_expected_dataframe(0.6, 0.4)
    # Mock data from artifact
    key_data = get_expected_dataframe(0.1, 0.2)

    if len(rate_agg_weights.weight_keys) > 1:
        weights = rate_agg_weights.get_weights(population_data, key_data)
    else:
        weights = rate_agg_weights.get_weights(population_data)

    # Expected calculation depends on the measure type
    if "prevalence" in expected_weights_config:
        if "1 - prevalence" in expected_description:
            # Incidence: population * (1 - prevalence)
            expected_weights = pd.DataFrame(
                {"value": [0.6 * (1 - 0.1), 0.4 * (1 - 0.2)]},
                index=test_index,  # [0.54, 0.32]
            )
        else:
            # SIRemission and ExcessMortalityRate: population * prevalence
            expected_weights = pd.DataFrame(
                {"value": [0.6 * 0.1, 0.4 * 0.2]}, index=test_index  # [0.06, 0.08]
            )
    else:
        # Population weighted measures: just population
        expected_weights = population_data

    pd.testing.assert_frame_equal(weights, expected_weights)
