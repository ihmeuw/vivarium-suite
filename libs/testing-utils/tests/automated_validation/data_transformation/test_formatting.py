import pandas as pd
from pandas.testing import assert_frame_equal

from vivarium_testing_utils.automated_validation.data_transformation.formatting import (
    Deaths,
    RiskStatePersonTime,
    StatePersonTime,
    TotalPopulationPersonTime,
    TransitionCounts,
)


def get_expected_dataframe(value_1: float, value_2: float) -> pd.DataFrame:
    """Create an expected dataframe for testing."""
    return pd.DataFrame(
        {
            "value": [value_1, value_2],
        },
        index=pd.MultiIndex.from_tuples(
            [("A", "baseline"), ("B", "baseline")],
            names=["common_stratify_column", "scenario"],
        ),
    )


def test_transition_counts(transition_count_data: pd.DataFrame) -> None:
    """Test TransitionCounts formatting."""
    formatter = TransitionCounts("disease", "susceptible_to_disease", "disease")
    # assert formatter has right number of attrs
    assert len(formatter.__dict__) == 7
    assert formatter.measure == "transition_count"
    assert formatter.entity == "disease"
    assert formatter.raw_dataset_name == "transition_count_disease"
    assert formatter.filter_value == "susceptible_to_disease_to_disease"
    assert formatter.filters == {"sub_entity": ["susceptible_to_disease_to_disease"]}
    assert formatter.name == "susceptible_to_disease_to_disease_transition_count"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]

    expected_dataframe = pd.DataFrame(
        {
            "value": [1.0, 2.0, 2.0, 3.0],
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
                "tc_unique_stratification",
            ],
        ),
    )

    assert_frame_equal(formatter.format_dataset(transition_count_data), expected_dataframe)


def test_person_time(person_time_data: pd.DataFrame) -> None:
    """Test PersonTime formatting."""
    # Create a mock dataset
    formatter = StatePersonTime("disease", "disease")
    assert len(formatter.__dict__) == 7
    assert formatter.measure == "person_time"
    assert formatter.entity == "disease"
    assert formatter.raw_dataset_name == "person_time_disease"
    assert formatter.filters == {"sub_entity": ["disease"]}
    assert formatter.name == "disease_person_time"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]

    expected_dataframe = pd.DataFrame(
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

    assert_frame_equal(formatter.format_dataset(person_time_data), expected_dataframe)


def test_person_time_state_total(person_time_data: pd.DataFrame) -> None:
    """Test PersonTime formatting with total state."""
    formatter = StatePersonTime("disease")
    assert len(formatter.__dict__) == 7
    assert formatter.measure == "person_time"
    assert formatter.entity == "disease"
    assert formatter.raw_dataset_name == "person_time_disease"
    assert formatter.filters == {"sub_entity": ["total"]}
    assert formatter.name == "total_person_time"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]

    expected_dataframe = pd.DataFrame(
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

    assert_frame_equal(formatter.format_dataset(person_time_data), expected_dataframe)


def test_total_person_time(total_person_time_data: pd.DataFrame) -> None:
    """Test StatePersonTime formatter initialization with total."""
    formatter = StatePersonTime()

    assert formatter.measure == "person_time"
    assert formatter.entity == "total"
    assert formatter.raw_dataset_name == "person_time_total"
    assert formatter.name == "total_person_time"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]
    assert formatter.filters == {"sub_entity": ["total"]}

    expected_dataframe = pd.DataFrame(
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

    assert_frame_equal(formatter.format_dataset(total_person_time_data), expected_dataframe)


def test_total_population_person_time(total_person_time_data: pd.DataFrame) -> None:
    """Test TotalPopulationPersonTime formatter with scenario columns."""
    scenario_columns = ["scenario"]
    formatter = TotalPopulationPersonTime(scenario_columns)

    assert formatter.measure == "person_time"
    assert formatter.entity == "total"
    assert formatter.raw_dataset_name == "person_time_total"
    assert formatter.name == "total_population_person_time"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]
    assert formatter.filters == {"sub_entity": ["total"]}
    assert formatter.scenario_columns == ["scenario"]

    # Create test data with DRAW_INDEX, SEED_INDEX, and scenario columns

    # The formatter should sum over everything except DRAW_INDEX, SEED_INDEX, and scenario columns
    expected_dataframe = pd.DataFrame(
        {
            "value": [17.0 + 23.0 + 29.0 + 37.0],
        },
        index=pd.Index(
            ["baseline"],
            name="scenario",
        ),
    )

    assert_frame_equal(formatter.format_dataset(total_person_time_data), expected_dataframe)


def test_deaths_cause_specific(deaths_data: pd.DataFrame) -> None:
    """Test Deaths formatter with a specific cause."""
    formatter = Deaths("disease")

    assert formatter.measure == "deaths"
    assert formatter.raw_dataset_name == "deaths"
    assert formatter.filters == {"entity": ["disease"], "sub_entity": ["disease"]}
    assert formatter.name == "disease_deaths"
    assert formatter.unused_columns == ["measure", "entity_type"]

    assert_frame_equal(
        formatter.format_dataset(deaths_data), get_expected_dataframe(2.0, 4.0)
    )


def test_deaths_all_causes(deaths_data: pd.DataFrame) -> None:
    """Test Deaths formatter for all causes."""
    formatter = Deaths("all_causes")

    assert formatter.measure == "deaths"
    assert formatter.raw_dataset_name == "deaths"
    assert formatter.filters == {"entity": ["total"], "sub_entity": ["total"]}
    assert formatter.name == "total_deaths"
    assert formatter.unused_columns == ["measure", "entity_type"]

    assert_frame_equal(
        formatter.format_dataset(deaths_data), get_expected_dataframe(5.0, 9.0)
    )


def test_risk_state_person_time(risk_state_person_time_data: pd.DataFrame) -> None:
    """Test RiskStatePersonTime formatting without sum_all."""
    formatter = RiskStatePersonTime("child_stunting")

    assert formatter.entity == "child_stunting"
    assert formatter.raw_dataset_name == "person_time_child_stunting"
    assert formatter.sum_all == False
    assert formatter.name == "person_time"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]

    expected_dataframe = pd.DataFrame(
        {
            "value": [8.0, 12.0, 15.0, 20.0, 6.0, 10.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cat1", "A"),
                ("cat2", "A"),
                ("cat3", "A"),
                ("cat1", "B"),
                ("cat2", "B"),
                ("cat3", "B"),
            ],
            names=["parameter", "common_stratify_column"],
        ),
    )

    assert_frame_equal(
        formatter.format_dataset(risk_state_person_time_data), expected_dataframe
    )


def test_risk_state_person_time_sum_all(risk_state_person_time_data: pd.DataFrame) -> None:
    """Test RiskStatePersonTime formatting with sum_all=True."""
    formatter = RiskStatePersonTime("child_stunting", sum_all=True)

    assert formatter.entity == "child_stunting"
    assert formatter.raw_dataset_name == "person_time_child_stunting"
    assert formatter.sum_all == True
    assert formatter.name == "person_time_total"
    assert formatter.unused_columns == ["measure", "entity_type", "entity"]

    # With sum_all=True, each risk state gets the total person time for its stratification
    # Total for A = 8+12+15 = 35, Total for B = 20+6+10 = 36
    expected_dataframe = pd.DataFrame(
        {
            "value": [35.0, 35.0, 35.0, 36.0, 36.0, 36.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cat1", "A"),
                ("cat2", "A"),
                ("cat3", "A"),
                ("cat1", "B"),
                ("cat2", "B"),
                ("cat3", "B"),
            ],
            names=["parameter", "common_stratify_column"],
        ),
    )

    assert_frame_equal(
        formatter.format_dataset(risk_state_person_time_data), expected_dataframe
    )
