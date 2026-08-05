"""Tests for the ``deploy-docs`` target in ``resources/makefiles/base.mk``.

``deploy-docs`` publishes into a shared, web-served directory, so the directory
name it derives is part of a user-facing URL. It previously used
``PACKAGE_NAME`` (``$(notdir $(CURDIR))``), which under Jenkins is the
job-derived *workspace* name rather than the repo name - publishing standalone
repos' docs to an unreachable URL (MIC-7275).
"""

import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"

# A realistic Jenkins workspace directory name: folder prefix, branch, and the
# "@2" suffix Jenkins appends for a concurrent workspace.
JENKINS_WORKSPACE_NAME = "Private_vivarium_gbd_access_main@2"
REPO_NAME = "vivarium_gbd_access"


def _make(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(BASE_MK), *args], cwd=cwd, capture_output=True, text=True
    )


def _docs_name(cwd: Path, package_name: str) -> str:
    """Return the ``DOCS_NAME`` make resolves in ``cwd``."""
    result = _make(cwd, "debug", f"PACKAGE_NAME={package_name}")
    assert result.returncode == 0, result.stderr
    values = [
        line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.startswith("DOCS_NAME:")
    ]
    assert values, result.stdout
    return values[0]


def _init_repo(repo: Path, remote_url: str) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", remote_url],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "CHANGELOG.rst").write_text("**1.2.3 - 08/05/26**\n")


class TestDocsName:
    @pytest.mark.parametrize(
        "remote_url",
        [
            f"https://github.com/ihmeuw/{REPO_NAME}.git",
            f"git@github.com:ihmeuw/{REPO_NAME}.git",
            f"https://github.com/ihmeuw/{REPO_NAME}",
        ],
    )
    def test_uses_git_remote_at_repo_root(self, tmp_path: Path, remote_url: str) -> None:
        # The checkout directory is named after the Jenkins job, so PACKAGE_NAME
        # is unusable here and the remote is the only reliable source.
        repo = tmp_path / JENKINS_WORKSPACE_NAME
        _init_repo(repo, remote_url)
        assert _docs_name(repo, JENKINS_WORKSPACE_NAME) == REPO_NAME

    def test_uses_package_dir_in_monorepo(self, tmp_path: Path) -> None:
        # Monorepo libs run make from libs/<pkg>/, where the directory name *is*
        # the package name, so it must win over the repo-level remote.
        package_dir = tmp_path / "libs" / "engine"
        _init_repo(package_dir, "https://github.com/ihmeuw/vivarium-suite.git")
        assert _docs_name(package_dir, "engine") == "engine"


class TestDeployDocs:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / JENKINS_WORKSPACE_NAME
        _init_repo(repo, f"https://github.com/ihmeuw/{REPO_NAME}.git")
        html = repo / "docs" / "build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text("<html>docs</html>")
        return repo

    def test_publishes_versioned_dir_and_current_symlink(
        self, repo: Path, tmp_path: Path
    ) -> None:
        docs_root = tmp_path / "docs_root"
        result = _make(repo, "deploy-docs", f"DOCS_ROOT_PATH={docs_root}")
        assert result.returncode == 0, result.stderr
        published = docs_root / REPO_NAME
        assert (published / "1.2.3" / "index.html").read_text() == "<html>docs</html>"
        assert (published / "current").is_symlink()
        assert (published / "current").readlink() == Path("1.2.3")

    def test_refuses_workspace_like_name(self, repo: Path, tmp_path: Path) -> None:
        docs_root = tmp_path / "docs_root"
        result = _make(
            repo,
            "deploy-docs",
            f"DOCS_ROOT_PATH={docs_root}",
            f"DOCS_NAME={JENKINS_WORKSPACE_NAME}",
        )
        assert result.returncode != 0
        assert "refusing to publish" in result.stdout + result.stderr
        assert not docs_root.exists()

    def test_refuses_empty_name(self, repo: Path, tmp_path: Path) -> None:
        docs_root = tmp_path / "docs_root"
        result = _make(repo, "deploy-docs", f"DOCS_ROOT_PATH={docs_root}", "DOCS_NAME=")
        assert result.returncode != 0
        assert "DOCS_NAME is empty" in result.stdout + result.stderr
        assert not docs_root.exists()
