from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd
import pytest

# Run the pandas 2 suite with pandas 3 semantics (copy-on-write, str dtype) so
# every CI leg exercises the future behavior ahead of the unpin (MIC-6773).
# Guarded to pandas >=2.1, where these options exist. set_option (rather
# than attribute access) keeps mypy happy with the pinned pandas-stubs.
_PANDAS_VERSION = tuple(int(part) for part in pd.__version__.split(".")[:2])
if (2, 1) <= _PANDAS_VERSION < (3, 0):
    pd.set_option("mode.copy_on_write", True)
    pd.set_option("future.infer_string", True)


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
