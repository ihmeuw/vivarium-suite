"""Tests for the RatioMeasureDataBundle class."""

from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
from pytest_mock import MockFixture
from vivarium_inputs import interface

from tests.automated_validation.conftest import IS_ON_SLURM
from vivarium_testing_utils.automated_validation.bundle import RatioMeasureDataBundle
from vivarium_testing_utils.automated_validation.constants import (
    DRAW_INDEX,
    INPUT_DATA_INDEX_NAMES,
    DataSource,
)
from vivarium_testing_utils.automated_validation.data_loader import DataLoader
from vivarium_testing_utils.automated_validation.data_transformation import age_groups
from vivarium_testing_utils.automated_validation.data_transformation.measures import (
    Incidence,
    RatioMeasure,
)


@pytest.mark.parametrize("data_source", [DataSource.SIM, DataSource.ARTIFACT])
def test_data_bundle_init(
    data_source: DataSource,
    sample_age_group_df: pd.DataFrame,
    sim_result_dir: Path,
    artifact_disease_incidence: pd.DataFrame,
) -> None:
    data_loader = DataLoader(sim_result_dir)
    measure = Incidence("disease")
    bundle = RatioMeasureDataBundle(
        measure=measure,
        source=data_source,
        data_loader=data_loader,
        age_group_df=sample_age_group_df,
        scenarios={},
    )

    if data_source == DataSource.SIM:
        expected_keys = set(measure.sim_output_datasets.keys())
    else:
        expected_keys = set(measure.sim_input_datasets.keys())
    assert set(bundle.dataset_names) == expected_keys

    if data_source == DataSource.SIM:
        expected_index = pd.MultiIndex.from_tuples(
            [("A", "baseline"), ("B", "baseline")],
            names=["stratify_column", "scenario"],
        )

        expected_numerator_data = pd.DataFrame(
            {
                "value": [3.0, 5.0],
            },
            index=expected_index,
        )
        expected_denominator_data = pd.DataFrame(
            {
                "value": [17.0, 29.0],
            },
            index=expected_index,
        )
        assert bundle.datasets["numerator_data"].equals(expected_numerator_data)
        assert bundle.datasets["denominator_data"].equals(expected_denominator_data)
        assert bundle.weights is None
    else:
        formatted_data = age_groups.format_dataframe_from_age_bin_df(
            artifact_disease_incidence, sample_age_group_df
        )
        assert bundle.datasets["data"].equals(formatted_data)
        assert isinstance(bundle.weights, pd.DataFrame)
        assert set(bundle.datasets["data"].index.names).issubset(
            set(bundle.weights.index.names)
        )


def test_get_metadata(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    sample_age_group_df: pd.DataFrame,
    test_data: dict[str, pd.DataFrame],
) -> None:
    """Test get_metadata method returns correct basic structure."""

    mocker.patch.object(
        RatioMeasureDataBundle,
        "_get_formatted_datasets",
        return_value=test_data,
    )

    bundle = RatioMeasureDataBundle(
        measure=mock_ratio_measure,
        source=DataSource.SIM,
        data_loader=mocker.MagicMock(spec=DataLoader),
        age_group_df=sample_age_group_df,
    )

    metadata = bundle.get_metadata()

    assert metadata["source"] == "sim"
    assert metadata["index_columns"] == [
        "year",
        "sex",
        "age",
        "input_draw",
        "random_seed",
        "scenario",
    ]
    assert metadata["size"] == "4 rows × 1 columns"


def test_custom_data_source_dataset_names_value_error(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    sample_age_group_df: pd.DataFrame,
) -> None:
    """Test _get_formatted_datasets raises NotImplementedError for GBD source."""
    mock_data_loader = mocker.MagicMock(spec=DataLoader)
    mock_data_loader._get_raw_data_from_source.return_value = {}

    with pytest.raises(ValueError):
        RatioMeasureDataBundle(
            measure=mock_ratio_measure,
            source=DataSource.CUSTOM,
            data_loader=mock_data_loader,
            age_group_df=sample_age_group_df,
        )


@pytest.mark.parametrize("stratifications", [[], ["year", "sex", "age"]])
def test_aggregate_scenario_stratifications(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    test_data: dict[str, pd.DataFrame],
    sample_age_group_df: pd.DataFrame,
    stratifications: list[str],
) -> None:
    # Scenario is dropped from test datasets in the DataBundle formatting
    test_data = {key: dataset.droplevel("scenario") for key, dataset in test_data.items()}

    # mock loading of datasets
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
        return_value=test_data,
    )
    bundle = RatioMeasureDataBundle(
        measure=mock_ratio_measure,
        source=DataSource.SIM,
        data_loader=mocker.MagicMock(spec=DataLoader),
        age_group_df=sample_age_group_df,
        scenarios={"scenario": "baseline"},
    )
    # This is marginalizing the stratifications out of the test data
    aggregated = bundle._aggregate_scenario_stratifications(test_data, stratifications)

    if not stratifications:
        aggregated.equals(test_data["numerator_data"] / test_data["denominator_data"])
    else:
        assert list(stratifications) == list(aggregated.index.names)
        expected = pd.DataFrame(
            data={
                "value": [10 / 100, 20 / 100, (30 + 35) / (100 + 100)],
            },
            index=pd.MultiIndex.from_tuples(
                [("2020", "male", 0), ("2020", "female", 0), ("2025", "male", 0)],
                names=["year", "sex", "age"],
            ),
        )
        pd.testing.assert_frame_equal(aggregated, expected)


@pytest.mark.parametrize("stratifications", ["all", ["age", "sex"]])
def test_aggregate_reference_stratifications(
    mocker: MockFixture,
    mock_ratio_measure: RatioMeasure,
    reference_data: pd.DataFrame,
    reference_weights: pd.DataFrame,
    sample_age_group_df: pd.DataFrame,
    stratifications: list[str] | Literal["all"],
) -> None:
    # mock loading of datasets
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
        return_value={"data": reference_data},
    )
    mocker.patch(
        "vivarium_testing_utils.automated_validation.bundle.RatioMeasureDataBundle._get_aggregated_weights",
        return_value=reference_weights,
    )
    bundle = RatioMeasureDataBundle(
        measure=mock_ratio_measure,
        source=DataSource.ARTIFACT,
        data_loader=mocker.MagicMock(spec=DataLoader),
        age_group_df=sample_age_group_df,
    )
    aggregated = bundle._aggregate_sim_input_stratifications(stratifications)

    if stratifications == "all":
        aggregated.equals(reference_data)
    else:
        assert set(stratifications) == set(aggregated.index.names)
        expected = pd.DataFrame(
            data={
                "value": [
                    (0.20 * 0.25) / 0.25,
                    ((0.15 * 0.12) + (0.35 * 0.29)) / (0.15 + 0.35),
                ]
            },
            index=pd.MultiIndex.from_tuples(
                [
                    ("female", 0),
                    ("male", 0),
                ],
                names=["sex", "age"],
            ),
        )
        pd.testing.assert_frame_equal(aggregated, expected)


@pytest.mark.slow
def test_data_bundle_gbd_source(sim_result_dir: Path) -> None:
    """Test that GBD data source is handled correctly in RatioMeasureDataBundle."""
    if not IS_ON_SLURM:
        pytest.skip("GBD access not available for this test.")

    age_bins = interface.get_age_bins()
    age_bins.index.rename({"age_group_name": INPUT_DATA_INDEX_NAMES.AGE_GROUP}, inplace=True)

    incidence = Incidence("diarrheal_diseases")
    bundle = RatioMeasureDataBundle(
        measure=incidence,
        source=DataSource.GBD,
        data_loader=DataLoader(sim_result_dir),
        age_group_df=age_bins,
    )

    assert set(bundle.dataset_names) == {"data"}
    # Validate datasets and weights schema
    dataset_index_names = {
        "sex",
        INPUT_DATA_INDEX_NAMES.AGE_GROUP,
        "year_start",
        "year_end",
        DRAW_INDEX,
    }
    assert set(bundle.datasets["data"].index.names) == dataset_index_names
    assert set(bundle.datasets["data"].columns) == {"value"}
    assert bundle.weights is not None
    assert set(bundle.weights.index.names) == dataset_index_names.union({"location"})
    assert set(bundle.weights.columns) == {"value"}

    # Validate data aggregation
    stratify_1 = bundle.get_measure_data("all")
    pd.testing.assert_frame_equal(
        stratify_1,
        bundle.datasets["data"].sort_index(),
        check_exact=False,
        rtol=1e-5,
        atol=1e-8,
    )
    stratify_2 = bundle.get_measure_data(["sex", INPUT_DATA_INDEX_NAMES.AGE_GROUP])
    assert set(stratify_2.index.names) == {
        "sex",
        INPUT_DATA_INDEX_NAMES.AGE_GROUP,
        DRAW_INDEX,
    }

    metadata = bundle.get_metadata()
    assert metadata["source"] == "gbd"
    assert metadata["index_columns"] == [
        "sex",
        "year_start",
        "year_end",
        "input_draw",
        "age_group",
    ]
    assert set(metadata.keys()) == {
        "source",
        "index_columns",
        "size",
        "num_draws",
        "input_draws",
    }
