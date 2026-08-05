import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import tables
from pytest_mock import MockerFixture
from tables.file import File
from tables.nodes import filenode

from vivarium.artifact import _hdf
from vivarium.artifact.entity_key import EntityKey

_KEYS = [
    "population.age_bins",
    "population.structure",
    "population.theoretical_minimum_risk_life_expectancy",
    "cause.all_causes.restrictions",
    "metadata.versions",
    "metadata.locations",
    "metadata.keyspace",
]


@pytest.fixture
def hdf_keys() -> list[str]:
    return _KEYS


@pytest.fixture(params=_KEYS)
def hdf_key(request: pytest.FixtureRequest) -> str:
    assert isinstance(request.param, str)
    return request.param


@pytest.fixture(
    params=[
        "totally.new.thing",
        "other.new_thing",
        "cause.sort_of_new",
        "cause.also.new",
        "cause.all_cause.kind_of_new",
    ]
)
def mock_key(request: pytest.FixtureRequest) -> EntityKey:
    return EntityKey(request.param)


@pytest.fixture(params=[[], {}, ["data"], {"thing": "value"}, "bananas"])
def json_data(request: pytest.FixtureRequest) -> Any:
    return request.param


def test_touch_no_file(mocker: MockerFixture) -> None:
    path = Path("not/an/existing/path.hdf")
    tables_mock = mocker.patch("vivarium.artifact._hdf.tables")

    _hdf.touch(path)
    tables_mock.open_file.assert_called_once_with(str(path), mode="w")
    tables_mock.reset_mock()


def test_touch_exists_but_not_hdf_file_path(hdf_file_path: Path) -> None:
    dir_path = Path(hdf_file_path).parent
    with pytest.raises(ValueError):
        _hdf.touch(dir_path)
    non_hdf_path = Path(hdf_file_path).parent / "test.txt"
    with pytest.raises(ValueError):
        _hdf.touch(non_hdf_path)


def test_touch_existing_file(tmpdir: Path) -> None:
    path = f"{str(tmpdir)}/test.hdf"

    _hdf.touch(path)
    _hdf.write(path, EntityKey("test.key"), "data")
    assert _hdf.get_keys(path) == ["test.key"]

    # should wipe out and make it again
    _hdf.touch(path)
    assert _hdf.get_keys(path) == []


def test_write_df(hdf_file_path: Path, mock_key: EntityKey, mocker: MockerFixture) -> None:
    df_mock = mocker.patch("vivarium.artifact._hdf._write_pandas_data")
    data = pd.DataFrame(np.random.random((10, 3)), columns=["a", "b", "c"], index=range(10))

    _hdf.write(hdf_file_path, mock_key, data)

    df_mock.assert_called_once_with(hdf_file_path, mock_key, data)


def test_write_json(
    hdf_file_path: Path, mock_key: EntityKey, json_data: list[str], mocker: MockerFixture
) -> None:
    json_mock = mocker.patch("vivarium.artifact._hdf._write_json_blob")
    _hdf.write(hdf_file_path, mock_key, json_data)
    json_mock.assert_called_once_with(hdf_file_path, mock_key, json_data)


def test_load(hdf_file_path: Path, hdf_key: str) -> None:
    key = EntityKey(hdf_key)
    data = _hdf.load(hdf_file_path, key, filter_terms=None, column_filters=None)
    if "restrictions" in key or "versions" in key:
        assert isinstance(data, dict)
    elif "metadata" in key:
        assert isinstance(data, list)
    else:
        assert isinstance(data, pd.DataFrame)


def test_load_with_invalid_filters(hdf_file_path: Path, hdf_key: str) -> None:
    key = EntityKey(hdf_key)
    data = _hdf.load(hdf_file_path, key, filter_terms=["fake_filter==0"], column_filters=None)
    if "restrictions" in key or "versions" in key:
        assert isinstance(data, dict)
    elif "metadata" in key:
        assert isinstance(data, list)
    else:
        assert isinstance(data, pd.DataFrame)


def test_load_with_valid_filters(hdf_file_path: Path, hdf_key: str) -> None:
    key = EntityKey(hdf_key)
    data = _hdf.load(hdf_file_path, key, filter_terms=["year == 2006"], column_filters=None)
    if "restrictions" in key or "versions" in key:
        assert isinstance(data, dict)
    elif "metadata" in key:
        assert isinstance(data, list)
    else:
        assert isinstance(data, pd.DataFrame)
        if "year" in data.columns:
            assert set(data.year) == {2006}


def test_load_filter_empty_data_frame_index(hdf_file_path: Path) -> None:
    key = EntityKey("cause.test.prevalence")
    data = pd.DataFrame(data={"age": range(10), "year": range(10), "draw": range(10)})
    data = data.set_index(list(data.columns))

    _hdf._write_pandas_data(hdf_file_path, key, data)
    loaded_data = _hdf.load(
        hdf_file_path, key, filter_terms=["year == 4"], column_filters=None
    )
    loaded_data = loaded_data.reset_index()
    assert loaded_data.year.unique() == 4


def test_remove(hdf_file_path: Path, hdf_key: str) -> None:
    key = EntityKey(hdf_key)
    _hdf.remove(hdf_file_path, key)
    with tables.open_file(str(hdf_file_path)) as file:
        assert key.path not in file


def test_get_keys(hdf_file_path: Path, hdf_keys: list[str]) -> None:
    assert sorted(_hdf.get_keys(hdf_file_path)) == sorted(hdf_keys)


def test_write_json_blob(
    hdf_file_path: Path, mock_key: EntityKey, json_data: list[str]
) -> None:
    _hdf._write_json_blob(hdf_file_path, mock_key, json_data)

    with tables.open_file(str(hdf_file_path)) as file:
        node = file.get_node(mock_key.path)
        with filenode.open_node(node) as file_node:
            data = json.load(file_node)
            assert data == json_data


def test_write_empty_data_frame(hdf_file_path: Path) -> None:
    key = EntityKey("cause.test.prevalence")
    data = pd.DataFrame(columns=("age", "year", "sex", "draw", "location", "value"))

    with pytest.raises(ValueError):
        _hdf._write_pandas_data(hdf_file_path, key, data)


def test_write_empty_data_frame_index(hdf_file_path: Path) -> None:
    key = EntityKey("cause.test.prevalence")
    data = pd.DataFrame(data={"age": range(10), "year": range(10), "draw": range(10)})
    data = data.set_index(list(data.columns))

    _hdf._write_pandas_data(hdf_file_path, key, data)
    written_data = pd.read_hdf(hdf_file_path, key.path)
    written_data = written_data.set_index(
        list(written_data)
    )  # write resets index. only calling load undoes it
    assert written_data.equals(data)


def test_write_load_empty_data_frame_index(hdf_file_path: Path) -> None:
    key = EntityKey("cause.test.prevalence")
    data = pd.DataFrame(data={"age": range(10), "year": range(10), "draw": range(10)})
    data = data.set_index(list(data.columns))

    _hdf._write_pandas_data(hdf_file_path, key, data)
    loaded_data = _hdf.load(hdf_file_path, key, filter_terms=None, column_filters=None)
    assert loaded_data.equals(data)


def test_write_data_frame(hdf_file_path: Path) -> None:
    key = EntityKey("cause.test.prevalence")
    # A multi-indexed DataFrame with a "draw" column so the where="draw == 0"
    # filter test below has something to slice on. The exact axes / values
    # don't matter for the round-trip; we just need a non-trivial frame.
    index = pd.MultiIndex.from_product(
        [range(5), range(2020, 2022), [0, 1], ["Kenya"]],
        names=["age", "year", "draw", "location"],
    )
    data = pd.DataFrame(
        {"value": [random.choice([0, 1]) for _ in range(len(index))]},
        index=index,
    )

    _hdf._write_pandas_data(hdf_file_path, key, data)

    written_data = pd.read_hdf(hdf_file_path, key.path)
    assert isinstance(written_data, pd.DataFrame)
    pd.testing.assert_frame_equal(written_data, data)

    filter_terms = "draw == 0"
    written_data = pd.read_hdf(hdf_file_path, key.path, where=filter_terms)

    draw_0_data = data.xs(0, level="draw", drop_level=False)
    assert isinstance(written_data, pd.DataFrame)
    assert isinstance(draw_0_data, pd.DataFrame)
    pd.testing.assert_frame_equal(written_data, draw_0_data)


def test_get_keys_from_node(hdf_file: File, hdf_keys: list[str]) -> None:
    assert sorted(_hdf._get_keys_from_node(hdf_file.root)) == sorted(hdf_keys)


def test_get_node_name(hdf_file: File, hdf_key: str) -> None:
    key = EntityKey(hdf_key)
    assert _hdf._get_node_name(hdf_file.get_node(key.path)) == key.measure


def test_get_valid_filter_terms_all_invalid(hdf_key: str, hdf_file: File) -> None:
    node = hdf_file.get_node(EntityKey(hdf_key).path)
    if not isinstance(node, tables.earray.EArray):
        columns = node.table.colnames
        invalid_filter_terms = _construct_no_valid_filters(columns)
        assert _hdf._get_valid_filter_terms(invalid_filter_terms, columns) is None


def test_get_valid_filter_terms_all_valid(hdf_key: str, hdf_file: File) -> None:
    node = hdf_file.get_node(EntityKey(hdf_key).path)
    if not isinstance(node, tables.earray.EArray):
        columns = node.table.colnames
        valid_filter_terms = _construct_all_valid_filters(columns)
        result = _hdf._get_valid_filter_terms(valid_filter_terms, columns)
        assert result is not None
        assert set(result) == set(valid_filter_terms)


def test_get_valid_filter_terms_some_valid(hdf_key: str, hdf_file: File) -> None:
    node = hdf_file.get_node(EntityKey(hdf_key).path)
    if not isinstance(node, tables.earray.EArray):
        columns = node.table.colnames
        invalid_filter_terms = _construct_no_valid_filters(columns)
        valid_filter_terms = _construct_all_valid_filters(columns)
        all_terms = invalid_filter_terms + valid_filter_terms
        result = _hdf._get_valid_filter_terms(all_terms, columns)
        assert result is not None
        assert set(result) == set(valid_filter_terms)


def test_get_valid_filter_terms_no_terms() -> None:
    assert _hdf._get_valid_filter_terms(None, []) is None


def _construct_no_valid_filters(columns: list[str]) -> list[str]:
    fake_cols = [
        c[1:] for c in columns
    ]  # strip out the first char to make a list of all fake cols
    terms = [c + " <= 0" for c in fake_cols]
    return _complicate_terms_to_parse(terms)


def _construct_all_valid_filters(columns: list[str]) -> list[str]:
    terms = [
        c + "=0" for c in columns
    ]  # assume c is numeric - we won't actually apply filter
    return _complicate_terms_to_parse(terms)


def _complicate_terms_to_parse(terms: list[str]) -> list[str]:
    n_terms = len(terms)
    if n_terms > 1:
        # throw in some parens and ifs/ands
        term_1 = "(" + " & ".join(terms[: (n_terms // 2 + n_terms % 2)]) + ")"
        term_2 = "(" + " | ".join(terms[(n_terms // 2 + n_terms % 2) :]) + ")"
        terms = [term_1, term_2] + terms
    return ["(" + t + ")" for t in terms]
