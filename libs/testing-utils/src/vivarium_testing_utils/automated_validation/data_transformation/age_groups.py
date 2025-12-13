from __future__ import annotations

import re

import pandas as pd
from loguru import logger

from vivarium_testing_utils.automated_validation.constants import INPUT_DATA_INDEX_NAMES

AgeTuple = tuple[str, int | float, int | float]
AgeRange = tuple[int | float, int | float]

from vivarium_testing_utils.automated_validation.data_transformation import utils
from vivarium_testing_utils.automated_validation.data_transformation.data_schema import (
    SingleNumericColumn,
)

# Tolerance for floating-point age comparisons, sufficient to handle floating-point precision issues
# while still catching legitimate data problems
AGE_TOLERANCE = 1e-8


class AgeGroup:
    """
    Class to represent a single age interval with start and end ages.
    """

    def __init__(self, name: str, start: int | float, end: int | float):
        """

        Parameters
        ----------
        name
            The name of the age group.
        start
            The start age of the age group.
        end
            The end age of the age group.
        Raises
        ------
        ValueError
            If the start or end age is negative, or if the end age is less than or equal to the start age.
        """
        self.name = name
        if start < 0:
            raise ValueError(f"Negative start age.")
        self.start = float(start)
        if end < 0:
            raise ValueError(f"Negative end age.")
        self.end = float(end)
        if self.end - self.start <= 0:
            raise ValueError("End age must be greater than start age.")
        self.span = float(self.end - self.start)

    def __eq__(self, other: object) -> bool:
        """Define equality between two age groups.

        Parameters
        ----------
        other
            The other object to compare to.

        Returns
        -------
            True if the two age groups have the same start and end ages, False otherwise.
        """
        if not isinstance(other, AgeGroup):
            return NotImplemented
        return (self.start, self.end) == (other.start, other.end)

    def fraction_contained_by(self, other: AgeGroup) -> float:
        """
        Return the amount of this group that is contained within another group.

        Parameters
        ----------
        other
            The other age group to compare to.

        Returns
        -------
            The fraction of this age group that is contained within the other age group.
        """
        overlap_start = max(self.start, other.start)
        overlap_end = min(self.end, other.end)
        overlap = max(0.0, overlap_end - overlap_start)
        return overlap / self.span

    @classmethod
    def from_string(cls, name: str) -> AgeGroup:
        """
        Parse age group names to extract start and end ages and their units.
        Supports formats like '0_to_6_months', '12_to_15_years', '0_to_8_days', '14_to_17'

        Parameters
        ----------
        name
            The name of the age group.

        Returns
        -------
            An AgeGroup object with the parsed name, start, and end ages.

        Raises
        ------
            ValueError
                If the name does not match the expected format.
        """
        # Special case for "early_neonatal", "late_neonatal", and "95_plus"
        special_age_groups = {
            "early_neonatal": ("Early Neonatal", 0.0, 7 / 365.0),
            "late_neonatal": ("Late Neonatal", 7 / 365.0, 28 / 365.0),
            # 1-5 months is not exactly 1 month so it is special cased
            "1-5_months": ("1-5 months", 28.0 / 365.0, 0.5),
            "95_plus": ("95 plus", 95.0, 125.0),
        }
        if name in special_age_groups:
            special_name, start, end = special_age_groups[name]
            return cls(special_name, start, end)
        # Extract numbers and unit from the group name
        pattern = r"(\d+(?:\.\d+)?)(?:_to_|-)(\d+(?:\.\d+)?)(?:_(\w+))?"
        match = re.match(pattern, name.lower())

        if not match:
            raise ValueError(f"Invalid age group name format: {name}")

        start_str, end_str, unit = match.groups()
        start, end = float(start_str), float(end_str) + 1

        # Default to years if unit is not specified
        if unit is None:
            unit = "years"

        # Convert all to years for consistent comparison
        if unit == "days":
            start_years = start / 365  # Approximate
            end_years = end / 365
        elif unit == "months":
            start_years = start / 12
            end_years = end / 12
        elif unit == "years":
            start_years = start
            end_years = end
        else:
            raise ValueError(f"Invalid unit: {unit}. Must be 'days', 'months', or 'years'.")

        return cls(name, start_years, end_years)

    @classmethod
    def from_range(cls, start: float | int, end: float | int) -> AgeGroup:
        """
        Create an AgeGroup from a start and end age.
        Parameters
        ----------
        start
            The start age of the age group.
        end
            The end age of the age group.
        Returns
        -------
            An AgeGroup object with the specified start and end ages.
        """
        return cls(f"{start}_to_{end}", start, end)


class AgeSchema:
    """
    An AgeSchema is an ordered collection of disjoint age groups spanning a contiguous range of ages.
    """

    def __init__(self, age_groups: list[AgeGroup]) -> None:
        """
        Parameters
        ----------
        age_groups
            A list of AgeGroup objects representing the age groups in the schema.

        Raises
        ------
        ValueError
            If there are no age groups provided, or if the age groups are overlapping or not contiguous.
        """
        self.age_groups = age_groups
        self.age_groups.sort(key=lambda x: x.start)
        self._validate()
        self.range = (self.age_groups[0].start, self.age_groups[-1].end)
        self.span = self.range[1] - self.range[0]

    def __getitem__(self, index: int) -> AgeGroup:
        """Get an age group by index.

        Parameters
        ----------
        index
            The index of the age group to get.

        Returns
        -------
            The age group at the specified index.
        """
        return self.age_groups[index]

    def __len__(self) -> int:
        """Get the number of age groups."""
        return len(self.age_groups)

    def __eq__(self, other: object) -> bool:
        """Define equality between two age schemas.

        Parameters
        ----------
        other
            The other object to compare to.

        Returns
        -------
            True if the two age groups have an equivalent set of age groups, False otherwise.
        """
        if not isinstance(other, AgeSchema):
            return NotImplemented
        if len(self.age_groups) != len(other.age_groups):
            return False
        for i in range(len(self.age_groups)):
            if self.age_groups[i] != other.age_groups[i]:
                return False
        return True

    def __contains__(self, item: AgeGroup) -> bool:
        """
        Check if an age group is contained in the schema.

        Parameters
        ----------
        item
            The age group to check for.

        Returns
        -------
            True if the age group is contained in the schema, False otherwise.
        """
        return any(item == group for group in self.age_groups)

    def is_subset(self, other: AgeSchema) -> bool:
        """
        Check if this schema is a subset of another schema.
        """
        return all(group in other for group in self.age_groups)

    @classmethod
    def from_tuples(cls, age_tuples: list[AgeTuple]) -> AgeSchema:
        """Create an AgeSchema from a list of age tuples.

        Parameters
        ----------
        age_tuples
            A list of tuples containing the name, start, and end ages of the age groups.

        Returns
        -------
            An AgeSchema with the specified age groups.
        """
        age_groups = []
        for group_tuple in age_tuples:
            age_groups.append(AgeGroup(*group_tuple))
        return cls(age_groups)

    @classmethod
    def from_ranges(cls, age_ranges: list[AgeRange]) -> AgeSchema:
        """
        Create an AgeSchema from a list of age ranges.

        Parameters
        ----------
        age_ranges
            A list of tuples containing the start and end ages of the age groups.

        Returns
        -------
            An AgeSchema with the specified age groups.
        """
        age_groups = []
        for start, end in age_ranges:
            age_groups.append(AgeGroup.from_range(start, end))
        return cls(age_groups)

    @classmethod
    def from_strings(cls, age_strings: list[str]) -> AgeSchema:
        """
        Create an AgeSchema from a list of age group names.

        Parameters
        ----------
        age_strings
            A list of strings representing the names of the age groups.

        Returns
        -------
            An AgeSchema with the specified age groups.
        """
        age_groups = []
        for name in age_strings:
            age_groups.append(AgeGroup.from_string(name))
        return cls(age_groups)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> AgeSchema:
        """
        Create an AgeSchema from a DataFrame with age group names.

        The DataFrame must have either 'age_group' or 'age_start' and 'age_end' index levels.

        Parameters
        ----------
        df
            A DataFrame with age group names and/or their start and end ages.

        Returns
        -------
            An AgeSchema with the specified age groups.
        """
        has_age_group = INPUT_DATA_INDEX_NAMES.AGE_GROUP in df.index.names
        has_age_range = (
            INPUT_DATA_INDEX_NAMES.AGE_START in df.index.names
            and INPUT_DATA_INDEX_NAMES.AGE_END in df.index.names
        )

        # Usually this occurs for the artifact population.age_bins
        if has_age_group and has_age_range:
            levels = [
                INPUT_DATA_INDEX_NAMES.AGE_GROUP,
                INPUT_DATA_INDEX_NAMES.AGE_START,
                INPUT_DATA_INDEX_NAMES.AGE_END,
            ]
            age_groups = list(
                df.index.droplevel(list(set(df.index.names) - set(levels)))
                .reorder_levels(levels)
                .unique()
            )

            return cls.from_tuples(age_groups)
        # Most artifact dataframes have age start/end but not age group
        elif has_age_range:
            levels = [INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]
            age_groups = (
                df.index.droplevel(list(set(df.index.names) - set(levels)))
                .reorder_levels(levels)
                .unique()
            )
            return cls.from_ranges(age_groups)
        # Most simulation dataframes have age group but not start/end
        elif has_age_group:
            levels = [INPUT_DATA_INDEX_NAMES.AGE_GROUP]
            age_groups = list(
                df.index.droplevel(list(set(df.index.names) - set(levels))).unique()
            )
            return cls.from_strings(age_groups)
        else:
            raise ValueError(
                "DataFrame must have either 'age_group' or 'age_start' and 'age_end' index levels."
            )

    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert the AgeSchema to a DataFrame with age group names and their start and end ages.
        """
        data = {
            INPUT_DATA_INDEX_NAMES.AGE_GROUP: [group.name for group in self.age_groups],
            INPUT_DATA_INDEX_NAMES.AGE_START: [group.start for group in self.age_groups],
            INPUT_DATA_INDEX_NAMES.AGE_END: [group.end for group in self.age_groups],
        }
        return pd.DataFrame(data).set_index(
            [
                INPUT_DATA_INDEX_NAMES.AGE_GROUP,
                INPUT_DATA_INDEX_NAMES.AGE_START,
                INPUT_DATA_INDEX_NAMES.AGE_END,
            ]
        )

    def _validate(self) -> None:
        """
        Validate the age groups to ensure they are non-overlapping and complete.
        """
        if len(self.age_groups) == 0:
            raise ValueError("No age groups provided.")

        for i in range(len(self.age_groups) - 1):
            if self.age_groups[i].end > self.age_groups[i + 1].start + AGE_TOLERANCE:
                raise ValueError(
                    f"Overlapping age groups: {self.age_groups[i]} and {self.age_groups[i + 1]}"
                )
            if self.age_groups[i].end < self.age_groups[i + 1].start - AGE_TOLERANCE:
                raise ValueError(
                    f"Gap between consecutive age groups: {self.age_groups[i]} and {self.age_groups[i + 1]}"
                )

    def can_coerce_to(self, target: AgeSchema) -> bool:
        """
        Check whether this schema can be coerced to another schema.

        That is, this schema spans a sub-interval of the other schema.

        Parameters
        ----------
        target
            The target age schema to check against.
        Returns
        -------
            True if this schema can be coerced to the other schema, False otherwise.

        """
        overlap_start = max(self.range[0], target.range[0])
        overlap_end = min(self.range[1], target.range[1])
        overlap = max(0, overlap_end - overlap_start)
        if overlap < target.span - AGE_TOLERANCE:
            return False
        if self.span < target.span - AGE_TOLERANCE:
            logger.warning(
                "Warning: Age Groups span different total ranges. This could lead to unexpected results at extreme age ranges."
            )
        return True


def _format_dataframe(target_schema: AgeSchema, df: pd.DataFrame) -> pd.DataFrame:
    """
    Format a DataFrame to match the current schema.

    Parameters
    ----------
    target_schema
        The target age schema to convert to.
    df
        The DataFrame to format.
    Returns
    -------
        A DataFrame with the target age schema in the index and transformed values, if rebinning is necessary.

    Raises
    ------
        ValueError
            If the source age schema cannot be coerced to the target schema.
    """
    source_age_schema = AgeSchema.from_dataframe(df)
    index_names = list(df.index.names)
    for age_group_indices in [
        INPUT_DATA_INDEX_NAMES.AGE_GROUP,
        INPUT_DATA_INDEX_NAMES.AGE_START,
        INPUT_DATA_INDEX_NAMES.AGE_END,
    ]:
        if age_group_indices not in index_names:
            index_names.append(age_group_indices)
    df = pd.merge(
        df, source_age_schema.to_dataframe(), left_index=True, right_index=True
    ).reorder_levels(index_names)

    if not source_age_schema.can_coerce_to(target_schema):
        raise ValueError(
            f"Cannot coerce {source_age_schema} to {target_schema}. "
            "The source age interval must be a contained by the target interval of age groups."
        )

    if target_schema.is_subset(source_age_schema):
        return (
            pd.merge(
                df.droplevel([INPUT_DATA_INDEX_NAMES.AGE_GROUP]),
                target_schema.to_dataframe(),
                left_index=True,
                right_index=True,
            )
            .reorder_levels(index_names)
            .droplevel([INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END])
        )
    else:
        logger.info(
            f"Rebinning DataFrame age groups from {source_age_schema} to {target_schema}."
        )
        # if we don't fit pandera schema SimOutputData, assume the data is rate data and raise an error.
        data = rebin_count_dataframe(
            target_schema,
            df.droplevel([INPUT_DATA_INDEX_NAMES.AGE_START, INPUT_DATA_INDEX_NAMES.AGE_END]),
        )
        return data


@utils.check_io(df=SingleNumericColumn, out=SingleNumericColumn)
def rebin_count_dataframe(
    target_schema: AgeSchema,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Rebin a DataFrame to match the target age schema.

    The basic operation is to unstack the DataFrame, multiply by the transform matrix, and stack it back.
    This is done for each value column in the DataFrame.

    Parameters
    ----------
    target_schema
        The target age schema to convert to.
    df
        The DataFrame to rebin.
    Returns
    -------
        A DataFrame with the target age schema in the index and transformed values.
    """
    source_age_schema = AgeSchema.from_dataframe(df)

    transform_matrix = _get_transform_matrix(source_age_schema, target_schema)

    original_index_names = list(df.index.names)

    all_results_series = []

    for val_col in df.columns.tolist():

        # Unstack the DataFrame to get the age groups as columns
        unstacked_series = (
            df[val_col]
            .unstack(level=INPUT_DATA_INDEX_NAMES.AGE_GROUP, fill_value=0)
            .reindex(columns=transform_matrix.columns, fill_value=0)
        )

        # Perform the dot product
        result_matrix_for_col = unstacked_series.dot(transform_matrix.T)

        # Name the column GBD_INDEX_NAMES.AGE_GROUP for re-stacking
        result_matrix_for_col.columns.name = INPUT_DATA_INDEX_NAMES.AGE_GROUP

        # Stack the new age group columns into the index
        stacked_series_for_col = result_matrix_for_col.stack(
            level=INPUT_DATA_INDEX_NAMES.AGE_GROUP
        )
        stacked_series_for_col.name = val_col

        all_results_series.append(stacked_series_for_col)

    output_df = pd.concat(all_results_series, axis=1).reorder_levels(original_index_names)

    return output_df


def _get_transform_matrix(source_schema: AgeSchema, target_schema: AgeSchema) -> pd.DataFrame:
    """
    Get a linear converter mapping between this source schema and target schema.

    Parameters
    ----------
    source_schema
        The source age schema to convert from.
    target_schema
        The target age schema to convert to.
    Returns
    -------
        A dataframe with the target age groups as rows and the source age groups as columns,
        with the values representing the fraction of the source age group that should go into the target age group. Currently,
        this only supports an unweighted allocation--it is therefore NOT appropriate for transforming data in rate space.
    """
    source_age_groups = [group.name for group in source_schema.age_groups]
    target_age_groups = [group.name for group in target_schema.age_groups]

    transform_matrix = pd.DataFrame(0.0, index=target_age_groups, columns=source_age_groups)
    for target_group in target_schema.age_groups:
        for source_group in source_schema.age_groups:
            # Calculate what fraction of the source group should go into the target group
            fraction = source_group.fraction_contained_by(target_group)
            if fraction > 0:
                transform_matrix.loc[target_group.name, source_group.name] = fraction
    return transform_matrix


def format_dataframe_from_age_bin_df(
    data: pd.DataFrame, age_bin_df: pd.DataFrame
) -> pd.DataFrame:
    """Try to merge the age groups with the data. If it fails, just return the data."""
    context_age_schema = AgeSchema.from_dataframe(age_bin_df)
    try:
        return _format_dataframe(context_age_schema, data)
    except ValueError:
        logger.info(
            "Could not resolve age groups. The DataFrame likely has no age data. Returning dataframe as-is."
        )
        return data
