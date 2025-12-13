from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vivarium_testing_utils.automated_validation.constants import INPUT_DATA_INDEX_NAMES
from vivarium_testing_utils.automated_validation.data_transformation.age_groups import (
    AgeGroup,
    AgeRange,
    AgeSchema,
    AgeTuple,
    _format_dataframe,
    _get_transform_matrix,
    format_dataframe_from_age_bin_df,
    rebin_count_dataframe,
)


def test_age_group() -> None:
    """Test the AgeGroup class instantiation."""
    group = AgeGroup("foo", 5, 13)
    assert group.name == "foo"
    assert group.start == 5
    assert group.end == 13
    assert group.span == 8


@pytest.mark.parametrize(
    "name, start, end, match",
    [
        ("0_to_5_years", -1, 1, "Negative start age"),
        ("0_to_5_years", 1, -1, "Negative end age"),
        ("0_to_5_years", 5, 4, "End age must be greater than start age."),
    ],
)
def test_age_group_invalid(name: str, start: int, end: int, match: str) -> None:
    """Test the AgeGroup class instantiation with invalid parameters."""
    with pytest.raises(ValueError, match=match):
        AgeGroup(name, start, end)


def test_age_group_eq() -> None:
    """Test the equality operator for AgeGroup."""
    group1 = AgeGroup("0_to_5_years", 0, 5)
    group2 = AgeGroup("0_to_5", 0, 5)
    group3 = AgeGroup("5_to_10_years", 5, 10)
    assert group1 == group2
    assert group1 != group3


@pytest.mark.parametrize(
    "string, ages",
    [
        ("0_to_4_years", (0, 5)),
        ("0_to_5_months", (0, 0.5)),
        ("0_to_7_days", (0, 0.02191780821917808)),
        ("14_to_16", (14, 17)),
    ],
)
def test_age_group_from_string(string: str, ages: AgeRange) -> None:
    """Test AgeGroup instantiation from string."""
    group = AgeGroup.from_string(string)
    assert group.name == string
    assert group.start == ages[0]
    assert np.isclose(group.end, ages[1])
    assert np.isclose(group.span, ages[1] - ages[0])


@pytest.mark.parametrize(
    "string, match",
    [
        ("invalid_format", "Invalid age group name format:"),
        (
            "0_to_5_invalid_unit",
            "Invalid unit: invalid_unit. Must be 'days', 'months', or 'years'.",
        ),
    ],
)
def test_age_group_invalid_string(string: str, match: str) -> None:
    """Test AgeGroup instantiation from invalid string."""
    with pytest.raises(ValueError, match=match):
        AgeGroup.from_string(string)


def test_age_group_from_range() -> None:
    """Test AgeGroup instantiation from range."""
    group = AgeGroup.from_range(0, 5)
    assert group.name == "0_to_5"
    assert group.start == 0
    assert group.end == 5
    assert group.span == 5


@pytest.mark.parametrize(
    "group_name, group_ages, fraction",
    [
        ("0_to_5_years", (0, 5), 1.0),
        ("0_to_10_years", (0, 10), 1.0),
        ("3_to_8_years", (3, 8), 2 / 5),
        ("6_to_10_years", (6, 10), 0.0),
    ],
)
def test_age_group_fraction_contained_by(
    group_name: str, group_ages: AgeRange, fraction: float
) -> None:
    """Test that we get the correct amount of overlap between two age groups."""
    group = AgeGroup("0_to_5_years", 0, 5)

    other_group = AgeGroup(group_name, *group_ages)
    assert group.fraction_contained_by(other_group) == fraction


def check_example_age_schema(age_schema: AgeSchema) -> None:
    """Check that the example age schema was instantiated correctly, regardless of method."""
    assert len(age_schema) == 3
    assert age_schema[0] == AgeGroup("0_to_5", 0, 5)
    assert age_schema[1] == AgeGroup("5_to_10", 5, 10)
    assert age_schema[2] == AgeGroup("10_to_15", 10, 15)
    assert age_schema.range == (0, 15)
    assert age_schema.span == 15


def test_age_schema_instantiation(
    sample_age_tuples: list[AgeTuple],
    sample_df_with_ages: pd.DataFrame,
) -> None:
    """Test the AgeSchema class instantiation."""
    for age_schema in [
        AgeSchema.from_tuples(sample_age_tuples),
        AgeSchema.from_ranges([(tuple[1], tuple[2]) for tuple in sample_age_tuples]),
        AgeSchema.from_strings([tuple[0] for tuple in sample_age_tuples]),
        AgeSchema.from_dataframe(sample_df_with_ages),
    ]:
        check_example_age_schema(age_schema)


@pytest.mark.parametrize(
    "age_groups, err_match",
    [
        ([("0_to_5", 0, 5), ("4_to_10", 4, 10)], "Overlapping age groups"),
        ([("0_to_5", 0, 5), ("6_to_10", 6, 10)], "Gap between consecutive age groups"),
        ([], "No age groups provided"),
    ],
)
def test_age_schema_validation(age_groups: list[AgeTuple], err_match: str) -> None:
    """Test we get errors for invalid combinations of age groups."""
    with pytest.raises(ValueError, match=err_match):
        AgeSchema.from_tuples(age_groups)


def test_age_schema_to_dataframe(
    sample_age_schema: AgeSchema, sample_age_group_df: pd.DataFrame
) -> None:
    """Test we can convert an AgeSchema to a DataFrame."""
    pd.testing.assert_frame_equal(sample_age_schema.to_dataframe(), sample_age_group_df)


def test_age_schema_eq() -> None:
    """Test the equality operator for AgeSchema."""
    schema1 = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_10", 5, 10)])
    schema2 = AgeSchema.from_tuples([("foo", 0, 5), ("bar", 5, 10)])
    schema3 = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_15", 5, 15)])

    assert schema1 == schema2
    assert schema1 != schema3


def test_age_schema_contains() -> None:
    """Test the contains method for AgeSchema."""
    schema = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_10", 5, 10)])

    assert AgeGroup("different_name", 0, 5) in schema
    assert AgeGroup("0_to_5", 0, 5) in schema
    assert not AgeGroup("10_to_15", 10, 15) in schema


def test_age_schema_is_subset(sample_age_schema: AgeSchema) -> None:
    """Test we can see whether one schema is a subset of another."""
    subset_schema = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_10", 5, 10)])
    not_subset_schema = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_15", 5, 15)])

    assert subset_schema.is_subset(sample_age_schema)
    assert not not_subset_schema.is_subset(sample_age_schema)


def test_age_schema_can_coerce_to() -> None:
    """Test whether one schema can be transformed to another."""
    schema1 = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_10", 5, 10)])
    schema2 = AgeSchema.from_tuples([("0_to_4", 0, 4), ("4_to_10", 4, 10)])
    schema3 = AgeSchema.from_tuples([("0_to_5", 0, 5), ("5_to_15", 5, 15)])

    assert schema1.can_coerce_to(schema2)
    assert schema2.can_coerce_to(schema1)
    assert not schema1.can_coerce_to(schema3)
    assert schema3.can_coerce_to(schema1)


def test_age_schema_get_transform_matrix(sample_age_schema: AgeSchema) -> None:
    """Test we can get a transform matrix between two schemas."""
    new_schema = AgeSchema.from_tuples([("0_to_7.4", 0, 7.5), ("7.5_to_14", 7.5, 15)])
    transform_matrix = _get_transform_matrix(sample_age_schema, new_schema)
    expected_matrix = pd.DataFrame(
        {
            "0_to_4": [1.0, 0.0],
            "5_to_9": [0.5, 0.5],
            "10_to_14": [0.0, 1.0],
        },
        index=["0_to_7.4", "7.5_to_14"],
    )

    pd.testing.assert_frame_equal(transform_matrix, expected_matrix)


def test_age_schema_format_cols(
    sample_age_schema: AgeSchema, sample_df_with_ages: pd.DataFrame
) -> None:
    """Test we can format a DataFrame with only age groups."""
    for dataframe in [
        sample_df_with_ages,
        sample_df_with_ages.droplevel(
            [INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]
        ),
    ]:
        pd.testing.assert_frame_equal(
            _format_dataframe(sample_age_schema, dataframe),
            sample_df_with_ages.droplevel(
                [INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]
            ),
        )


def test_age_schema_format_dataframe_invalid(sample_age_schema: AgeSchema) -> None:
    """Test we get an error if we try to format a DataFrame with invalid age groups."""
    df = pd.DataFrame(
        {
            "foo": [1.0, 2.0],
            "bar": [5.0, 6.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cause", "disease", "25_to_29"),
                ("cause", "disease", "30_to_39"),
            ],
            names=["cause", "disease", INPUT_DATA_INDEX_NAMES.AGE_GROUP],
        ),
    )
    with pytest.raises(ValueError, match="Cannot coerce"):
        _format_dataframe(sample_age_schema, df)


def test_age_schema_format_dataframe_rebin(sample_df_with_ages: pd.DataFrame) -> None:
    """Test we that format_dataframe rebins the dataframe when necessary."""
    target_age_schema = AgeSchema.from_tuples(
        [
            ("0_to_3", 0, 3),
            ("3_to_4", 3, 4),
            ("4_to_7", 4, 7),
            ("7_to_15", 7, 15),
        ]
    )
    formatted_df = _format_dataframe(target_age_schema, sample_df_with_ages)
    pd.testing.assert_frame_equal(
        formatted_df,
        rebin_count_dataframe(
            target_age_schema,
            sample_df_with_ages.droplevel(
                [INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]
            ),
        ),
    )


def test_rebin_dataframe(sample_df_with_ages: pd.DataFrame) -> None:
    """Test we can transform a DataFrame to a new age schema with uneven groups."""
    df = sample_df_with_ages.droplevel(
        [INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]
    )

    target_age_schema = AgeSchema.from_tuples(
        [
            ("0_to_3", 0, 3),
            ("3_to_4", 3, 4),
            ("4_to_7", 4, 7),
            ("7_to_15", 7, 15),
        ]
    )
    expected_foo = {
        "0_to_3": 1.0 * 3 / 5,
        "3_to_4": 1.0 * 1 / 5,
        "4_to_7": 1.0 * 1 / 5 + 2.0 * 2 / 5,
        "7_to_15": 2.0 * 3 / 5 + 3.0,
    }

    rebinned_df = rebin_count_dataframe(target_age_schema, df)
    expected_df = pd.DataFrame(
        {
            "value": expected_foo.values(),
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cause", "disease", "0_to_3"),
                ("cause", "disease", "3_to_4"),
                ("cause", "disease", "4_to_7"),
                ("cause", "disease", "7_to_15"),
            ],
            names=["cause", "disease", INPUT_DATA_INDEX_NAMES.AGE_GROUP],
        ),
    )
    pd.testing.assert_frame_equal(rebinned_df, expected_df)


def test_format_dataframe_from_age_bin_df(
    sample_df_with_ages: pd.DataFrame,
    sample_age_group_df: pd.DataFrame,
    person_time_data: pd.DataFrame,
) -> None:
    """Test we can reconcile age groups with the data."""
    # Ensure that if the age groups are in the data, we can format the data
    formatted_df = format_dataframe_from_age_bin_df(sample_df_with_ages, sample_age_group_df)
    context_age_schema = AgeSchema.from_dataframe(sample_age_group_df)
    pd.testing.assert_frame_equal(
        formatted_df,
        _format_dataframe(context_age_schema, sample_df_with_ages),
    )

    formatted_df = format_dataframe_from_age_bin_df(person_time_data, sample_age_group_df)
    pd.testing.assert_frame_equal(
        formatted_df,
        person_time_data,
    )


def test_resolve_special_age_groups() -> None:
    """Test we can resolve special age groups."""

    # Format of VPH observer outputs
    data = pd.DataFrame(
        {
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cause", "disease", "early_neonatal"),
                ("cause", "disease", "late_neonatal"),
                ("cause", "disease", "1-5_months"),
                ("cause", "disease", "6-11_months"),
                ("cause", "disease", "12-23_months"),
                ("cause", "disease", "2_to_4"),
                ("cause", "disease", "5_to_9"),
            ],
            names=["measure", "entity", INPUT_DATA_INDEX_NAMES.AGE_GROUP],
        ),
    )

    # Make sample age group to match GBD format
    age_bins = pd.DataFrame(
        {
            INPUT_DATA_INDEX_NAMES.AGE_GROUP: [
                "Early Neonatal",
                "Late Neonatal",
                "1-5 months",
                "6-11 months",
                "12-23 months",
                "2 to 4",
                "5 to 9",
            ],
            INPUT_DATA_INDEX_NAMES.AGE_START: [
                0.0,
                7 / 365.0,
                28 / 365.0,
                0.5,
                1.0,
                2.0,
                5.0,
            ],
            INPUT_DATA_INDEX_NAMES.AGE_END: [7 / 365.0, 28 / 365.0, 0.5, 1.0, 2.0, 5.0, 10.0],
        }
    )
    age_bins = age_bins.set_index(
        [
            INPUT_DATA_INDEX_NAMES.AGE_GROUP,
            INPUT_DATA_INDEX_NAMES.AGE_START,
            INPUT_DATA_INDEX_NAMES.AGE_END,
        ]
    )

    formatted_df = format_dataframe_from_age_bin_df(data, age_bins)
    context_age_schema = AgeSchema.from_dataframe(age_bins)
    pd.testing.assert_frame_equal(formatted_df, _format_dataframe(context_age_schema, data))

    # Test older age groups for 95 plus special case
    old_but_gold = pd.DataFrame(
        {
            "value": [1.0, 2.0, 3.0, 4.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("cause", "disease", "80_to_84"),
                ("cause", "disease", "85_to_89"),
                ("cause", "disease", "90_to_94"),
                ("cause", "disease", "95_plus"),
            ],
            names=["measure", "entity", INPUT_DATA_INDEX_NAMES.AGE_GROUP],
        ),
    )
    oldies = pd.DataFrame(
        {
            INPUT_DATA_INDEX_NAMES.AGE_GROUP: [
                "80 to 84",
                "85 to 89",
                "90 to 94",
                "95 plus",
            ],
            INPUT_DATA_INDEX_NAMES.AGE_START: [80.0, 85.0, 90.0, 95.0],
            INPUT_DATA_INDEX_NAMES.AGE_END: [85.0, 90.0, 95.0, 125.0],
        }
    )
    oldies = oldies.set_index(
        [
            INPUT_DATA_INDEX_NAMES.AGE_GROUP,
            INPUT_DATA_INDEX_NAMES.AGE_START,
            INPUT_DATA_INDEX_NAMES.AGE_END,
        ]
    )
    formatted_oldies = format_dataframe_from_age_bin_df(old_but_gold, oldies)
    context_oldies_schema = AgeSchema.from_dataframe(oldies)
    pd.testing.assert_frame_equal(
        formatted_oldies, _format_dataframe(context_oldies_schema, old_but_gold)
    )
