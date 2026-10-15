"""Tests for ``vivarium.build_utils.run_steps``, which backs ``make check`` and ``make lint``."""

from __future__ import annotations

from pathlib import Path

import pytest

from vivarium.build_utils import run_steps
from vivarium.build_utils.run_steps import main


class FakeRunner:
    """Record the targets run and return a preset exit code for each."""

    def __init__(self, exit_codes: dict[str, int]) -> None:
        self.exit_codes = exit_codes
        self.calls: list[str] = []

    def __call__(self, target: str) -> int:
        self.calls.append(target)
        return self.exit_codes[target]


def test_all_targets_pass(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner({"lint": 0, "test-all": 0})
    monkeypatch.setattr(run_steps, "run_make_target", runner)

    assert main(["lint", "test-all"]) == 0
    assert runner.calls == ["lint", "test-all"]
    out = capsys.readouterr().out
    assert "PASSED  lint\nPASSED  test-all\nAll 2 targets passed" in out


def test_failure_does_not_stop_later_targets(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner({"lint": 1, "mypy-if-typed": 0, "test-all": 2})
    monkeypatch.setattr(run_steps, "run_make_target", runner)

    assert main(["lint", "mypy-if-typed", "test-all"]) == 1
    assert runner.calls == ["lint", "mypy-if-typed", "test-all"]
    out = capsys.readouterr().out
    assert (
        "FAILED  lint\nPASSED  mypy-if-typed\nFAILED  test-all\n2 of 3 targets failed" in out
    )


def test_requires_a_target() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_runs_real_make_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("bad:\n\texit 1\n\ngood:\n\ttouch good-ran\n")
    monkeypatch.chdir(tmp_path)

    assert main(["bad", "good"]) == 1
    assert (tmp_path / "good-ran").exists()
