from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pandas as pd
import pytest
import tables

# Run the pandas 2 suite with pandas 3 semantics (copy-on-write, str dtype) so
# every CI leg exercises the future behavior ahead of the unpin (MIC-6773).
# Guarded to pandas >=2.1, where these options exist.
_PANDAS_VERSION = tuple(int(part) for part in pd.__version__.split(".")[:2])
if (2, 1) <= _PANDAS_VERSION < (3, 0):
    pd.options.mode.copy_on_write = True
    pd.options.future.infer_string = True


@pytest.fixture
def test_data_dir() -> Path:
    """Directory containing binary test fixtures (e.g. artifact.hdf)."""
    data_dir = Path(__file__).resolve().parent / "test_data"
    assert data_dir.exists(), "Test directory structure is broken"
    return data_dir


@pytest.fixture
def hdf_file_path(tmp_path: Path, test_data_dir: Path) -> Path:
    """Path to a writable copy of the canonical test artifact.

    The fixture file contains the following object tree::

        / (RootGroup) ''
        /cause (Group) ''
        /population (Group) ''
        /population/age_bins (Group) ''
        /population/age_bins/table (Table(23,), shuffle, zlib(9)) ''
        /population/structure (Group) ''
        /population/structure/table (Table(1863,), shuffle, zlib(9)) ''
        /population/theoretical_minimum_risk_life_expectancy (Group) ''
        /population/theoretical_minimum_risk_life_expectancy/table (Table(10502,), shuffle, zlib(9)) ''
        /population/structure/meta (Group) ''
        /population/structure/meta/values_block_1 (Group) ''
        /population/structure/meta/values_block_1/meta (Group) ''
        /population/structure/meta/values_block_1/meta/table (Table(3,), shuffle, zlib(9)) ''
        /cause/all_causes (Group) ''
        /cause/all_causes/restrictions (EArray(166,)) ''
    """
    # Make a temporary writable copy so tests can mutate without polluting
    # the committed fixture.
    p = tmp_path / "artifact.hdf"
    with tables.open_file(str(test_data_dir / "artifact.hdf")) as file:
        file.copy_file(str(p), overwrite=True)
    return p


@pytest.fixture
def hdf_file(hdf_file_path: Path) -> Generator[tables.file.File, None, None]:
    """An open ``tables.File`` handle to the test artifact (read-only)."""
    with tables.open_file(str(hdf_file_path)) as file:
        yield file
