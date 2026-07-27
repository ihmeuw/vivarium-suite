import pandas as pd
import pytest
from vivarium.config_tree import ConfigTree

from vivarium.public_health.population import BasePopulation
from vivarium.public_health.results.stratification import ResultsStratifier


# Age bins prior to get_age_bins
def fake_data_load_population_age_bins(*args):
    AGE_BINS_RAW_DICT = {
        "age_start": {0: 0.0, 1: 0.01917808, 2: 0.07671233, 3: 0.5, 4: 1.0, 5: 2.0},
        "age_end": {0: 0.01917808, 1: 0.07671233, 2: 0.5, 3: 1.0, 4: 2.0, 5: 5.0},
        "age_group_name": {
            0: "Early Neonatal",
            1: "Late Neonatal",
            2: "1-5 months",
            3: "6-11 months",
            4: "12 to 23 months",
            5: "2 to 4",
        },
        "age_group_id": {0: 2, 1: 3, 2: 388, 3: 389, 4: 238, 5: 34},
    }
    return pd.DataFrame(AGE_BINS_RAW_DICT)


# Age bins as processed by get_age_bins
AGE_BINS_EXPECTED_DICT = {
    "age_start": {0: 0.0, 1: 0.01917808, 2: 0.07671233, 3: 0.5, 4: 1.0, 5: 2.0},
    "age_end": {0: 0.01917808, 1: 0.07671233, 2: 0.5, 3: 1.0, 4: 2.0, 5: 5.0},
    "age_group_name": {
        0: "early_neonatal",
        1: "late_neonatal",
        2: "1-5_months",
        3: "6-11_months",
        4: "12_to_23_months",
        5: "2_to_4",
    },
    "age_group_id": {0: 2, 1: 3, 2: 388, 3: 389, 4: 238, 5: 34},
}

# Population table for mapper testing
FAKE_POP_AGE_DICT = {
    "age": {0: 0.01, 1: 0.45, 2: 1.01, 3: 1.99, 4: 2.02},
}

# Series of expected age_bin intervals for mapper testing
FAKE_POP_AGE_GROUP_EXPECTED_SERIES = pd.Series(
    {
        0: "early_neonatal",
        1: "1-5_months",
        2: "12_to_23_months",
        3: "12_to_23_months",
        4: "2_to_4",
    },
    name="age_group",
)

FAKE_POP_EVENT_TIME = {
    "year": {
        0: pd.to_datetime("1/1/2045"),
        1: pd.to_datetime("1/1/2045"),
        2: pd.to_datetime("1/1/2045"),
        3: pd.to_datetime("1/1/2045"),
        4: pd.to_datetime("1/1/2045"),
    },
}


def _mock_age_bins_builder(mocker, *, initialization_age_min, untracking_age):
    """Build a mock builder carrying the population age range and simulation years."""
    builder = mocker.Mock()
    builder.configuration.population.initialization_age_min = initialization_age_min
    builder.configuration.population.untracking_age = untracking_age
    builder.configuration.time.start.year = 2022
    builder.configuration.time.end.year = 2025
    return builder


def test_results_stratifier_register_stratifications(mocker):
    """Test that ResultsStratifier.register_stratifications registers expected stratifications
    and only the expected stratifications."""
    builder = _mock_age_bins_builder(mocker, initialization_age_min=0.0, untracking_age=5.0)
    builder.data.load = fake_data_load_population_age_bins
    years_list = ["2022", "2023", "2024", "2025"]
    age_group_names_list = [
        "early_neonatal",
        "late_neonatal",
        "1-5_months",
        "6-11_months",
        "12_to_23_months",
        "2_to_4",
    ]
    mocker.patch.object(builder, "results.register_stratification")
    builder.results.register_stratification = mocker.MagicMock()
    rs = ResultsStratifier()
    # Override the (inherit-from-population) default with the artifact key directly
    # so the mocked builder.data.load is exercised without a population config.
    rs.configuration = ConfigTree({"data_sources": {"age_bins": "population.age_bins"}})

    builder.results.register_stratification.assert_not_called()

    rs.setup(builder)  # setup calls register_stratifications()

    builder.results.register_stratification.assert_any_call(
        "age_group",
        age_group_names_list,
        mapper=rs.map_age_groups,
        is_vectorized=True,
        requires_attributes=["age"],
    )
    builder.results.register_stratification.assert_any_call(
        "current_year",
        years_list,
        mapper=rs.map_year,
        is_vectorized=True,
        requires_attributes=["current_time"],
    )
    builder.results.register_stratification.assert_any_call(
        "event_year",
        years_list + [str(int(years_list[-1]) + 1)],
        excluded_categories=[str(int(years_list[-1]) + 1)],
        mapper=rs.map_year,
        is_vectorized=True,
        requires_attributes=["event_time"],
    )
    # builder.results.register_stratification.assert_any_call(
    #     "entrance_year",
    #     years_list,
    #     mapper=rs.map_year,
    #     is_vectorized=True,
    #     requires_attributes=["entrance_time"],
    # )
    # TODO [MIC-4803]: Known bug with this registration
    # builder.results.register_stratification.assert_any_call(
    #     "exit_year",
    #     years_list + ["nan"],
    #     mapper=rs.map_year,
    #     is_vectorized=True,
    #     requires_attributes=["exit_time"],
    # )
    builder.results.register_stratification.assert_any_call(
        "sex", ["Female", "Male"], requires_attributes=["sex"]
    )
    assert builder.results.register_stratification.call_count == 4


def test_results_stratifier_map_age_groups():
    """Test that ages of the population are mapped to intervals as expected."""
    pop = pd.DataFrame(FAKE_POP_AGE_DICT)
    rs = ResultsStratifier()
    rs.age_bins = pd.DataFrame(AGE_BINS_EXPECTED_DICT)
    mapped_pop = rs.map_age_groups(pop)
    pd.testing.assert_series_equal(
        mapped_pop,
        FAKE_POP_AGE_GROUP_EXPECTED_SERIES,
        check_dtype=False,
        check_categorical=False,
    )


def test_results_stratifier_map_year():
    """Test that datetimes are mapped to the correct year."""
    pop = pd.DataFrame(FAKE_POP_EVENT_TIME)
    rs = ResultsStratifier()
    the_year = rs.map_year(pop)
    assert (the_year == "2045").all()


def test_results_stratifier_get_age_bins(mocker):
    """Test that get_age_bins produces expected age_bins DataFrame."""
    builder = _mock_age_bins_builder(mocker, initialization_age_min=0.0, untracking_age=5.0)
    builder.data.load = fake_data_load_population_age_bins

    rs = ResultsStratifier()
    rs.configuration = ConfigTree({"data_sources": {"age_bins": "population.age_bins"}})
    age_bins = rs.get_age_bins(builder)

    assert age_bins.equals(pd.DataFrame(AGE_BINS_EXPECTED_DICT))


def test_results_stratifier_default_age_bins_source(mocker):
    """The default age_bins source resolves through the population component."""
    builder = _mock_age_bins_builder(mocker, initialization_age_min=0.0, untracking_age=5.0)
    builder.data.load = fake_data_load_population_age_bins
    builder.configuration.population.age_bins = "population.age_bins"
    builder.components.get_components_by_type.return_value = [BasePopulation()]

    rs = ResultsStratifier()
    rs.configuration = ConfigTree(rs.configuration_defaults[rs.name])

    age_bins = rs.get_age_bins(builder)

    builder.components.get_components_by_type.assert_called_once_with(BasePopulation)
    assert age_bins.equals(pd.DataFrame(AGE_BINS_EXPECTED_DICT))


@pytest.mark.parametrize(
    "age_bins_source",
    [fake_data_load_population_age_bins(), fake_data_load_population_age_bins],
    ids=["dataframe", "callable"],
)
def test_results_stratifier_get_age_bins_from_config(mocker, age_bins_source):
    """A config-supplied DataFrame or callable is used as the age_bins source (no artifact load)."""
    builder = _mock_age_bins_builder(mocker, initialization_age_min=0.0, untracking_age=5.0)
    builder.data.load = mocker.Mock()

    rs = ResultsStratifier()
    rs.configuration = ConfigTree({"data_sources": {"age_bins": age_bins_source}})

    age_bins = rs.get_age_bins(builder)

    builder.data.load.assert_not_called()
    assert age_bins.equals(pd.DataFrame(AGE_BINS_EXPECTED_DICT))
