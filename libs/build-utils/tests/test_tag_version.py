"""Tests for the ``tag-version`` target in ``resources/makefiles/base.mk``.

``tag-version`` is release-critical: it creates and pushes the git tag that
triggers a release. These tests exercise its idempotency and single-tag push
behavior by running the target against a throwaway repo wired to a local bare
remote, so nothing touches the network.
"""
import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _run_tag_version(
    repo: Path, version: str = "1.2.3", prefix: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "-f",
            str(BASE_MK),
            "tag-version",
            f"PACKAGE_VERSION={version}",
            f"TAG_PREFIX={prefix}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo_with_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "commit", "--allow-empty", "-m", "init")
    return repo


def test_tag_version_creates_and_pushes_single_tag(repo_with_remote: Path) -> None:
    result = _run_tag_version(repo_with_remote, prefix="vivarium-build-utils-")
    assert result.returncode == 0, result.stderr
    assert _git(repo_with_remote, "tag", "--list").split() == ["vivarium-build-utils-v1.2.3"]
    remote_tags = _git(repo_with_remote, "ls-remote", "--tags", "origin")
    assert "vivarium-build-utils-v1.2.3" in remote_tags


def test_tag_version_is_idempotent(repo_with_remote: Path) -> None:
    first = _run_tag_version(repo_with_remote)
    assert first.returncode == 0, first.stderr
    second = _run_tag_version(repo_with_remote)
    assert second.returncode == 0, second.stderr
    assert "already exists" in second.stdout
    assert _git(repo_with_remote, "tag", "--list").split() == ["v1.2.3"]
