"""Tests for the primary-data handoff contract (``frontend.handoff``)."""

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pytask import PathNode, TaskOutcome

from tests.backend.test_wiring import _Code
from tests.frontend.handoff_example import pipeline
from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry, WiringError
from vivarium.workflow.frontend.handoff import HandoffError, handoff_inputs, handoff_key
from vivarium.workflow.frontend.python_step import python_step

_COLUMNS = ["location_id", "year_id", "coverage"]
_FILES = {"coverage.csv": _COLUMNS}
_CSV_AND_PARQUET = {"coverage.csv": _COLUMNS, "draws.parquet": _COLUMNS}
_COVERAGE = pd.DataFrame(
    {"location_id": [1, 1], "year_id": [2020, 2021], "coverage": [0.4, 0.5]}
)
_FILE_ACCESS: list[tuple[object, str]] = [
    (Path, "exists"),
    (Path, "is_dir"),
    (Path, "is_file"),
    (Path, "iterdir"),
    (pd, "read_csv"),
    (pq, "read_schema"),
]
_UNSUPPORTED = "unsupported Parquet feature"


def _primary(root: Path, location: str) -> Path:
    """Return ``location``'s primary directory under ``root``, creating it."""
    primary = root / location / "primary"
    primary.mkdir(parents=True, exist_ok=True)
    return primary


def _deliver(root: Path, location: str, name: str, frame: pd.DataFrame = _COVERAGE) -> Path:
    """Write ``frame`` as ``location``'s file ``name``, in the format its suffix names."""
    path = _primary(root, location) / name
    if path.suffix == ".parquet":
        frame.to_parquet(path)
    else:
        frame.to_csv(path, index=False)
    return path


def _handoff_error(root: Path, locations: list[str], files: dict[str, list[str]]) -> str:
    """Return handoff_inputs' HandoffError message, showing ``root`` as <root>."""
    with pytest.raises(HandoffError) as excinfo:
        handoff_inputs(root, locations, files)
    return str(excinfo.value).replace(str(root), "<root>")


def _reader_message(path: Path) -> str:
    """Return the message the CSV or Parquet column reader raises for ``path``."""
    try:
        if path.suffix == ".parquet":
            pq.read_schema(str(path))
        else:
            pd.read_csv(path, nrows=0)
    except (ValueError, OSError) as error:
        return str(error)
    raise AssertionError(f"{path} is readable")


def _line_of(message: str, fragment: str) -> int:
    """Return the index of the one line of ``message`` that contains ``fragment``."""
    [index] = [i for i, line in enumerate(message.splitlines()) if fragment in line]
    return index


def _problem_line(message: str, fragment: str) -> str:
    """Return the one line of ``message`` that contains ``fragment``, lowercased."""
    return message.splitlines()[_line_of(message, fragment)].lower()


def _forbid_file_access(*args: object, **kwargs: object) -> None:
    """Fail the test because a file was touched before the definition was checked."""
    raise AssertionError("a file was touched before the definition was checked")


def _assert_rejected_before_reading(
    root: Path, locations: list[str], files: Mapping[str, Sequence[str]]
) -> None:
    """Assert handoff_inputs raises ValueError without touching any file."""
    with pytest.MonkeyPatch.context() as patch:
        for owner, attribute in _FILE_ACCESS:
            patch.setattr(owner, attribute, _forbid_file_access)
        with pytest.raises(ValueError):
            handoff_inputs(root, locations, files)


def _unsupported_parquet(*args: object, **kwargs: object) -> None:
    """Raise pyarrow's error for a Parquet feature it does not implement."""
    raise pa.ArrowNotImplementedError(_UNSUPPORTED)


def _mean_coverage(coverage: Path) -> pd.DataFrame:
    """Average the coverage column of the file at ``coverage``."""
    return pd.DataFrame({"mean_coverage": [pd.read_csv(coverage)["coverage"].mean()]})


def _mean_step(root: Path, key: str) -> Task:
    """Validate ethiopia's handoff under ``root`` and return a step averaging ``key``."""
    inputs = handoff_inputs(root, ["ethiopia"], _FILES)
    registry = ArtifactRegistry(root / "out", "run1", root_inputs=inputs)
    return python_step(
        _mean_coverage, inputs={"coverage": key}, produces="mean.parquet", registry=registry
    )


def test_handoff_key_is_the_path_under_the_root() -> None:
    """handoff_key joins the location, the primary directory, and the file name."""
    assert handoff_key("ethiopia", "coverage.csv") == "ethiopia/primary/coverage.csv"


def test_valid_handoff_returns_every_location_and_file(tmp_path: Path) -> None:
    """Every declared .csv and .parquet file of every location is returned at its path, keyed by handoff_key."""
    expected = {
        handoff_key(location, name): _deliver(tmp_path, location, name)
        for location in ["ethiopia", "nigeria"]
        for name in _CSV_AND_PARQUET
    }

    assert handoff_inputs(tmp_path, ["ethiopia", "nigeria"], _CSV_AND_PARQUET) == expected


def test_missing_file_names_the_location_file_and_expected_path(tmp_path: Path) -> None:
    """A missing file is reported with the location, the file name, and the expected absolute path."""
    _primary(tmp_path, "ethiopia")

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    assert "<root>/ethiopia/primary/coverage.csv" in message
    line = _problem_line(message, "<root>/ethiopia/primary/coverage.csv")
    assert "missing" in line
    assert "could not be read" not in line


def test_missing_file_names_a_same_stem_file_with_another_suffix(tmp_path: Path) -> None:
    """When coverage.csv is missing but coverage.xlsx is present, the message names the file that was found."""
    (_primary(tmp_path, "ethiopia") / "coverage.xlsx").write_bytes(b"PK\x03\x04")

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    assert "coverage.xlsx" in message


@pytest.mark.parametrize(
    "location_dir_exists", [False, True], ids=["no-location-dir", "no-primary-dir"]
)
def test_missing_location_directory_reports_each_declared_file(
    tmp_path: Path, location_dir_exists: bool
) -> None:
    """A location with no directory, or no primary directory, has each declared file reported missing."""
    if location_dir_exists:
        (tmp_path / "ethiopia").mkdir()

    message = _handoff_error(tmp_path, ["ethiopia"], _CSV_AND_PARQUET)

    for name in _CSV_AND_PARQUET:
        expected = f"<root>/ethiopia/primary/{name}"
        assert expected in message
        line = _problem_line(message, expected)
        assert "missing" in line
        assert "could not be read" not in line


def test_directory_in_place_of_a_file_is_reported_missing(tmp_path: Path) -> None:
    """A directory named like a declared file is reported missing, not read."""
    primary = _primary(tmp_path, "ethiopia")
    missing = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    (primary / "coverage.csv").mkdir()

    assert _handoff_error(tmp_path, ["ethiopia"], _FILES) == missing


@pytest.mark.parametrize("name", ["rates.csv", "rates.parquet"])
def test_missing_columns_are_listed_with_the_columns_present(
    tmp_path: Path, name: str
) -> None:
    """A CSV or Parquet file lacking required columns is reported with the missing and the present columns."""
    frame = _COVERAGE.rename(columns={"coverage": "mean_value"})
    _deliver(tmp_path, "ethiopia", name, frame)

    message = _handoff_error(tmp_path, ["ethiopia"], {name: _COLUMNS})

    assert f"<root>/ethiopia/primary/{name}" in message
    line = _problem_line(message, f"<root>/ethiopia/primary/{name}")
    assert "'coverage'" in line and "'mean_value'" in line
    assert line.index("'coverage'") < line.index("'mean_value'")
    assert line.count("'location_id'") == line.count("'year_id'") == 1


@pytest.mark.parametrize(
    "name, content",
    [
        ("coverage.csv", b""),
        ("coverage.csv", b"\x89PNG\r\n\x1a\n"),
        ("coverage.parquet", b"location_id,year_id,coverage\n1,2020,0.4\n"),
        ("coverage.parquet", b""),
    ],
)
def test_unreadable_files_are_reported_with_the_reader_message(
    tmp_path: Path, name: str, content: bytes
) -> None:
    """A 0-byte CSV, a binary CSV, a non-Parquet .parquet, and a 0-byte .parquet are each reported as could not be read."""
    path = _primary(tmp_path, "ethiopia") / name
    path.write_bytes(content)

    message = _handoff_error(tmp_path, ["ethiopia"], {name: _COLUMNS})

    assert "could not be read" in message
    assert f"<root>/ethiopia/primary/{name}" in message
    assert _reader_message(path).replace(str(tmp_path), "<root>") in message


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_file_without_read_permission_is_reported_unreadable(tmp_path: Path) -> None:
    """A file the user cannot read is reported as could not be read (skipped when running as root)."""
    _deliver(tmp_path, "ethiopia", "coverage.csv").chmod(0)

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    assert "could not be read" in message
    assert "<root>/ethiopia/primary/coverage.csv" in message


def test_all_problems_are_raised_together_by_location(tmp_path: Path) -> None:
    """Problems across several locations and files are raised as one HandoffError with a count and one line each."""
    _deliver(tmp_path, "ethiopia", "draws.parquet", _COVERAGE.drop(columns="coverage"))
    (_primary(tmp_path, "nigeria") / "coverage.csv").write_bytes(b"")
    _deliver(tmp_path, "nigeria", "draws.parquet")

    message = _handoff_error(tmp_path, ["ethiopia", "nigeria"], _CSV_AND_PARQUET)

    ethiopia = [
        _line_of(message, f"<root>/ethiopia/primary/{name}") for name in _CSV_AND_PARQUET
    ]
    nigeria = _line_of(message, "<root>/nigeria/primary/coverage.csv")
    assert max(ethiopia) < nigeria
    assert "nigeria/primary/draws.parquet" not in message
    assert re.search(r"\b3\b", message)


def test_handoff_error_is_a_wiring_error(tmp_path: Path) -> None:
    """HandoffError subclasses WiringError, so callers that handle wiring errors handle it too."""
    assert issubclass(HandoffError, WiringError)
    with pytest.raises(WiringError):
        handoff_inputs(tmp_path, ["ethiopia"], _FILES)


@pytest.mark.parametrize("name", ["coverage.csv", "coverage.parquet"])
def test_extra_columns_and_any_column_order_are_accepted(tmp_path: Path, name: str) -> None:
    """Columns beyond the required ones, in any order, pass validation."""
    frame = _COVERAGE.assign(draw=0)[["coverage", "draw", "year_id", "location_id"]]
    path = _deliver(tmp_path, "ethiopia", name, frame)

    assert handoff_inputs(tmp_path, ["ethiopia"], {name: _COLUMNS}) == {
        handoff_key("ethiopia", name): path
    }


def test_only_the_header_is_read(tmp_path: Path) -> None:
    """A CSV with the right header but ragged or non-numeric data rows passes validation."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    path.write_text(
        "location_id,year_id,coverage\n"
        "1,2020,0.4\n"
        "1,2021\n"
        "1,2022,0.6,9,9\n"
        "x,not a number,high\n"
    )

    assert handoff_inputs(tmp_path, ["ethiopia"], _FILES) == {
        handoff_key("ethiopia", "coverage.csv"): path
    }


def test_csv_with_a_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    """A UTF-8 CSV with a byte-order mark passes validation."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    path.write_bytes(_COVERAGE.to_csv(index=False).encode("utf-8-sig"))

    assert handoff_inputs(tmp_path, ["ethiopia"], _FILES) == {
        handoff_key("ethiopia", "coverage.csv"): path
    }


def test_header_spaces_report_the_column_missing(tmp_path: Path) -> None:
    """A header name with surrounding spaces is reported missing, and the message shows it with its spaces."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    path.write_text("location_id, year_id ,coverage\n1,2020,0.4\n")

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    line = _problem_line(message, "<root>/ethiopia/primary/coverage.csv")
    assert "'year_id'" in line and "' year_id '" in line
    assert line.index("'year_id'") < line.index("' year_id '")
    assert line.count("'location_id'") == line.count("'coverage'") == 1


def test_csv_without_a_header_shows_its_first_row_as_columns(tmp_path: Path) -> None:
    """A CSV with no header row is reported as missing columns, listing its first data row as the columns present."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    _COVERAGE.to_csv(path, header=False, index=False)

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    line = _problem_line(message, "<root>/ethiopia/primary/coverage.csv")
    assert all(repr(column) in line for column in _COLUMNS)
    assert "'2020'" in line and "'0.4'" in line
    assert max(line.index(repr(column)) for column in _COLUMNS) < line.index("'2020'")


def test_csv_written_with_its_index_is_accepted(tmp_path: Path) -> None:
    """A CSV whose extra first column is an unnamed pandas index passes validation."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    _COVERAGE.to_csv(path)

    assert handoff_inputs(tmp_path, ["ethiopia"], _FILES) == {
        handoff_key("ethiopia", "coverage.csv"): path
    }


@pytest.mark.parametrize("separator", [";", "\t"])
def test_non_comma_separator_is_reported_as_missing_columns(
    tmp_path: Path, separator: str
) -> None:
    """A semicolon- or tab-separated file is read as one column and reported as missing the required ones."""
    path = _primary(tmp_path, "ethiopia") / "coverage.csv"
    _COVERAGE.to_csv(path, sep=separator, index=False)

    message = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    assert "could not be read" not in message
    assert separator.join(_COLUMNS) in message.replace("\\t", "\t")


def test_parquet_index_column_counts_as_present(tmp_path: Path) -> None:
    """A required column stored as a Parquet file's named index counts as present."""
    frame = _COVERAGE.set_index("location_id")
    path = _deliver(tmp_path, "ethiopia", "coverage.parquet", frame)

    assert handoff_inputs(tmp_path, ["ethiopia"], {"coverage.parquet": _COLUMNS}) == {
        handoff_key("ethiopia", "coverage.parquet"): path
    }


@pytest.mark.parametrize(
    "location, name",
    [
        ("", "coverage.csv"),
        (".", "coverage.csv"),
        ("..", "coverage.csv"),
        ("east/africa", "coverage.csv"),
        ("ethiopia", "sub/coverage.csv"),
        ("ethiopia", "../coverage.csv"),
    ],
)
def test_bad_location_or_file_name_raises_value_error_before_reading(
    tmp_path: Path, location: str, name: str
) -> None:
    """A location of '', '.', '..', or containing '/', or a file name with a directory, raises ValueError before any file is read."""
    files = {"draws.parquet": _COLUMNS, name: _COLUMNS}

    _assert_rejected_before_reading(tmp_path, ["nigeria", location], files)


@pytest.mark.parametrize("name", ["coverage.xlsx", "coverage.csv.gz", "coverage.hdf"])
def test_unsupported_suffix_raises_value_error(tmp_path: Path, name: str) -> None:
    """A declared file ending in .xlsx, .csv.gz, or .hdf raises ValueError."""
    files = {"draws.parquet": _COLUMNS, name: _COLUMNS}

    _assert_rejected_before_reading(tmp_path, ["nigeria", "ethiopia"], files)


def test_bare_string_column_list_raises_value_error(tmp_path: Path) -> None:
    """A required-column list given as a bare string raises ValueError."""
    _assert_rejected_before_reading(
        tmp_path,
        ["nigeria", "ethiopia"],
        {"draws.parquet": _COLUMNS, "coverage.csv": "coverage"},
    )


def test_duplicate_locations_are_checked_once(tmp_path: Path) -> None:
    """A location listed twice is checked once, so each of its problems is reported once."""
    once = _handoff_error(tmp_path, ["ethiopia"], _FILES)

    assert _handoff_error(tmp_path, ["ethiopia", "ethiopia"], _FILES) == once


def test_empty_locations_or_files_return_nothing(tmp_path: Path) -> None:
    """An empty location list or an empty file mapping returns an empty dict."""
    assert handoff_inputs(tmp_path, [], _FILES) == {}
    assert handoff_inputs(tmp_path, ["ethiopia"], {}) == {}


def test_symlinked_root_resolves_to_the_real_path(tmp_path: Path) -> None:
    """Returned paths under a symlinked handoff root are resolved."""
    path = _deliver(tmp_path / "real", "ethiopia", "coverage.csv")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    assert handoff_inputs(tmp_path / "link", ["ethiopia"], _FILES) == {
        handoff_key("ethiopia", "coverage.csv"): path
    }


def test_mistyped_handoff_key_suggests_the_declared_one(tmp_path: Path) -> None:
    """A step naming a mistyped handoff key gets the registry's did-you-mean suggestion of the declared key."""
    _deliver(tmp_path, "ethiopia", "coverage.csv")

    with pytest.raises(WiringError, match="Did you mean 'ethiopia/primary/coverage.csv'"):
        _mean_step(tmp_path, "ethiopia/primary/coverge.csv")


def test_handoff_file_feeds_a_python_step_end_to_end(tmp_path: Path) -> None:
    """A python_step reading a handoff key gets the handoff path as its input, and build() writes its product."""
    path = _deliver(tmp_path, "ethiopia", "coverage.csv")
    task = _mean_step(tmp_path, handoff_key("ethiopia", "coverage.csv"))

    [node] = list(task.inputs.values())
    assert isinstance(node, PathNode)
    assert node.path == path
    assert outcomes(build([task], tmp_path / "state")) == {task.name: TaskOutcome.SUCCESS}
    product = pd.read_parquet(tmp_path / "out" / "run1" / "mean.parquet")
    assert product["mean_coverage"].tolist() == pytest.approx([0.45])


def test_redelivery_staleness_follows_the_file_contents(tmp_path: Path) -> None:
    """After a build, rewriting identical bytes leaves the step current; changing a value makes it would-run, naming the file."""
    path = _deliver(tmp_path, "ethiopia", "coverage.csv")
    task = _mean_step(tmp_path, handoff_key("ethiopia", "coverage.csv"))
    task.code_id = _Code("mean")
    state = tmp_path / "state"
    assert outcomes(build([task], state)) == {task.name: TaskOutcome.SUCCESS}

    path.write_bytes(path.read_bytes())
    [resaved] = preview([task], state)
    path.write_text(path.read_text().replace("0.5", "0.6"))
    [changed] = preview([task], state)

    assert not resaved.would_run
    assert changed.would_run
    assert "coverage.csv" in " ".join(changed.reasons)


def test_worked_example_validates_and_builds(tmp_path: Path) -> None:
    """The documented example handoff validates and its pipeline builds the expected mean-coverage product."""
    inputs = handoff_inputs(pipeline.HANDOFF_ROOT, pipeline.LOCATIONS, pipeline.PRIMARY_DATA)
    assert list(inputs) == [handoff_key("example_location", "coverage.csv")]

    tasks = pipeline.build_tasks(tmp_path / "out")
    assert outcomes(build(tasks, tmp_path / "state")) == {
        task.name: TaskOutcome.SUCCESS for task in tasks
    }
    product = tmp_path / "out" / "example" / "example_location" / "mean_coverage.parquet"
    assert pd.read_parquet(product)["mean_coverage"].tolist() == pytest.approx([0.5])


@pytest.mark.skipif(os.geteuid() == 0, reason="root can search any directory")
def test_unsearchable_directory_is_reported_unreadable(tmp_path: Path) -> None:
    """A primary directory that cannot be searched is reported as could not be read, alongside other locations' problems (skipped as root)."""
    primary = _deliver(tmp_path, "ethiopia", "coverage.csv").parent
    primary.chmod(0o644)
    try:
        message = _handoff_error(tmp_path, ["ethiopia", "nigeria"], _FILES)
    finally:
        primary.chmod(0o755)

    ethiopia = _problem_line(message, "<root>/ethiopia/primary/coverage.csv")
    assert "could not be read" in ethiopia
    assert "<root>/nigeria/primary/coverage.csv" in message


def test_any_parquet_reader_error_is_reported_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Parquet reader error that is neither ValueError nor OSError, such as pyarrow's ArrowNotImplementedError, is reported as could not be read."""
    _deliver(tmp_path, "ethiopia", "draws.parquet")
    monkeypatch.setattr(pq, "read_schema", _unsupported_parquet)

    message = _handoff_error(tmp_path, ["ethiopia"], {"draws.parquet": _COLUMNS})

    assert "could not be read" in message
    assert "<root>/ethiopia/primary/draws.parquet" in message
    assert _UNSUPPORTED in message


def test_bare_string_locations_raise_value_error(tmp_path: Path) -> None:
    """A bare string passed as the location list raises ValueError instead of being split into letters."""
    with pytest.raises(ValueError):
        handoff_inputs(tmp_path, "ethiopia", _FILES)


def test_found_hint_ignores_case_and_quotes_names(tmp_path: Path) -> None:
    """When coverage.csv is missing, a present Coverage.CSV is named in the hint, quoted."""
    probe = tmp_path / "probe"
    probe.touch()
    if (tmp_path / probe.name.upper()).exists():
        pytest.skip("tmp_path is on a case-insensitive file system")
    _deliver(tmp_path, "ethiopia", "Coverage.CSV")

    assert "'Coverage.CSV'" in _handoff_error(tmp_path, ["ethiopia"], _FILES)
