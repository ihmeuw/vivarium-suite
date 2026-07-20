from pathlib import Path

import pandas as pd
import pytest
from loguru import logger

from vivarium.cluster_tools.psimulate.performance_logger import build_perf_log_filename
from vivarium.cluster_tools.vipin.perf_report import PerformanceSummary, print_stat_report

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
    (tmp_path / f"525_3.perf.{TASK_ID}.log").write_text("{}")
    (tmp_path / f"perf.{TASK_ID}.log").write_text("{}")
    (tmp_path / "log_summary.csv").write_text("keep me")

    PerformanceSummary(tmp_path).clean_perf_logs()

    assert {p.name for p in tmp_path.iterdir()} == {"log_summary.csv"}


def test_reader_discovers_name_the_worker_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name the worker builds on the cluster is discovered by the reader -
    guards the writer<->reader contract end to end."""
    monkeypatch.setenv("SLURM_ARRAY_JOB_ID", "525")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "3")
    (tmp_path / build_perf_log_filename(TASK_ID)).write_text("{}")

    PerformanceSummary(tmp_path).clean_perf_logs()

    assert not list(tmp_path.iterdir())
