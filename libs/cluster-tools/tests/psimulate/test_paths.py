import pytest

from vivarium.cluster_tools.psimulate.paths import build_perf_log_filename

# A worker task id is a 16-hex-char hash.
TASK_ID = "0123456789abcdef"


def test_build_perf_log_filename_prefixes_slurm_array_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLURM_ARRAY_JOB_ID", "525")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "3")
    assert build_perf_log_filename(TASK_ID) == f"525_3.perf.{TASK_ID}.log"


def test_build_perf_log_filename_falls_back_off_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLURM_ARRAY_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_ARRAY_TASK_ID", raising=False)
    assert build_perf_log_filename(TASK_ID) == f"perf.{TASK_ID}.log"
