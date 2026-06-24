from typing import Generator

import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger


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
