import pandas as pd
import pandera as pa
import pytest
from pandera.errors import SchemaError

from vivarium_testing_utils.automated_validation.data_transformation.data_schema import (
    DrawData,
    SimOutputData,
    SingleNumericColumn,
)


def check_schema_error_batch(
    schema: type[pa.DataFrameModel], invalid_dataframes: list[pd.DataFrame]
) -> None:
    """Helper function to check if a schema raises an error for invalid data."""
    for df in invalid_dataframes:
        with pytest.raises(SchemaError):
            schema.validate(df)


def test_single_numeric_value() -> None:
    """Test that the SingleNumericValue schema correctly validates a DataFrame with a single numeric column."""
    data = pd.DataFrame({"value": [1, 2, 3]}, index=pd.Index([0, 1, 2], name="index"))
    SingleNumericColumn.validate(data)

    # Test that the schema raises an error for invalid data
    wrong_dtype = data.copy()
    wrong_dtype["value"] = "invalid"

    # Test that the schema raises an error for missing columns
    missing_column = data.drop(columns=["value"])

    # Test that the schema raises an error for extra columns
    extra_column = data.copy()
    extra_column["extra_column"] = 0

    check_schema_error_batch(SingleNumericColumn, [wrong_dtype, missing_column, extra_column])


def test_sim_output(
    transition_count_data: pd.DataFrame, person_time_data: pd.DataFrame
) -> None:
    """
    Test that the SimOutputData schema correctly validates the transition count and person time data.
    We don't want it too permissive or too strict. Note that we are implicitly testing that extra index
    levels are OK, because the sample data has the "common_stratify_column" index level.
    """
    assert "common_stratify_column" in transition_count_data.index.names

    # Test that the SimOutputData schema correctly validates the transition count and person time data
    SimOutputData.validate(transition_count_data)
    SimOutputData.validate(person_time_data)

    error_cases = []
    # Test that a missing index level raises an error
    for key in ["measure", "entity_type", "entity", "sub_entity"]:
        missing_index_data = transition_count_data.droplevel(key)
        error_cases.append(missing_index_data)

    # Test that out of order index levels raises an error
    out_of_order_index_data = transition_count_data.swaplevel("entity", "measure")

    # Test that the schema raises an error for extra columns
    extra_column_data = transition_count_data.copy()
    extra_column_data["extra_column"] = 0

    error_cases.extend([out_of_order_index_data, extra_column_data])

    check_schema_error_batch(SimOutputData, error_cases)


def test_draw_data(raw_artifact_disease_incidence: pd.DataFrame) -> None:
    """
    Test that the DrawData schema correctly validates the artifact disease incidence data.
    """

    # Test that the DrawData schema correctly validates the artifact disease incidence data
    DrawData.validate(raw_artifact_disease_incidence)

    # Test that a missing column does not raise an error
    missing_column_data = raw_artifact_disease_incidence.drop(columns=["draw_0"])
    DrawData.validate(missing_column_data)

    # Test that an extra column raises an error
    extra_column_data = raw_artifact_disease_incidence.copy()
    extra_column_data["extra_column"] = 0

    # Test that the schema raises an error for invalid data
    wrong_dtype = raw_artifact_disease_incidence.copy()
    wrong_dtype["draw_0"] = "invalid"

    check_schema_error_batch(DrawData, [extra_column_data, wrong_dtype])
