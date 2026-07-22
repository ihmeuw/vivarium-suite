from pathlib import Path

import pandas as pd
import pytest
from loguru import logger

from vivarium.cluster_tools.psimulate.paths import build_perf_log_filename
from vivarium.cluster_tools.vipin.perf_report import (
    PERF_LOG_PATTERN,
    PerformanceSummary,
    print_stat_report,
)

TASK_ID = "0123456789abcdef"


def test_print_stat_report_emits_at_warning() -> None:
    """The end-of-run performance report is emitted at WARNING so it stays
    visible under psimulate's default (WARNING) terminal verbosity."""
    levels: list[str] = []
    sink_id = logger.add(
        lambda message: levels.append(message.record["level"].name), level="DEBUG"
    )
    try:
        # Minimal performance frame: one exec_time_ column, no scenario columns.
        perf_df = pd.DataFrame({"exec_time_total_minutes": [1.0, 2.0, 3.0]})
        print_stat_report(perf_df, scenario_cols=[])
    finally:
        logger.remove(sink_id)

    # The report must be logged, and at WARNING (not INFO, which would be hidden
    # under the default verbosity).
    assert levels == ["WARNING"]


def test_clean_perf_logs_discovers_prefixed_and_legacy(tmp_path: Path) -> None:
    """clean_perf_logs finds both the SLURM-prefixed and legacy perf-log names,
    and leaves everything else (e.g. the summary) alone."""
    (tmp_path / f"perf.525_3.{TASK_ID}.log").write_text("{}")
    (tmp_path / f"perf.{TASK_ID}.log").write_text("{}")
    (tmp_path / "log_summary.csv").write_text("keep me")

    PerformanceSummary(tmp_path).clean_perf_logs()

    assert {p.name for p in tmp_path.iterdir()} == {"log_summary.csv"}


def test_reader_discovers_name_the_worker_writes(tmp_path: Path) -> None:
    """The name the worker builds on the cluster is discovered by the reader -
    guards the writer<->reader contract end to end."""
    (tmp_path / build_perf_log_filename(TASK_ID, "525", "3")).write_text("{}")

    PerformanceSummary(tmp_path).clean_perf_logs()

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name",
    [
        f"perf.525_3.{TASK_ID}.log",  # prefixed
        f"perf.525_31.{TASK_ID}.log",  # multi-digit array task id
        f"perf.{TASK_ID}.log",  # legacy, unprefixed
    ],
)
def test_perf_log_pattern_accepts_valid_names(name: str) -> None:
    assert PERF_LOG_PATTERN.fullmatch(name)


@pytest.mark.parametrize(
    "name",
    [
        "log_summary.csv",
        "main.log",
        f"perf.525_3{TASK_ID}.log",  # missing the '.' delimiter after the array id
        "perf.0123456789abcde.log",  # 15-char hash, too short
    ],
)
def test_perf_log_pattern_rejects_non_perf_names(name: str) -> None:
    assert PERF_LOG_PATTERN.fullmatch(name) is None
