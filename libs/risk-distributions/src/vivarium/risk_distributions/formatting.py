from typing import Any, Dict, List, Tuple, TypeVar, Union

import numpy as np
import pandas as pd

Parameter = TypeVar("Parameter", np.ndarray, pd.Series, List, Tuple, int, float)
Parameters = TypeVar(
    "Parameters", np.ndarray, pd.Series, pd.DataFrame, List, Tuple, Dict[str, Parameter]
)


def cast_to_series(mean: Parameter, sd: Parameter) -> (pd.Series, pd.Series):
    """Casts mean and standard deviation data to identically indexed series."""

    if (
        not isinstance(mean, (int, float))
        and len(mean) == 0
        or not isinstance(sd, (int, float))
        and len(sd) == 0
    ):
        raise ValueError("Empty data structure provided for mean or sd.")

    mean_length = 1 if isinstance(mean, (int, float)) else len(mean)
    sd_length = 1 if isinstance(sd, (int, float)) else len(sd)
    if mean_length != sd_length:
        raise ValueError(
            "You must provide the same number of values for mean and standard deviation."
        )

    if isinstance(mean, pd.Series) and isinstance(sd, pd.Series):
        if np.any(mean.index != sd.index):
            raise ValueError(
                "If providing mean and sd as pandas series, they must be identically indexed."
            )
    elif isinstance(mean, pd.Series):
        sd = pd.Series(sd, index=mean.index)
    elif isinstance(sd, pd.Series):
        mean = pd.Series(mean, index=sd.index)
    else:
        mean, sd = pd.Series(mean), pd.Series(sd)

    return mean, sd


def format_data(data: Parameters, required_columns: List[Any], measure: str) -> pd.DataFrame:
    """Formats parameter data into a dataframe."""
    if isinstance(data, np.ndarray):
        data = format_array(data, required_columns, measure)
    elif isinstance(data, pd.Series):
        data = format_series(data, required_columns, measure)
    elif isinstance(data, pd.DataFrame):
        data = format_data_frame(data, required_columns, measure)
    elif isinstance(data, (list, tuple)):
        data = format_list_like(data, required_columns, measure)
    elif isinstance(data, dict):
        data = format_dict(data, required_columns, measure)

    return data


def format_array(data: np.ndarray, required_columns: List[any], measure: str) -> pd.DataFrame:
    """Transforms 1d and 2d arrays into dataframes with columns for the
    parameters and (possibly) rows for each parameter variation."""
    if not data.size:
        raise ValueError(f"No data provided for {measure}.")

    if len(required_columns) == 1:
        # We can accept row or column vectors
        if len(data.shape) == 1:  # column vector, works directly
            data = pd.DataFrame(data, columns=required_columns)
        elif len(data.shape) == 2:  # row vector
            if data.shape[0] != 1:
                raise ValueError(
                    f"2D array provided for {measure} where values for "
                    f"a single parameter were expected."
                )
            data = pd.DataFrame(data[0], columns=required_columns)
        else:
            raise ValueError(f"Invalid data shape {data.shape} provided for {measure}.")

    else:
        # We can take row or column vectors or a 2D array
        if len(data.shape) == 1:  # Column vector
            if data.size != len(required_columns):
                raise ValueError(
                    f"{data.size} values provided for {measure} when "
                    f"{len(required_columns)} were expected."
                )
            data = pd.DataFrame([data], columns=required_columns)
        elif len(data.shape) == 2 and data.shape[0] == 1:  # Row vector
            if data.size != len(required_columns):
                raise ValueError(
                    f"{data.size} values provided for {measure} when "
                    f"{len(required_columns)} were expected."
                )
            data = pd.DataFrame(data, columns=required_columns)
        elif len(data.shape) == 2:  # 2D array
            # Presume a column for each parameter (to handle square case), but accept rows as well.
            if data.shape[1] == len(required_columns):
                data = pd.DataFrame(data, columns=required_columns)
            elif data.shape[0] == len(required_columns):
                data = pd.DataFrame(data.T, columns=required_columns)
            else:
                raise ValueError(
                    f"Expected one axis in {measure} data to have length {len(required_columns)} "
                    f"but data with shape {data.shape} was provided."
                )
        else:
            raise ValueError(f"Invalid data shape {data.shape} provided for {measure}.")

    return data


def format_series(data: pd.Series, required_columns: List[Any], measure: str) -> pd.DataFrame:
    """Transforms series data into dataframes with columns for the
    parameters and (possibly) rows for each parameter variation."""
    if data.empty:
        raise ValueError(f"No data provided for {measure}.")

    if len(required_columns) == 1:  # Interpret the series as parameter variations
        data = pd.DataFrame(data, columns=required_columns)
    else:  # Interpret the series as a dict or array of single parameter entries
        if len(data) != len(required_columns):
            raise ValueError(
                f"{len(data)} values provided for {measure} when "
                f"{len(required_columns)} were expected."
            )
        if set(data.index) == set(required_columns):
            data = pd.DataFrame([data.values], columns=data.index)
        else:  # Interpret by order
            data = pd.DataFrame([data.values], columns=required_columns)

    return data


def format_data_frame(
    data: pd.DataFrame, required_columns: List[Any], measure: str
) -> pd.DataFrame:
    """Checks that input data provided as a dataframe is properly formatted."""
    if data.empty:
        raise ValueError(f"No data provided for {measure.lower()}.")

    missing_cols = set(required_columns).difference(data.columns)
    if missing_cols:
        raise ValueError(
            f"{measure} data provided is missing "
            f"columns {set(required_columns).difference(data.columns)}."
        )

    extra_cols = data.columns.difference(required_columns)
    if np.any(extra_cols):
        raise ValueError(f"{measure} data has extra columns: {extra_cols}.")

    return data


def format_list_like(
    data: Union[List, Tuple], required_columns: List[Any], measure: str
) -> pd.DataFrame:
    """Transforms 1d and 2d lists or tuples into dataframes with columns for
    the parameters and (possibly) rows for each parameter variation."""
    data = np.array(data)
    return format_array(data, required_columns, measure)


def format_dict(
    data: Dict[str, Parameter], required_columns: List[Any], measure: str
) -> pd.DataFrame:
    """Transform dictionaries with scalar or list-like values into dataframes
    with columns for the parameters and (possibly) rows for each parameter
    variation."""
    if set(data.keys()) != set(required_columns):
        raise ValueError(
            f"If passing values for {measure} as a dictionary, you "
            f"must supply only keys {required_columns}."
        )
    data = {key: list(val) for key, val in data.items()}
    if len(set(len(val) for val in data.values())) != 1:
        raise ValueError(
            f"If passing values for {measure} as a dictionary, you "
            "must specify the same number of values for each parameter."
        )
    data = pd.DataFrame(data)
    return data


def format_call_data(call_data, parameters):
    if len(parameters) != 1:
        if isinstance(call_data, pd.Series) and np.any(call_data.index != parameters.index):
            raise ValueError(
                "If providing call_data as a series it must "
                "be indexed consistently with the parameter data."
            )
        call_data = pd.Series(call_data, index=parameters.index)
    else:
        call_data = pd.Series(call_data)
        parameters = parameters.reindex(call_data.index, method="nearest")

    return call_data, parameters
