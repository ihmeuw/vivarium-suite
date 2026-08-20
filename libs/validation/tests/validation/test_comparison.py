from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Literal, cast
from unittest import mock

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pytest_check import check
from pytest_mock import MockFixture
from vivarium.fuzzy_checker import TestResult
from vivarium_inputs import interface

from vivarium.validation.bundle import RatioMeasureDataBundle
from vivarium.validation.comparison import (
    FuzzyComparison,
    StratifiedTargetIntervalConfig,
    StratValue,
)
from vivarium.validation.constants import (
    DAYS_PER_YEAR,
    DRAW_INDEX,
    INPUT_DATA_INDEX_NAMES,
    SEED_INDEX,
    DataSource,
)
from vivarium.validation.data_loader import DataLoader
from vivarium.validation.data_transformation import age_groups
from vivarium.validation.data_transformation.measures import Incidence, RatioMeasure


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
        "vivarium.validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
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
        "vivarium.validation.bundle.RatioMeasureDataBundle._get_formatted_datasets",
        return_value={
            "data": reference_data,
        },
    )
    mocker.patch(
        "vivarium.validation.bundle.RatioMeasureDataBundle._get_aggregated_weights",
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
@pytest.mark.cluster
def test_comparison_with_gbd_init(sim_result_dir: Path) -> None:
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


def test_comparison_verify(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test the verify method of the FuzzyComparison class."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    step_size = 28 / DAYS_PER_YEAR
    comparison.verify(step_size=step_size)
    assert set(["overall", "stratified"]) == set(comparison.proportion_test_results.keys())
    # Reference bundle has 3 rows (groups) that would be validated between the two bundles
    stratified_results = comparison.proportion_test_results["stratified"]
    assert isinstance(stratified_results, dict)
    # Index levels are age, sex, year. (age, sex, year), (age, sex), (age, year), (sex, year)
    # and each index of the 3 index levels
    assert len(stratified_results.keys()) == 7
    overall_result = comparison.proportion_test_results["overall"]
    assert isinstance(overall_result, TestResult)
    assert not any(
        result.reject_null for result in stratified_results[("year", "sex", "age")].values()
    )
    assert not overall_result.reject_null


def _finest_results(comparison: FuzzyComparison) -> dict[str, TestResult]:
    """Return the results for the most granular stratification."""
    stratified = comparison.proportion_test_results["stratified"]
    assert isinstance(stratified, dict)
    return stratified[("year", "sex", "age")]


def _declare(
    comparison: FuzzyComparison,
    *,
    denominator_is_person_time: bool = False,
    reference_is_rate: bool = False,
) -> None:
    """Set what the comparison's mock measure declares about its data.

    ``is_person_time`` and ``reference_is_rate`` are read-only properties on the real
    classes, so the annotations reject assignment even though the mock accepts it.
    """
    measure = cast(Any, comparison.measure)
    measure.denominator.is_person_time = denominator_is_person_time
    measure.reference_is_rate = reference_is_rate


@pytest.mark.parametrize("step_size_days", [1, 7, 28])
def test_verify_preserves_expected_events(
    step_size_days: int,
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test that converting to person-steps leaves the expected event count alone."""
    step_size = step_size_days / DAYS_PER_YEAR

    # A measure that declares neither side converts is the unscaled baseline.
    baseline = FuzzyComparison(test_bundle, reference_bundle)
    baseline.verify(step_size=step_size)
    expected_events = {
        key: result.observed_denominator * result.target_lower_bound
        for key, result in _finest_results(baseline).items()
    }

    converted = FuzzyComparison(test_bundle, reference_bundle)
    _declare(converted, denominator_is_person_time=True, reference_is_rate=True)
    converted.verify(step_size=step_size)

    for key, result in _finest_results(converted).items():
        # The denominator is rounded to a whole number of person-steps, so the
        # invariant holds to within that rounding rather than exactly.
        assert result.observed_denominator * result.target_lower_bound == pytest.approx(
            expected_events[key], rel=1e-3
        )


def test_verify_requires_step_size_only_for_person_time(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test that only a person-time measure demands a step size."""
    counts = FuzzyComparison(test_bundle, reference_bundle)
    counts.verify(step_size=None)
    assert counts.proportion_test_results["overall"]

    person_time = FuzzyComparison(test_bundle, reference_bundle)
    _declare(person_time, denominator_is_person_time=True)
    with pytest.raises(ValueError, match="without a step size"):
        person_time.verify(step_size=None)


@pytest.mark.parametrize(
    "reference_is_rate, scaled",
    [
        pytest.param(True, True, id="rate_target_is_scaled"),
        pytest.param(False, False, id="proportion_target_is_not_scaled"),
    ],
)
def test_verify_scales_only_rate_targets(
    reference_is_rate: bool,
    scaled: bool,
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
    reference_data: pd.DataFrame,
) -> None:
    """Test that the step size converts a rate target and leaves a proportion alone."""
    step_size = 28 / DAYS_PER_YEAR
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    _declare(comparison, reference_is_rate=reference_is_rate)
    comparison.verify(step_size=step_size)

    expected = sorted(reference_data["value"] * (step_size if scaled else 1.0))
    actual = sorted(
        result.target_lower_bound for result in _finest_results(comparison).values()
    )
    assert actual == pytest.approx(expected)


def test_target_interval_configuration_default_none(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test that a new FuzzyComparison has target_interval_configuration as None."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    assert comparison.target_interval_configuration is None


def test_target_interval_configuration_setter(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
) -> None:
    """Test that target_interval_configuration can be set with a StratifiedTargetIntervalConfig."""
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    config = StratifiedTargetIntervalConfig(
        relative_error=0.1, stratifications={"sex": "all"}
    )
    comparison.target_interval_configuration = config
    assert comparison.target_interval_configuration is config
    assert comparison.target_interval_configuration.stratifications == {"sex": "all"}
    assert comparison.target_interval_configuration.relative_error == 0.1

    # Test overwrite with a new config
    new_config = StratifiedTargetIntervalConfig(
        relative_error=0.2, stratifications={"age": "specific"}
    )
    comparison.target_interval_configuration = new_config
    assert comparison.target_interval_configuration is new_config
    assert comparison.target_interval_configuration.stratifications == {"age": "specific"}

    # Test setting back to None
    comparison.target_interval_configuration = None
    assert comparison.target_interval_configuration is None


@pytest.mark.parametrize(
    "stratifications, index_info, expected",
    [
        # "all" matches only groups where the stratification is absent
        ({"sex": "all"}, {"age": "Early Neonatal", "year": 2024}, True),
        ({"sex": "all"}, {"sex": "Male", "age": "Early Neonatal"}, False),
        ({"sex": "all"}, {}, True),
        # "specific" matches only groups where the stratification is present
        ({"sex": "specific"}, {"sex": "Male"}, True),
        ({"sex": "specific"}, {"age": "Early Neonatal"}, False),
        ({"sex": "specific"}, {}, False),
        # An explicit value matches only that value
        ({"sex": "Male"}, {"sex": "Male", "age": "Early Neonatal"}, True),
        ({"sex": "Male"}, {"sex": "Female", "age": "Early Neonatal"}, False),
        ({"sex": "Male"}, {"age": "Early Neonatal"}, False),
        # Multiple filters are ANDed together
        (
            {"sex": "specific", "age": "Early Neonatal"},
            {"sex": "Male", "age": "Early Neonatal"},
            True,
        ),
        (
            {"sex": "specific", "age": "Early Neonatal"},
            {"sex": "Male", "age": "Late Neonatal"},
            False,
        ),
        ({"sex": "specific", "age": "Early Neonatal"}, {"age": "Early Neonatal"}, False),
        # No filters applies to everything, as the base class does
        ({}, {"sex": "Male"}, True),
    ],
)
def test_stratified_target_interval_config_applies_to(
    stratifications: dict[str, StratValue],
    index_info: dict[str, StratValue],
    expected: bool,
) -> None:
    """Test which groups a StratifiedTargetIntervalConfig applies to."""
    config = StratifiedTargetIntervalConfig(
        relative_error=0.1, stratifications=stratifications
    )
    assert config.applies_to(index_info) is expected


@pytest.mark.parametrize("relative_error", [-0.1, 0.0, 1.1])
def test_stratified_config_inherits_relative_error_validation(
    relative_error: float,
) -> None:
    """Test that the relative_error validation inherited from the base class still fires."""
    with pytest.raises(ValueError, match="relative_error must be between"):
        StratifiedTargetIntervalConfig(
            relative_error=relative_error, stratifications={"sex": "all"}
        )


def test_stratified_config_is_keyword_only() -> None:
    """Test that positional construction fails rather than binding in field order."""
    with pytest.raises(TypeError, match="positional argument"):
        StratifiedTargetIntervalConfig({"sex": "all"}, 0.1)  # type: ignore[call-arg, arg-type]


@pytest.mark.parametrize(
    "stratifications, applies",
    [
        ({"sex": "all"}, lambda info: "sex" not in info),
        ({"sex": "specific"}, lambda info: "sex" in info),
        ({"sex": "male"}, lambda info: info.get("sex") == "male"),
        # "year" rather than "age" because the fixture has two years but only one
        # age, so this exercises rejection on a differing value, not just absence
        (
            {"sex": "specific", "year": "2020"},
            lambda info: "sex" in info and info.get("year") == "2020",
        ),
    ],
    ids=["all", "specific", "value", "combined"],
)
def test_verify_applies_stratified_target_interval(
    test_bundle: RatioMeasureDataBundle,
    reference_bundle: RatioMeasureDataBundle,
    stratifications: dict[str, StratValue],
    applies: Callable[[dict[str, StratValue]], bool],
) -> None:
    """Test that verify threads a StratifiedTargetIntervalConfig through to the bounds.

    ``applies`` restates each filter independently of
    :meth:`~vivarium.validation.comparison.StratifiedTargetIntervalConfig.applies_to`,
    so a wrong filter cannot satisfy these assertions by agreeing with itself.
    """
    comparison = FuzzyComparison(test_bundle, reference_bundle)
    comparison.target_interval_configuration = StratifiedTargetIntervalConfig(
        relative_error=0.1, stratifications=stratifications
    )
    # step_size=None leaves the reference data unscaled, keeping targets inside [0, 1]
    # so the interval is not clipped
    comparison.verify(step_size=None)

    # A group the config matches gets an interval target, (t * 0.9, t * 1.1), so its
    # bounds differ; a group it does not match keeps the exact target it started with,
    # so its bounds are equal. Count both to be sure each case actually occurred.
    with_interval = without_interval = 0
    stratified_results = comparison.proportion_test_results["stratified"]
    assert isinstance(stratified_results, dict)
    for results in stratified_results.values():
        for result in results.values():
            if applies(result.index_info or {}):
                assert result.target_lower_bound == pytest.approx(
                    result.target_upper_bound * 0.9 / 1.1
                )
                with_interval += 1
            else:
                assert result.target_lower_bound == result.target_upper_bound
                without_interval += 1
    # A filter matching every group or none would satisfy one branch vacuously
    assert with_interval and without_interval

    # The population-level test is described to the filter with no index values
    overall_result = comparison.proportion_test_results["overall"]
    assert isinstance(overall_result, TestResult)
    if applies({}):
        assert overall_result.target_lower_bound == pytest.approx(
            overall_result.target_upper_bound * 0.9 / 1.1
        )
    else:
        assert overall_result.target_lower_bound == overall_result.target_upper_bound
