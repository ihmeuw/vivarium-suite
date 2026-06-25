"""Regression tests for the pytest plugin."""

import pytest

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
        (16, 2.5, 2),  # memory-limited -> 2.5 GB / 1 GB per worker
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


def test_report_header_silent_when_not_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_plugin, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(pytest_plugin, "_available_memory_gb", lambda: 32.0)
    assert pytest_plugin.pytest_report_header(_FakeConfig(None)) == []


def test_report_header_reports_when_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_plugin, "_usable_cpu_count", lambda: 8)
    monkeypatch.setattr(pytest_plugin, "_available_memory_gb", lambda: 32.0)
    [header] = pytest_plugin.pytest_report_header(_FakeConfig("auto"))
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
