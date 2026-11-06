"""Tests for the targets in ``base.mk`` that change in a repo with ``uv.lock``.

In the ``install`` tests, a stub ``pip`` comes first on ``PATH``, so a run that gets
past the guard stops at its first ``pip`` call and installs nothing.
"""

import os
import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"
STUB_MARKER = "STUB PIP CALLED"
GUARD_MESSAGE = "builds its environment from uv.lock"


@pytest.fixture
def stub_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment with a failing stub ``pip`` and no ``JENKINS_URL``."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    pip = bin_dir / "pip"
    pip.write_text(f'#!/bin/sh\necho "{STUB_MARKER}"\nexit 3\n')
    pip.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "JENKINS_URL"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def _make_install(
    directory: Path, env: dict[str, str], with_lock: bool
) -> subprocess.CompletedProcess[str]:
    directory.mkdir(parents=True)
    (directory / "pyproject.toml").write_text('[project]\nname = "probe"\n')
    if with_lock:
        (directory / "uv.lock").write_text("version = 1\n")
    return subprocess.run(
        ["make", "-f", str(BASE_MK), "install"],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
    )


def test_refuses_in_standalone_repo_with_lock(
    tmp_path: Path, stub_env: dict[str, str]
) -> None:
    result = _make_install(tmp_path / "repo", stub_env, with_lock=True)
    assert result.returncode != 0
    assert GUARD_MESSAGE in result.stdout
    assert "source environment.sh" in result.stdout
    assert STUB_MARKER not in result.stdout


@pytest.mark.parametrize(
    "subdir, with_lock, jenkins_url",
    [
        ("repo", False, None),
        ("repo", True, "https://jenkins.example/"),
        ("libs/engine", True, None),
    ],
    ids=["no-lock", "jenkins", "monorepo-lib"],
)
def test_installs_otherwise(
    tmp_path: Path,
    stub_env: dict[str, str],
    subdir: str,
    with_lock: bool,
    jenkins_url: str | None,
) -> None:
    if jenkins_url is not None:
        stub_env["JENKINS_URL"] = jenkins_url
    result = _make_install(tmp_path / subdir, stub_env, with_lock)
    assert GUARD_MESSAGE not in result.stdout
    # Reaching the stub pip shows the guard let the install run.
    assert STUB_MARKER in result.stdout


@pytest.mark.parametrize(
    "subdir, with_lock, uv_steps",
    [
        ("repo", True, True),
        ("repo", False, False),
        ("libs/engine", True, False),
    ],
    ids=["uv-repo", "conda-repo", "monorepo-lib"],
)
def test_help_lists_the_build_steps_for_the_repo(
    tmp_path: Path, subdir: str, with_lock: bool, uv_steps: bool
) -> None:
    """``make help`` gives the uv steps only in a standalone repo with ``uv.lock``."""
    directory = tmp_path / subdir
    directory.mkdir(parents=True)
    if with_lock:
        (directory / "uv.lock").write_text("version = 1\n")
    result = subprocess.run(
        ["make", "-s", "-f", str(BASE_MK), "help"],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert ("uv sync --locked" in result.stdout) is uv_steps
    assert ("create-env" in result.stdout) is not uv_steps
    assert "including this repo's own" in result.stdout
