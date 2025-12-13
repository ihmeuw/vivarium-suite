from collections.abc import Collection
from pathlib import Path
from typing import Literal
from unittest import mock

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pytest_check import check
from pytest_mock import MockFixture
from vivarium_inputs import interface

from tests.automated_validation.conftest import IS_ON_SLURM
from vivarium_testing_utils.automated_validation.bundle import RatioMeasureDataBundle
from vivarium_testing_utils.automated_validation.comparison import FuzzyComparison
from vivarium_testing_utils.automated_validation.constants import (
    DRAW_INDEX,
    INPUT_DATA_INDEX_NAMES,
    SEED_INDEX,
    DataSource,
)
from vivarium_testing_utils.automated_validation.data_loader import DataLoader
from vivarium_testing_utils.automated_validation.data_transformation import age_groups
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    Incidence,
    RatioMeasure,
)


@pytest.fixture
def test_bundle(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    test_data: dict[str, pd.DataFrame],
    sample_age_group_df: pd.DataFrame,
) -> RatioMeasureDataBundle:
    """A test RatioMeasureDataBundle instance."""
    # Scenario is dropped from test datasets in the DataBundle formatting
    test_data = {key: dataset.droplevel("scenario") for key, dataset in test_data.items()}

    # mock loading of datasets
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
        return_value=test_data,
    )

    return RatioMeasureDataBundle(
        measure=mock_ratio_measure,
        source=DataSource.SIM,
        data_loader=mocker.MagicMock(spec=DataLoader),
        age_group_df=sample_age_group_df,
        scenarios={"scenario": "baseline"},
    )


@pytest.fixture
def reference_bundle(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    reference_data: pd.DataFrame,
    reference_weights: pd.DataFrame,
    sample_age_group_df: pd.DataFrame,
) -> RatioMeasureDataBundle:
    """A reference RatioMeasureDataBundle instance."""

    # mock loading of datasets
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
        return_value={
            "data": reference_data,
        },
    )
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_aggregated_weights",
        return_value=reference_weights,
    )

    return RatioMeasureDataBundle(
        measure=mock_ratio_measure,
        source=DataSource.ARTIFACT,
        data_loader=mocker.MagicMock(spec=DataLoader),
        age_group_df=sample_age_group_df,
        scenarios={},
    )


def test_fuzzy_comparison_init(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test the initialization of the FuzzyComparison class."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)

    with check:
        assert comparison.measure == test_bundle.measure
        assert comparison.test_bundle == test_bundle
        assert comparison.reference_bundle == reference_bundle


def test_fuzzy_comparison_metadata(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test the metadata property of the FuzzyComparison class."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)

    metadata = comparison.metadata

    expected_metadata = [
        ("Measure Key", "mock_measure", "mock_measure"),
        ("Source", "sim", "artifact"),
        ("Shared Indices", "age, sex, year", "age, sex, year"),
        ("Source Specific Indices", "input_draw, random_seed", ""),
        ("Size", "4 rows × 1 columns", "3 rows × 1 columns"),
        ("Num Draws", "3", ""),
        ("Input Draws", "1, 2, 5", ""),
        ("Num Seeds", "3", ""),
    ]
    assert metadata.index.name == "Property"
    assert metadata.shape == (8, 2)
    assert metadata.columns.tolist() == ["Test Data", "Reference Data"]
    for property_name, test_value, reference_value in expected_metadata:
        assert metadata.loc[property_name]["Test Data"] == test_value
        assert metadata.loc[property_name]["Reference Data"] == reference_value


def test_fuzzy_comparison_get_frame(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test the get_frame method of the FuzzyComparison class."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)

    diff = comparison.get_frame(num_rows=1)

    with check:
        assert len(diff) == 1
        assert "test_rate" in diff.columns
        assert "reference_rate" in diff.columns
        assert "percent_error" in diff.columns
        assert DRAW_INDEX in diff.index.names
        assert SEED_INDEX not in diff.index.names

    # Test returning all rows
    all_diff = comparison.get_frame()
    assert len(all_diff) == 3

    # Test sorting
    # descending order
    sorted_desc = comparison.get_frame(sort_by="percent_error", ascending=False)
    for i in range(len(sorted_desc) - 1):
        assert abs(sorted_desc.iloc[i]["percent_error"]) >= abs(
            sorted_desc.iloc[i + 1]["percent_error"]
        )
    sorted_asc = comparison.get_frame(sort_by="percent_error", ascending=True)
    for i in range(len(sorted_asc) - 1):
        assert abs(sorted_asc.iloc[i]["percent_error"]) <= abs(
            sorted_asc.iloc[i + 1]["percent_error"]
        )

    # Test sorting by reference rate
    sorted_by_ref = comparison.get_frame(sort_by="reference_rate", ascending=True)
    for i in range(len(sorted_by_ref) - 1):
        assert (
            sorted_by_ref.iloc[i]["reference_rate"]
            <= sorted_by_ref.iloc[i + 1]["reference_rate"]
        )


def test_fuzzy_comparison_get_frame_aggregated_draws(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test the get_frame method of the FuzzyComparison class with aggregated draws."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    diff = comparison.get_frame(aggregate_draws=True)
    expected_df = pd.DataFrame(
        {
            "test_mean": [0.2, 0.1, 0.325],
            "test_2.5%": [0.2, 0.1, 0.325],
            "test_97.5%": [0.2, 0.1, 0.325],
            # Reference data has no draws and we have no stratifications so we just return the reference data
            "reference_rate": [0.2, 0.12, 0.29],
        },
        index=pd.MultiIndex.from_tuples(
            [("2020", "female", 0), ("2020", "male", 0), ("2025", "male", 0)],
            names=["year", "sex", "age"],
        ),
    )
    assert_frame_equal(diff, expected_df)


@pytest.mark.parametrize("stratifications", ["all", ["year"], []])
@pytest.mark.parametrize("aggregate", [True, False])
@pytest.mark.parametrize("draws", ["test", "reference", "both", "neither"])
def test_fuzzy_comparison_get_frame_parametrized(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
    stratifications: Collection[str] | Literal["all"],
    aggregate: bool,
    draws: str,
) -> None:
    """Test that FuzzyComparison.get_frame raises NotImplementedError when called with non-empty stratifications."""
    draw_values = list(
        test_bundle.datasets["numerator_data"].index.get_level_values(DRAW_INDEX).unique()
    )
    if draws in ["reference", "both"]:
        # Remove draws from test data and add draws index level to reference datasets
        reference_data = _add_draws_to_dataframe(
            reference_bundle.datasets["data"], draw_values
        )
        # Assertion for mypy
        assert reference_bundle.weights is not None
        reference_weights = _add_draws_to_dataframe(reference_bundle.weights, draw_values)
        # Update the reference bundle with the modified data
        reference_bundle.datasets["data"] = reference_data
        reference_bundle.weights = reference_weights
    if draws in ["reference", "neither"]:
        # Remove draws from test dataset
        test_data = {
            dataset_key: test_bundle.datasets[dataset_key]
            .groupby(
                [
                    level
                    for level in test_bundle.datasets[dataset_key].index.names
                    if level != "input_draw"
                ]
            )
            .sum()
            for dataset_key in test_bundle.datasets
        }
        # Update the test bundle with the modified data
        for key, data in test_data.items():
            test_bundle.datasets[key] = data

    comparison = FuzzyComparison(test_bundle, reference_bundle)

    data = comparison.get_frame(stratifications=stratifications, aggregate_draws=aggregate)
    if stratifications == "all":
        expected_index_names = [
            col
            for col in test_bundle.datasets["numerator_data"].index.names
            if col not in ["input_draw", "random_seed", "scenario"]
        ]
        if not aggregate and draws != "neither":
            expected_index_names += ["input_draw"]
        assert set(data.index.names) == set(expected_index_names)
    elif stratifications == ["year"]:
        assert set(data.index.names) == {"year"} if aggregate else {"year", "input_draw"}
    else:
        # stratifications is [] and all index levels are aggregated over
        assert not data.empty
        assert set(data.index.names) == {"index"} if aggregate else {"input_draw"}
    if aggregate:
        schema_mapper = {
            "test": {"test_mean", "test_2.5%", "test_97.5%", "reference_rate"},
            "reference": {"test_rate", "reference_mean", "reference_2.5%", "reference_97.5%"},
            "both": {
                "test_mean",
                "test_2.5%",
                "test_97.5%",
                "reference_mean",
                "reference_2.5%",
                "reference_97.5%",
            },
            "neither": {"test_rate", "reference_rate"},
        }
        expected_columns = schema_mapper[draws]
    else:
        expected_columns = {"test_rate", "reference_rate", "percent_error"}
    assert set(data.columns) == expected_columns


def test_fuzzy_comparison_verify_not_implemented(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """ "FuzzyComparison.verify() is not implemented."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)

    with pytest.raises(NotImplementedError):
        comparison.verify()


def test_fuzzy_comparison_align_datasets_calculation(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test _align_datasets with varying denominators to ensure ratios are calculated correctly."""

    comparison = FuzzyComparison(test_bundle, reference_bundle)

    aligned_test_data, aligned_reference_data = comparison.align_datasets()
    pd.testing.assert_frame_equal(
        aligned_reference_data,
        reference_bundle.datasets["data"].sort_index(),
    )

    expected_values = [10 / 100, 20 / 100, (30 + 35) / (100 + 100)]
    expected_index = pd.MultiIndex.from_tuples(
        [
            ("2020", "male", 0, 1),
            ("2020", "female", 0, 5),
            ("2025", "male", 0, 2),
        ],
        names=["year", "sex", "age", DRAW_INDEX],
    )
    assert_frame_equal(
        aligned_test_data,
        pd.DataFrame(
            {"value": expected_values},
            index=expected_index,
        ),
    )


@pytest.mark.slow
def test_comparison_with_gbd_init(sim_result_dir: Path) -> None:
    if not IS_ON_SLURM:
        pytest.skip("No cluster access to use GBD data.")

    age_bins = interface.get_age_bins()
    age_bins.index.rename({"age_group_name": INPUT_DATA_INDEX_NAMES.AGE_GROUP}, inplace=True)

    incidence = Incidence("diarrheal_diseases")
    test_bundle = RatioMeasureDataBundle(
        measure=incidence,
        source=DataSource.GBD,
        data_loader=DataLoader(sim_result_dir),
        age_group_df=age_bins,
    )
    ref_bundle = RatioMeasureDataBundle(
        measure=incidence,
        source=DataSource.GBD,
        data_loader=DataLoader(sim_result_dir),
        age_group_df=age_bins,
    )
    comparison = FuzzyComparison(test_bundle, ref_bundle)
    assert comparison.reference_bundle == ref_bundle
    assert comparison.test_bundle == test_bundle

    # Bundles are the same so differences should be zero
    diff = comparison.get_frame()
    assert (diff["test_rate"] == diff["reference_rate"]).all()
    assert (diff["percent_error"] == 0.0).all()


def _add_draws_to_dataframe(df: pd.DataFrame, draw_values: list[int]) -> pd.DataFrame:
    """Add a 'input_draw' index level to the DataFrame."""
    df["input_draw"] = draw_values
    return df.set_index("input_draw", append=True).sort_index()


def test_get_frame_default_rows(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test that get_frame returns default number of rows when num_rows is not specified."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)

    diff = comparison.get_frame()
    assert len(diff) == 3  # There are only 3 rows in the test data

    non_default = comparison.get_frame(num_rows=2)
    assert len(non_default) == 2
