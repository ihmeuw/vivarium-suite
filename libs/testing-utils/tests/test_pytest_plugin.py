"""Regression tests for the pytest plugin."""

import os
from pathlib import Path
from typing import cast

import pytest
from _pytest.config import Config

from vivarium.testing_utils import pytest_plugin
from vivarium.testing_utils.pytest_plugin import DEFAULT_MAX_WORKERS, _auto_num_workers

pytest_plugins = ["pytester"]


class _FakeConfig:
    """Minimal stand-in exposing only the ``getoption`` the hooks call."""

    def __init__(self, numprocesses: object) -> None:
        self._numprocesses = numprocesses

    def getoption(self, name: str, default: object = None) -> object:
        return self._numprocesses if name == "numprocesses" else default


@pytest.mark.parametrize(
    "cpus, memory_gb, expected",
    [
        (16, 64.0, DEFAULT_MAX_WORKERS),  # ample resources -> capped at the target
        (2, 64.0, 2),  # CPU-limited -> scales down to the cores available
        (16, 2.5, 2),  # memory-limited -> int(2.5 // _GB_PER_WORKER)
        (1, 64.0, 1),  # single core -> floor
        (16, 0.5, 1),  # too little memory for even one budget -> never below 1
        (16, None, DEFAULT_MAX_WORKERS),  # memory unreadable -> CPU/target only
    ],
)
def test_auto_num_workers_scales_and_floors(
    monkeypatch: pytest.MonkeyPatch, cpus: int, memory_gb: float | None, expected: int
) -> None:
    """The worker count targets DEFAULT_MAX_WORKERS and degrades to >=1."""
    monkeypatch.setattr(pytest_plugin, "_usable_cpu_count", lambda: cpus)
    monkeypatch.setattr(pytest_plugin, "_available_memory_gb", lambda: memory_gb)
    assert _auto_num_workers() == expected


def test_usable_cpu_count_uses_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert pytest_plugin._usable_cpu_count() == 3


def test_usable_cpu_count_falls_back_when_affinity_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 7)
    assert pytest_plugin._usable_cpu_count() == 7


def test_usable_cpu_count_falls_back_when_affinity_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_oserror(pid: int) -> set[int]:
        raise OSError

    monkeypatch.setattr(os, "sched_getaffinity", raise_oserror, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 7)
    assert pytest_plugin._usable_cpu_count() == 7


def test_usable_cpu_count_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert pytest_plugin._usable_cpu_count() == 1


def test_node_available_memory_gb_parses_memavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:    16000000 kB\nMemAvailable:  8388608 kB\n")
    monkeypatch.setattr(pytest_plugin, "Path", lambda _: meminfo)
    assert pytest_plugin._node_available_memory_gb() == pytest.approx(8.0)


def test_node_available_memory_gb_none_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pytest_plugin, "Path", lambda _: tmp_path / "absent")
    assert pytest_plugin._node_available_memory_gb() is None


def test_node_available_memory_gb_none_when_no_memavailable_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nMemFree: 1234 kB\n")
    monkeypatch.setattr(pytest_plugin, "Path", lambda _: meminfo)
    assert pytest_plugin._node_available_memory_gb() is None


def test_node_available_memory_gb_none_when_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: notanumber kB\n")
    monkeypatch.setattr(pytest_plugin, "Path", lambda _: meminfo)
    assert pytest_plugin._node_available_memory_gb() is None


@pytest.mark.parametrize(
    "content, expected",
    [
        ("2147483648\n", 2147483648),  # numeric byte count
        ("max\n", None),  # cgroup v2 unlimited
        (str(2**63 - 1), None),  # cgroup v1 unlimited sentinel
        ("garbage\n", None),  # unparseable
    ],
)
def test_read_cgroup_memory_limit(tmp_path: Path, content: str, expected: int | None) -> None:
    limit_file = tmp_path / "memory.max"
    limit_file.write_text(content)
    assert pytest_plugin._read_cgroup_memory_limit(limit_file) == expected


def test_read_cgroup_memory_limit_none_when_missing(tmp_path: Path) -> None:
    assert pytest_plugin._read_cgroup_memory_limit(tmp_path / "absent") is None


def test_cgroup_memory_limit_gb_reads_v2_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text("0::/slurm/job_1\n")
    root = tmp_path / "cgroup_fs"
    (root / "slurm" / "job_1").mkdir(parents=True)
    (root / "slurm" / "job_1" / "memory.max").write_text(str(2 * 1024**3))
    (root / "memory.max").write_text("max")
    monkeypatch.setattr(pytest_plugin, "_PROC_SELF_CGROUP", proc)
    monkeypatch.setattr(pytest_plugin, "_CGROUP_V2_ROOT", root)
    assert pytest_plugin._cgroup_memory_limit_gb() == pytest.approx(2.0)


def test_cgroup_memory_limit_gb_none_when_unlimited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text("0::/\n")
    root = tmp_path / "cgroup_fs"
    root.mkdir()
    (root / "memory.max").write_text("max")
    monkeypatch.setattr(pytest_plugin, "_PROC_SELF_CGROUP", proc)
    monkeypatch.setattr(pytest_plugin, "_CGROUP_V2_ROOT", root)
    assert pytest_plugin._cgroup_memory_limit_gb() is None


def test_cgroup_memory_limit_gb_none_when_proc_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pytest_plugin, "_PROC_SELF_CGROUP", tmp_path / "absent")
    assert pytest_plugin._cgroup_memory_limit_gb() is None


@pytest.mark.parametrize(
    "node_gb, cgroup_gb, expected",
    [
        (8.0, 2.0, 2.0),  # cgroup allocation is tighter
        (2.0, 8.0, 2.0),  # node free memory is tighter
        (8.0, None, 8.0),  # no cgroup limit -> node only
        (None, 2.0, 2.0),  # no /proc/meminfo -> cgroup only
        (None, None, None),  # neither readable
    ],
)
def test_available_memory_gb_takes_min(
    monkeypatch: pytest.MonkeyPatch,
    node_gb: float | None,
    cgroup_gb: float | None,
    expected: float | None,
) -> None:
    monkeypatch.setattr(pytest_plugin, "_node_available_memory_gb", lambda: node_gb)
    monkeypatch.setattr(pytest_plugin, "_cgroup_memory_limit_gb", lambda: cgroup_gb)
    assert pytest_plugin._available_memory_gb() == expected


def test_report_header_silent_when_not_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_plugin, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(pytest_plugin, "_available_memory_gb", lambda: 32.0)
    assert pytest_plugin.pytest_report_header(cast(Config, _FakeConfig(None))) == []


def test_report_header_reports_when_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_plugin, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(pytest_plugin, "_available_memory_gb", lambda: 32.0)
    [header] = pytest_plugin.pytest_report_header(cast(Config, _FakeConfig("auto")))
    assert f"auto-workers: {DEFAULT_MAX_WORKERS}" in header


def test_test_in_slow_directory_not_skipped(pytester: pytest.Pytester) -> None:
    """Regression test: a test located in a 'slow/' directory should NOT be
    skipped just because 'slow' appears in its keywords (path components).
    Only tests explicitly marked @pytest.mark.slow should be skipped."""
    pytester.mkdir("slow")
    pytester.makepyfile(
        **{
            "slow/test_example.py": """
def test_not_actually_slow(request):
    assert "slow" in request.keywords
"""
        }
    )
    result = pytester.runpytest("-v")
    result.stdout.fnmatch_lines(["*test_not_actually_slow PASSED*"])
    assert result.ret == 0
