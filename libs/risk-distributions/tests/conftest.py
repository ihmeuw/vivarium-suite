from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd
import pytest


def assert_equal(a: object, b: object) -> None:
    """Assert that two values are equal, dispatching on their runtime type."""
    if isinstance(a, pd.Series):
        # b shares a's runtime type; mypy can't narrow it from a's isinstance.
        assert a.equals(cast("pd.Series[Any]", b))
    elif isinstance(a, pd.DataFrame):
        # b shares a's runtime type; mypy can't narrow it from a's isinstance.
        assert a.equals(cast("pd.DataFrame", b))
    elif isinstance(a, np.ndarray):
        # b shares a's runtime type; mypy can't narrow it from a's isinstance.
        assert np.allclose(a, cast("np.ndarray[Any, Any]", b))
    else:
        assert a == b
