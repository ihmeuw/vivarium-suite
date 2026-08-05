"""Tests for the ``deploy-docs`` target in ``resources/makefiles/base.mk``.

``deploy-docs`` publishes into a shared, web-served directory, so the directory
name it derives is part of a user-facing URL. It previously used
``PACKAGE_NAME`` (``$(notdir $(CURDIR))``), which for a repo built at its root is
the Jenkins job-derived *workspace* name rather than the package - publishing
docs to an unreachable URL (MIC-7275).
"""

import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"

# A realistic Jenkins workspace directory name: folder prefix, branch, and the
# "@2" suffix Jenkins appends for a concurrent workspace.
JENKINS_WORKSPACE_NAME = "Private_vivarium_gbd_access_main@2"
DIST_NAME = "vivarium_gbd_access"


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


def _write_package(directory: Path, dist_name: str | None) -> None:
    directory.mkdir(parents=True)
    project = f'[project]\nname = "{dist_name}"\n' if dist_name else "[project]\n"
    (directory / "pyproject.toml").write_text(project)
    (directory / "CHANGELOG.rst").write_text("**1.2.3 - 08/05/26**\n")


class TestDocsName:
    def test_uses_dist_name_not_workspace_dir(self, tmp_path: Path) -> None:
        # The checkout directory is named after the Jenkins job, so PACKAGE_NAME is
        # unusable here and the distribution name is the only reliable source.
        repo = tmp_path / JENKINS_WORKSPACE_NAME
        _write_package(repo, DIST_NAME)
        assert _docs_name(repo, JENKINS_WORKSPACE_NAME) == DIST_NAME

    def test_uses_dist_name_in_monorepo(self, tmp_path: Path) -> None:
        package_dir = tmp_path / "libs" / "engine"
        _write_package(package_dir, "vivarium-engine")
        assert _docs_name(package_dir, "engine") == "vivarium-engine"

    def test_is_empty_without_project_name(self, tmp_path: Path) -> None:
        # Must not silently fall back to PACKAGE_NAME the way DIST_NAME does.
        repo = tmp_path / JENKINS_WORKSPACE_NAME
        _write_package(repo, None)
        assert _docs_name(repo, JENKINS_WORKSPACE_NAME) == ""


class TestDeployDocs:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / JENKINS_WORKSPACE_NAME
        _write_package(repo, DIST_NAME)
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
        published = docs_root / DIST_NAME
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
