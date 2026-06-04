"""
==================
Results Formatting
==================

Helpers for shaping measure results before they are returned from the results
manager and written to disk.

"""

from __future__ import annotations

import pandas as pd
from pandas.api.types import is_object_dtype

from vivarium.engine.framework.results.observation import VALUE_COLUMN


def cast_non_value_columns_to_categorical(
    df: pd.DataFrame,
    orderings: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Return a copy of ``df`` with eligible non-value columns stored as Categoricals.

    A non-value column (any column other than the value column ``VALUE_COLUMN``)
    is cast to a pandas ``Categorical`` only when it is a categorical-like column,
    so that once the frame is written to parquet and reloaded, downstream
    ``groupby`` operations on those columns are fast and memory-light. A column is
    cast when either:

    * its name appears in ``orderings`` -- it becomes an *ordered* Categorical
      using that category order, so values compare and sort meaningfully (e.g.
      ``age_group``) and summary tables and plots get a consistent ordering; or
    * it is of ``object`` dtype or already Categorical -- it becomes an
      *unordered* Categorical.

    All other non-value columns (numeric, datetime, timedelta, bool, etc.) are
    left unchanged, so downstream arithmetic on them (e.g. adding a ``Timedelta``
    to a datetime ``event_time`` column) keeps working. The value column is
    likewise never modified.

    Parameters
    ----------
    df
        Formatted results for a single measure: the value column plus zero or
        more non-value (stratification) columns. Non-value columns may already
        be of Categorical dtype.
    orderings
        Maps a column name to the ordered list of its categories, typically the
        registered stratification categories. A non-value column absent from the
        mapping is cast to an unordered Categorical only if it is of ``object``
        dtype or already Categorical; otherwise it is left unchanged.

    Returns
    -------
        A copy of ``df`` with eligible non-value columns cast to (ordered)
        Categoricals and all other columns left unchanged.
    """
    if orderings is None:
        orderings = {}

    df = df.copy()

    for col in df.columns:
        if col == VALUE_COLUMN:
            continue
        if col in orderings:
            dtype = pd.CategoricalDtype(categories=orderings[col], ordered=True)
        elif is_object_dtype(df[col]) or isinstance(df[col].dtype, pd.CategoricalDtype):
            dtype = pd.CategoricalDtype(ordered=False)
        else:
            # Numeric, datetime, timedelta, bool, etc. are left unchanged.
            continue
        df[col] = df[col].astype(dtype)

    return df
