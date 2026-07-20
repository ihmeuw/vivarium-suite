import pytest

from vivarium.cluster_tools.psimulate.performance_logger import (
    PERF_LOG_PATTERN,
    build_perf_log_filename,
)

# A worker task id is a 16-hex-char hash.
TASK_ID = "0123456789abcdef"


def test_build_perf_log_filename_prefixes_slurm_array_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLURM_ARRAY_JOB_ID", "525")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "3")
    name = build_perf_log_filename(TASK_ID)
    assert name == f"525_3.perf.{TASK_ID}.log"
    assert PERF_LOG_PATTERN.fullmatch(name)


def test_build_perf_log_filename_falls_back_off_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLURM_ARRAY_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_ARRAY_TASK_ID", raising=False)
    name = build_perf_log_filename(TASK_ID)
    assert name == f"perf.{TASK_ID}.log"
    assert PERF_LOG_PATTERN.fullmatch(name)


@pytest.mark.parametrize(
    "name",
    [
        f"525_3.perf.{TASK_ID}.log",  # prefixed
        f"525_31.perf.{TASK_ID}.log",  # multi-digit array task id
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
        f"525_3perf.{TASK_ID}.log",  # missing the '.' delimiter after the id
        "perf.0123456789abcde.log",  # 15-char hash, too short
    ],
)
def test_perf_log_pattern_rejects_non_perf_names(name: str) -> None:
    assert PERF_LOG_PATTERN.fullmatch(name) is None
