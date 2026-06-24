import glob
from pathlib import Path
from typing import Generator

import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger
from pandas.testing import assert_frame_equal

from vivarium.cluster_tools.psimulate.paths import (
    CENTRAL_PERFORMANCE_LOGS_DIRECTORY,
    OutputPaths,
)
from vivarium.cluster_tools.psimulate.performance_logger import (
    CENTRAL_LOG_SCHEMA,
    append_child_job_data,
    append_perf_data_to_central_logs,
    generate_runner_job_data,
    transform_perf_df_for_appending,
)
from vivarium.cluster_tools.vipin.perf_counters import CounterSnapshot

# The columns that uniquely identify a child job; they form the perf-report index.
INDEX_COLS = ["host", "job_number", "task_number", "draw", "seed"]


def _patch_central_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, num_rows: int
) -> None:
    """Point the central log directory at tmp_path and cap each file at num_rows rows."""
    monkeypatch.setattr(
        "vivarium.cluster_tools.psimulate.performance_logger.NUM_ROWS_PER_CENTRAL_LOG_FILE",
        num_rows,
    )
    monkeypatch.setattr(
        "vivarium.cluster_tools.psimulate.performance_logger.CENTRAL_PERFORMANCE_LOGS_DIRECTORY",
        tmp_path,
    )


def _load_perf_df(filename: str) -> pd.DataFrame:
    """Load a perf-report fixture indexed like a real run's performance report."""
    filepath = Path(__file__).parent / "data" / filename
    # index_col=0 drops the unnamed row index saved into the fixture csv.
    df = pd.read_csv(filepath, index_col=0)
    # job_hash is part of the central log schema; the worker emits it per task.
    df.insert(
        list(df.columns).index("seed") + 1,
        "job_hash",
        [f"task_id_{i}" for i in range(len(df))],
    )
    scenario_cols = [col for col in df.columns if col.startswith("scenario")]
    return df.set_index(INDEX_COLS + scenario_cols)


@pytest.fixture
def artifact_perf_df() -> pd.DataFrame:
    return _load_perf_df("artifact_perf_df.csv")


@pytest.fixture
def artifactless_perf_df() -> pd.DataFrame:
    return _load_perf_df("artifactless_perf_df.csv")


@pytest.fixture
def result_directory() -> Path:
    return Path("/mnt/team/simulation_science/pub/models/project_name/results/model_version/")


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


def get_output_paths_from_output_directory(output_directory: Path) -> OutputPaths:
    model_name = "artifact"
    original_launch_time = "YYYY_MM_DD_HH_MM_SS"
    launch_time = "yyyy_mm_dd_hh_mm_ss"
    output_directory = output_directory / model_name / original_launch_time

    logging_directory = output_directory / "logs" / f"{launch_time}_runtype"
    logging_dirs = {
        "logging_root": logging_directory,
        "worker_logging_root": logging_directory / "worker_logs",
    }

    output_paths = OutputPaths(
        root=output_directory,
        **logging_dirs,
        metadata_dir=output_directory / "metadata",
        environment_file=output_directory / "requirements.txt",
        model_specification=output_directory / "model_specification.yaml",
        keyspace=output_directory / "keyspace.yaml",
        branches=output_directory / "branches.yaml",
        results_dir=output_directory / "results",
        backup_dir=output_directory / "sim_backups",
        backup_metadata_path=output_directory / "sim_backups" / "backup_metadata.csv",
    )

    return output_paths


@pytest.mark.parametrize("df_name", ["artifact_perf_df", "artifactless_perf_df"])
def test_expected_columns(
    df_name: str, result_directory: Path, request: pytest.FixtureRequest
) -> None:
    perf_df = request.getfixturevalue(df_name)
    # transform df
    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(perf_df, output_paths)
    expected_columns = (
        INDEX_COLS + perf_df.columns.tolist() + ["artifact_name", "scenario_parameters"]
    )
    assert list(central_perf_df.columns) == expected_columns


@pytest.mark.parametrize("df_name", ["artifact_perf_df", "artifactless_perf_df"])
def test_data_parsing(
    df_name: str, result_directory: Path, request: pytest.FixtureRequest
) -> None:
    perf_df = request.getfixturevalue(df_name)
    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(perf_df, output_paths)
    assert (central_perf_df["artifact_name"] == "artifact").all()

    if df_name == "artifact_perf_df":
        expected_scenario_parameters = [
            '{"scenario_parameter_one": "value_one", "scenario_input_data_artifact_path": "/path/to/artifact.hdf"}',
            '{"scenario_parameter_one": "value_two", "scenario_input_data_artifact_path": "/path/to/artifact.hdf"}',
        ] * 6
    else:
        expected_scenario_parameters = [
            '{"scenario_parameter_one": "value_one"}',
            '{"scenario_parameter_one": "value_two"}',
        ] * 6
    assert (central_perf_df["scenario_parameters"] == expected_scenario_parameters).all()

    job_number = int(central_perf_df["job_number"].unique().squeeze())
    runner_data = generate_runner_job_data(job_number, output_paths, "first_file_with_data")

    assert runner_data["project_name"].squeeze() == "project_name"
    assert runner_data["root_path"].squeeze() == (
        "/mnt/team/simulation_science/pub/models/project_name/results/model_version/artifact"
    )
    assert runner_data["original_run_date"].squeeze() == "YYYY_MM_DD_HH_MM_SS"
    assert runner_data["run_date"].squeeze() == "yyyy_mm_dd_hh_mm_ss"
    assert runner_data["run_type"].squeeze() == "runtype"
    assert runner_data["log_summary_file_path"].squeeze() == "first_file_with_data"
    assert (
        runner_data["original_log_file_path"].squeeze()
        == "/mnt/team/simulation_science/pub/models/project_name/results/model_version/artifact/YYYY_MM_DD_HH_MM_SS/logs/yyyy_mm_dd_hh_mm_ss_runtype/worker_logs/log_summary.csv"
    )


def test_valid_log_path(
    result_directory: Path,
    artifact_perf_df: pd.DataFrame,
    caplog: LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_central_logs(monkeypatch, tmp_path, num_rows=4)
    # add some data to central logs directory to allow appending
    output_paths = get_output_paths_from_output_directory(result_directory)
    pd.DataFrame(columns=CENTRAL_LOG_SCHEMA).to_csv(
        tmp_path / "log_summary_0000.csv", index=False
    )
    # test no warnings were raised
    append_perf_data_to_central_logs(artifact_perf_df, output_paths)
    assert not caplog.records


@pytest.mark.parametrize(
    "invalid_log_path",
    [
        Path("/mnt/team/simulation_science/pub/models/project_name/model_version/"),
        Path("/mnt/team/simulation_science/pub/project_and_model_version/"),
        Path("/ihme/homes/user/model_version/"),
    ],
)
def test_invalid_log_path(
    invalid_log_path: Path, artifact_perf_df: pd.DataFrame, caplog: LogCaptureFixture
) -> None:
    # test we raise specific warning
    output_paths = get_output_paths_from_output_directory(invalid_log_path)
    append_perf_data_to_central_logs(artifact_perf_df, output_paths)
    assert "Skipping appending central performance logs." in caplog.text


@pytest.mark.parametrize(
    "available_rows, rows_to_append, expected_output_files, multiple_log_files_exist",
    [
        (2, 2, 2, False),
        (2, 2, 2, True),
        (2, 6, 3, False),
        (2, 6, 3, True),
        (2, 9, 3, False),
        (4, 4, 2, False),
        (3, 2, 1, False),
        (1, 3, 2, False),
    ],
)
def test_appending(
    available_rows: int,
    rows_to_append: int,
    expected_output_files: int,
    multiple_log_files_exist: bool,
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    max_num_rows = 4
    _patch_central_logs(monkeypatch, tmp_path, num_rows=max_num_rows)

    # set up tests
    # create data we want to append
    output_paths = get_output_paths_from_output_directory(result_directory)
    # The logs are pinned to CENTRAL_LOG_SCHEMA, so compare against schema-ordered data.
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths).reindex(
        columns=CENTRAL_LOG_SCHEMA
    )
    data_to_append = central_perf_df[:rows_to_append]
    # create most recent files
    if multiple_log_files_exist:
        central_perf_df.to_csv(tmp_path / "log_summary_0000.csv", index=False)
        most_recent_file = tmp_path / "log_summary_0001.csv"
        expected_output_files += 1
    else:
        most_recent_file = tmp_path / "log_summary_0000.csv"
    initial_data = pd.DataFrame(
        index=range(max_num_rows - available_rows), columns=central_perf_df.columns
    )
    initial_data.to_csv(most_recent_file, index=False)

    # append data and test
    first_file_with_data = append_child_job_data(data_to_append)

    assert first_file_with_data == str(most_recent_file)

    # test that all files we expect to exist are there
    absolute_output_filepaths = sorted(tmp_path.glob("*"))
    output_filenames = [filepath.stem for filepath in absolute_output_filepaths]
    expected_filenames = [
        f"log_summary_{str(i).zfill(4)}" for i in range(expected_output_files)
    ]
    assert expected_filenames == output_filenames

    # test that each of those files has the right number of rows and the expected data
    # inspect first file
    first_file = pd.read_csv(most_recent_file)
    assert_frame_equal(first_file[: len(initial_data)], initial_data, check_dtype=False)
    assert_frame_equal(
        first_file[len(initial_data) :].reset_index(drop=True),
        data_to_append[: (max_num_rows - len(initial_data))],
        check_dtype=False,
    )

    data_to_append = data_to_append[(max_num_rows - len(initial_data)) :].reset_index(
        drop=True
    )

    if multiple_log_files_exist:
        # check that existing file wasn't modified
        existing_file = pd.read_csv(tmp_path / "log_summary_0000.csv")
        assert_frame_equal(existing_file, central_perf_df, check_dtype=False)
        # remove empty file from list of files to check
        absolute_output_filepaths = absolute_output_filepaths[1:]
    # inspect remaining files
    for file in absolute_output_filepaths[1:]:
        file_data = pd.read_csv(file)
        assert_frame_equal(file_data, data_to_append[:max_num_rows], check_dtype=False)
        data_to_append = data_to_append[max_num_rows:].reset_index(drop=True)


def test_appending_aligns_to_schema(
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """Data whose columns drift from the schema is realigned to it, not appended as-is."""
    _patch_central_logs(monkeypatch, tmp_path, num_rows=100)  # large enough to never roll

    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths)
    # The incoming run drifts from the schema: it carries an unexpected column and is
    # missing a pinned one. Appending it as-is is what used to corrupt the csv.
    incoming = central_perf_df.drop(columns=["job_hash"])
    incoming["unexpected_counter"] = 1.0

    log_file = tmp_path / "log_summary_0000.csv"
    pd.DataFrame(columns=CENTRAL_LOG_SCHEMA).to_csv(log_file, index=False)

    append_child_job_data(incoming)

    result = pd.read_csv(log_file)
    # The file stays parseable and pinned to the schema; no drift lands on disk.
    assert list(result.columns) == list(CENTRAL_LOG_SCHEMA)
    assert len(result) == len(incoming)
    # The unexpected column is dropped; the missing pinned column is filled with NaN.
    assert "unexpected_counter" not in result.columns
    assert result["job_hash"].isna().all()
    # The drift is surfaced as a warning naming both offending sides.
    assert "do not match the central log schema" in caplog.text
    assert "unexpected_counter" in caplog.text
    assert "job_hash" in caplog.text
    # Every non-drifting column round-trips unchanged in schema order.
    expected = incoming.reindex(columns=CENTRAL_LOG_SCHEMA).reset_index(drop=True)
    assert_frame_equal(result, expected, check_dtype=False)


def test_appending_rolls_past_legacy_header(
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """A most-recent file whose header predates the schema is left untouched; data rolls onward."""
    _patch_central_logs(monkeypatch, tmp_path, num_rows=100)  # large enough to never roll

    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths).reindex(
        columns=CENTRAL_LOG_SCHEMA
    )

    # A legacy file with the same columns but the old `location` name for `artifact_name`.
    # Appending into it positionally would misalign every row, so it must be skipped.
    legacy_columns = [
        "location" if column == "artifact_name" else column for column in CENTRAL_LOG_SCHEMA
    ]
    legacy_frame = central_perf_df.copy()
    legacy_frame.columns = legacy_columns
    legacy_file = tmp_path / "log_summary_0000.csv"
    legacy_frame.to_csv(legacy_file, index=False)

    first_file_with_data = append_child_job_data(central_perf_df.copy())

    # The legacy file is left exactly as it was.
    legacy_result = pd.read_csv(legacy_file)
    assert legacy_result.columns.tolist() == legacy_columns
    assert len(legacy_result) == len(central_perf_df)
    # The data went to a new file with the canonical schema header.
    new_file = tmp_path / "log_summary_0001.csv"
    assert first_file_with_data == str(new_file)
    new_result = pd.read_csv(new_file)
    assert new_result.columns.tolist() == list(CENTRAL_LOG_SCHEMA)
    assert_frame_equal(new_result, central_perf_df.reset_index(drop=True), check_dtype=False)
    assert "does not match the current schema" in caplog.text


def test_appending_rolls_past_header_missing_a_column(
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """A most-recent file missing a schema column (a set difference) also triggers a roll."""
    _patch_central_logs(monkeypatch, tmp_path, num_rows=100)  # large enough to never roll

    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths).reindex(
        columns=CENTRAL_LOG_SCHEMA
    )

    # A legacy file that predates the job_hash column entirely.
    legacy_columns = [column for column in CENTRAL_LOG_SCHEMA if column != "job_hash"]
    legacy_file = tmp_path / "log_summary_0000.csv"
    central_perf_df.drop(columns=["job_hash"]).to_csv(legacy_file, index=False)

    first_file_with_data = append_child_job_data(central_perf_df.copy())

    assert pd.read_csv(legacy_file).columns.tolist() == legacy_columns
    new_file = tmp_path / "log_summary_0001.csv"
    assert first_file_with_data == str(new_file)
    assert pd.read_csv(new_file).columns.tolist() == list(CENTRAL_LOG_SCHEMA)
    assert "does not match the current schema" in caplog.text


def test_appending_does_not_roll_when_header_matches(
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """A schema-matching most-recent file is appended to in place, never rolled past."""
    _patch_central_logs(monkeypatch, tmp_path, num_rows=100)  # large enough to never roll

    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths).reindex(
        columns=CENTRAL_LOG_SCHEMA
    )
    log_file = tmp_path / "log_summary_0000.csv"
    pd.DataFrame(columns=CENTRAL_LOG_SCHEMA).to_csv(log_file, index=False)

    first_file_with_data = append_child_job_data(central_perf_df.copy())

    assert first_file_with_data == str(log_file)
    assert not (tmp_path / "log_summary_0001.csv").exists()
    assert "does not match" not in caplog.text
    assert len(pd.read_csv(log_file)) == len(central_perf_df)


def test_appending_drifted_data_rolls_across_files(
    artifact_perf_df: pd.DataFrame,
    result_directory: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drifted data spanning multiple files: every rolled file holds the schema in order."""
    _patch_central_logs(monkeypatch, tmp_path, num_rows=5)  # forces rolls across 3 files

    output_paths = get_output_paths_from_output_directory(result_directory)
    central_perf_df = transform_perf_df_for_appending(artifact_perf_df, output_paths)
    # Drift (missing a pinned column, plus an unexpected one) must be realigned in every
    # rolled file, not just the first.
    incoming = central_perf_df.drop(columns=["job_hash"])
    incoming["unexpected_counter"] = 1.0
    pd.DataFrame(columns=CENTRAL_LOG_SCHEMA).to_csv(
        tmp_path / "log_summary_0000.csv", index=False
    )

    append_child_job_data(incoming)

    log_files = sorted(tmp_path.glob("log_summary_*.csv"))
    assert len(log_files) == 3  # 12 rows capped at 5 per file
    appended = 0
    for log_file in log_files:
        file_data = pd.read_csv(log_file)
        assert file_data.columns.tolist() == list(CENTRAL_LOG_SCHEMA)
        assert "unexpected_counter" not in file_data.columns
        appended += len(file_data)
    assert appended == len(incoming)


def test_central_log_schema_matches_produced_columns(result_directory: Path) -> None:
    """Canary: the worker -> report -> transform pipeline must produce exactly CENTRAL_LOG_SCHEMA.

    The central logs are appended to positionally, so the producing code and the pinned
    schema must never silently diverge. This reconstructs the produced columns from the
    real code path with no simulation or scheduler required: the message
    vivarium_work_horse.do_sim_epilogue emits (with live psutil counters), the json_normalize
    flattening done in vipin's perf_report, and transform_perf_df_for_appending. If it
    fails, the producing code drifted from the schema.
    """
    # Counters come from a live snapshot so a psutil change to the counter fields is
    # caught too. Some platforms (containers, VMs) omit a family such as cpu_freq.
    snapshot = CounterSnapshot()

    # Mirror the message emitted by vivarium_work_horse.do_sim_epilogue.
    worker_message = {
        "host": "host",
        "job_number": 1,
        "task_number": 1,
        "job_hash": "task_id_0",
        "draw": 0,
        "seed": 0,
        "scenario": {
            "scenario_input_data_artifact_path": "/path/to/artifact.hdf",
            "scenario_parameter_one": "value_one",
        },
        "event": {
            "start": 0.0,
            "simulant_initialization_start": 0.0,
            "simulation_start": 0.0,
            "results_start": 0.0,
            "end": 0.0,
        },
        "exec_time": {
            "setup_minutes": 0.0,
            "simulant_initialization_minutes": 0.0,
            "main_loop_minutes": 0.0,
            "step_mean_seconds": 0.0,
            "results_minutes": 0.0,
            "total_minutes": 0.0,
        },
        "counters": snapshot.to_dict(),
    }

    perf_df = pd.json_normalize(worker_message, sep="_")
    scenario_cols = [col for col in perf_df.columns if col.startswith("scenario")]
    perf_df = perf_df.set_index(INDEX_COLS + scenario_cols)

    output_paths = get_output_paths_from_output_directory(result_directory)
    produced = set(transform_perf_df_for_appending(perf_df, output_paths).columns)

    # The code-defined columns (identity, event, exec_time, transform) are deterministic,
    # so they must match the schema exactly in both directions.
    schema_non_counters = {c for c in CENTRAL_LOG_SCHEMA if not c.startswith("counters_")}
    produced_non_counters = {c for c in produced if not c.startswith("counters_")}
    assert produced_non_counters == schema_non_counters, (
        "Central performance log columns drifted from CENTRAL_LOG_SCHEMA. "
        f"Produced but not in schema: {sorted(produced_non_counters - schema_non_counters)}. "
        f"In schema but not produced: {sorted(schema_non_counters - produced_non_counters)}. "
        "Update CENTRAL_LOG_SCHEMA in performance_logger.py (and start a new log file) "
        "if this change is intended."
    )

    # Counters can only be validated for additions: any counter the code produces must be
    # in the schema. A missing counter is tolerated (a platform may omit a family such as
    # cpu_freq), so a rename or removal that orphans a schema counter is not caught here.
    schema_counters = {c for c in CENTRAL_LOG_SCHEMA if c.startswith("counters_")}
    produced_counters = {c for c in produced if c.startswith("counters_")}
    unexpected_counters = produced_counters - schema_counters
    assert not unexpected_counters, (
        f"Performance counters not in CENTRAL_LOG_SCHEMA: {sorted(unexpected_counters)}. "
        "Update CENTRAL_LOG_SCHEMA in performance_logger.py if this change is intended."
    )


@pytest.mark.cluster
def test_latest_central_log_file_matches_schema() -> None:
    """The latest central log file on the shared mount must match CENTRAL_LOG_SCHEMA.

    Runs only on the cluster, where the performance log mount exists. Unlike the canary
    (which checks the producing code) this guards the on-disk state: an out-of-band
    write, stale deployed code elsewhere, or a partial roll could leave the most recent
    file with a header the positional append can no longer safely match.
    """
    log_files = sorted(
        glob.glob(CENTRAL_PERFORMANCE_LOGS_DIRECTORY.as_posix() + "/log_summary_*.csv")
    )
    assert log_files, "No central performance log files found on the shared mount."

    latest_file = log_files[-1]
    header = pd.read_csv(latest_file, nrows=0).columns.tolist()
    assert header == list(CENTRAL_LOG_SCHEMA), (
        f"Latest central log file {latest_file} does not match CENTRAL_LOG_SCHEMA. "
        f"Its header is {header}. A non-canonical file was written out of band; "
        "investigate the producer before further appends drift it."
    )
