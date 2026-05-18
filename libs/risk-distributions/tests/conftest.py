import numpy as np
import pandas as pd
import pytest


def assert_equal(a, b):
    if isinstance(a, (pd.Series, pd.DataFrame)):
        assert a.equals(b)
    elif isinstance(a, np.ndarray):
        assert np.allclose(a, b)
    else:
        assert a == b
