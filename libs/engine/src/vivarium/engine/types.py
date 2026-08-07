from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from typing import SupportsFloat as Numeric
from typing import TypeGuard, Union

import numpy as np
import numpy.typing as npt
import pandas as pd

if TYPE_CHECKING:
    from vivarium.engine.framework.engine import Builder

NumericArray = npt.NDArray[np.number[Any]]

Time = pd.Timestamp | datetime
Timedelta = pd.Timedelta | timedelta
ClockTime = Time | int
ClockStepSize = Timedelta | int

ScalarValue = Numeric | Timedelta | Time
DataFrameMapping = Mapping[str, list[ScalarValue] | list[str]]
LookupTableData = (
    ScalarValue
    | str
    | pd.DataFrame
    | pd.Series  # type: ignore [type-arg]
    | list[ScalarValue]
    | tuple[ScalarValue, ...]
    | DataFrameMapping
)


def has_named_row_index(
    data: LookupTableData,
) -> TypeGuard[pd.DataFrame | pd.Series]:  # type: ignore [type-arg]
    """Return True if ``data`` carries its lookup attributes on the row index."""
    if isinstance(data, pd.Series):
        return True
    if isinstance(data, pd.DataFrame):
        return any(name is not None for name in data.index.names)
    return False


DataInput = LookupTableData | str | Callable[["Builder"], LookupTableData]

# TODO: For some of the uses of NumberLike, we probably want a TypeVar here instead.
NumberLike = Union[
    NumericArray,
    # TODO: Parameterizing pandas objects fails below python 3.12
    pd.Series,  # type: ignore [type-arg]
    pd.DataFrame,
    float,
    int,
]

VectorMapper = Callable[[pd.DataFrame], pd.Series]  # type: ignore [type-arg]
ScalarMapper = Callable[[pd.Series], str]  # type: ignore [type-arg]
