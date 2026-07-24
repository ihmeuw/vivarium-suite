from typing import Generator

import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger

# Run the pandas 2 suite with pandas 3 semantics (copy-on-write, str dtype) so
# every CI leg exercises the future behavior ahead of the unpin (MIC-6773).
# Guarded to pandas >=2.1, where these options exist. set_option (rather
# than attribute access) keeps mypy happy with the pinned pandas-stubs.
_PANDAS_VERSION = tuple(int(part) for part in pd.__version__.split(".")[:2])
if (2, 1) <= _PANDAS_VERSION < (3, 0):
    pd.set_option("mode.copy_on_write", True)
    pd.set_option("future.infer_string", True)


@pytest.fixture
def caplog(caplog: LogCaptureFixture) -> Generator[LogCaptureFixture, None, None]:
    handler_id = logger.add(
        caplog.handler,
        format="{message}",
        level=0,
        filter=lambda record: record["level"].no >= caplog.handler.level,
        enqueue=False,  # Set to 'True' if your test is spawning child processes.
    )
    yield caplog
    logger.remove(handler_id)


@pytest.fixture
def simple_demographic_index() -> pd.MultiIndex:
    return pd.MultiIndex.from_tuples(
        [
            ("Male", 5, 10),
            ("Male", 10, 15),
            ("Female", 5, 10),
            ("Female", 10, 15),
        ],
        names=["sex", "age_start", "age_end"],
    )


@pytest.fixture
def observed_proportion_dataframe(simple_demographic_index: pd.MultiIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {"value": [0.10, 0.25, 0.50, 0.75]},
        index=simple_demographic_index,
    )
