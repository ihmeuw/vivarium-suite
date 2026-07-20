from vivarium.cluster_tools.psimulate.paths import build_perf_log_filename

TASK_ID = "0123456789abcdef"


def test_build_perf_log_filename_prefixes_slurm_array_id() -> None:
    assert build_perf_log_filename(TASK_ID, "525", "3") == f"525_3.perf.{TASK_ID}.log"


def test_build_perf_log_filename_falls_back_without_array_ids() -> None:
    assert build_perf_log_filename(TASK_ID) == f"perf.{TASK_ID}.log"


def test_build_perf_log_filename_ignores_partial_array_ids() -> None:
    """One id without the other yields the legacy name, never a malformed ``525_.`` prefix."""
    assert build_perf_log_filename(TASK_ID, "525", "") == f"perf.{TASK_ID}.log"
    assert build_perf_log_filename(TASK_ID, "", "3") == f"perf.{TASK_ID}.log"
