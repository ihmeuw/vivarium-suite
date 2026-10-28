"""Tests for release version validation (``vivarium.build_utils.release_version``).

``validate-tag`` is the last check before a release workflow creates a tag and
publishes to PyPI, where a version can never be reused. A beta read as its final
would publish the final by mistake, so these tests pin both the accepted formats
and the allowed version steps.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vivarium.build_utils.release_version import (
    check_bump,
    is_release_version,
    main,
    notes_start_tag,
    read_changelog_version,
    released_versions,
    validate_tag,
)
from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"
PREFIX = "vivarium-engine-"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return a git repo with one commit and a CHANGELOG for ``1.3.0b1``."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "commit", "--allow-empty", "-m", "init")
    (tmp_path / "CHANGELOG.rst").write_text("**1.3.0b1 - 11/03/26**\n\n- Add a feature\n")
    return tmp_path


class TestIsReleaseVersion:
    @pytest.mark.parametrize("version", ["0.1.0", "1.3.0", "10.20.30", "1.3.0b1", "1.3.0b12"])
    def test_accepts_finals_and_betas(self, version: str) -> None:
        assert is_release_version(version)

    @pytest.mark.parametrize(
        "version",
        [
            "1.3.0a1",
            "1.3.0rc1",
            "1.3.0b0",
            "1.3.0-beta.1",
            "1.3.0.dev1",
            "1.3.0.post1",
            "01.3.0",
            "1.3",
            "1.3.0b",
        ],
    )
    def test_rejects_other_forms(self, version: str) -> None:
        """Only beta pre-releases are supported; alpha and rc are rejected."""
        assert not is_release_version(version)


class TestReadChangelogVersion:
    def test_reads_beta_heading(self, tmp_path: Path) -> None:
        changelog = tmp_path / "CHANGELOG.rst"
        changelog.write_text("**1.3.0b1 - 11/03/26**\n\n- Add a feature\n")
        assert read_changelog_version(changelog) == "1.3.0b1"

    def test_keeps_an_unsupported_suffix(self, tmp_path: Path) -> None:
        """``1.3.0rc1`` comes back whole, so validation rejects it instead of reading ``1.3.0``."""
        changelog = tmp_path / "CHANGELOG.rst"
        changelog.write_text("**1.3.0rc1 - 11/03/26**\n")
        assert read_changelog_version(changelog) == "1.3.0rc1"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_changelog_version(tmp_path / "CHANGELOG.rst")

    def test_missing_heading_raises(self, tmp_path: Path) -> None:
        changelog = tmp_path / "CHANGELOG.rst"
        changelog.write_text("No heading here\n")
        with pytest.raises(ValueError, match="changelog heading"):
            read_changelog_version(changelog)


class TestCheckBump:
    @pytest.mark.parametrize(
        "released, current",
        [
            (["1.2.0"], "1.2.1"),
            (["1.2.0"], "1.3.0"),
            (["1.2.0"], "2.0.0"),
            (["1.2.0"], "1.3.0b1"),
            (["1.2.0"], "1.2.1b1"),
            (["1.2.0"], "2.0.0b1"),
            (["1.2.0", "1.3.0b1"], "1.3.0b2"),
            (["1.2.0", "1.3.0b1", "1.3.0b2"], "1.3.0"),
            (["1.2.0", "1.3.0b1"], "2.0.0b1"),
            (["1.2.0", "1.3.0b1"], "1.2.1"),
            (["1.2.0", "1.3.0"], "1.2.1"),
            ([], "0.1.0"),
            ([], "0.1.0b1"),
        ],
        ids=[
            "patch",
            "minor",
            "major",
            "final to minor beta",
            "final to patch beta",
            "final to major beta",
            "beta to next beta",
            "beta to its final",
            "abandon a beta line for another version",
            "patch while a beta is open",
            "patch of an older line",
            "first final",
            "first beta",
        ],
    )
    def test_allows(self, released: list[str], current: str) -> None:
        assert check_bump(current, released) is None

    @pytest.mark.parametrize(
        "released, current, message",
        [
            (["1.2.0"], "1.4.0", "not one step"),
            (["1.2.0"], "1.3.0b2", "must be 1.3.0b1"),
            (["1.2.0"], "1.4.0b1", "not one step"),
            (["1.2.0", "1.3.0b1"], "1.3.0b3", "must be 1.3.0b2"),
            (["1.2.0", "1.3.0b1", "1.3.0"], "1.3.0b2", "already released"),
            (["1.2.0", "1.3.0b1"], "1.4.0", "not one step"),
            ([], "0.1.0b2", "must be 0.1.0b1"),
        ],
        ids=[
            "skipped minor",
            "first beta is not b1",
            "beta of a skipped version",
            "skipped beta",
            "beta after its final",
            "final skips past the beta line",
            "first-ever beta is not b1",
        ],
    )
    def test_rejects(self, released: list[str], current: str, message: str) -> None:
        error = check_bump(current, released)
        assert error is not None
        assert message in error

    def test_ignores_current_in_released(self) -> None:
        """A rerun after the tag exists still checks the step from the previous release."""
        assert check_bump("1.3.0b1", ["1.2.0", "1.3.0b1"]) is None


class TestNotesStartTag:
    def test_final_after_betas_starts_at_last_final(self) -> None:
        released = ["1.2.0", "1.3.0b1", "1.3.0b2"]
        assert notes_start_tag("1.3.0", released, PREFIX) == f"{PREFIX}v1.2.0"

    def test_final_after_final_uses_default(self) -> None:
        assert notes_start_tag("1.3.0", ["1.2.0"], PREFIX) is None

    def test_beta_uses_default(self) -> None:
        assert notes_start_tag("1.3.0b2", ["1.2.0", "1.3.0b1"], PREFIX) is None

    def test_first_final_after_betas_uses_default(self) -> None:
        assert notes_start_tag("0.1.0", ["0.1.0b1"], PREFIX) is None


class TestValidateTag:
    def test_returns_matching_beta(self, repo: Path) -> None:
        tag = f"{PREFIX}v1.3.0b1"
        assert validate_tag(tag, PREFIX, repo / "CHANGELOG.rst", ["1.2.0"]) == "1.3.0b1"

    def test_rejects_tag_that_is_not_a_release_version(self, repo: Path) -> None:
        with pytest.raises(ValueError, match="must be"):
            validate_tag(f"{PREFIX}v1.3.0rc1", PREFIX, repo / "CHANGELOG.rst", [])

    def test_rejects_unsupported_changelog_version(self, repo: Path) -> None:
        """A ``1.3.0rc1`` heading must not pass as the ``1.3.0`` the workflow parsed from it."""
        (repo / "CHANGELOG.rst").write_text("**1.3.0rc1 - 11/03/26**\n")
        with pytest.raises(ValueError, match="Latest CHANGELOG version '1.3.0rc1'"):
            validate_tag(f"{PREFIX}v1.3.0", PREFIX, repo / "CHANGELOG.rst", ["1.2.0"])

    def test_rejects_mismatch(self, repo: Path) -> None:
        with pytest.raises(ValueError, match="Version mismatch"):
            validate_tag(f"{PREFIX}v1.3.0", PREFIX, repo / "CHANGELOG.rst", ["1.2.0"])

    def test_rejects_invalid_bump(self, repo: Path) -> None:
        with pytest.raises(ValueError, match="Invalid version bump"):
            validate_tag(f"{PREFIX}v1.3.0b1", PREFIX, repo / "CHANGELOG.rst", ["1.0.0"])


class TestReleasedVersions:
    def test_reads_only_this_prefix_and_release_versions(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for tag in [
            f"{PREFIX}v1.2.0",
            f"{PREFIX}v1.3.0b1",
            f"{PREFIX}v1.3.0rc1",
            "vivarium-engine-extra-v9.0.0",
            "vivarium-config-tree-v5.0.0",
        ]:
            _git(repo, "tag", tag)
        monkeypatch.chdir(repo)
        assert sorted(released_versions(PREFIX)) == ["1.2.0", "1.3.0b1"]


class TestMain:
    def test_validate_tag_passes(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, "tag", f"{PREFIX}v1.2.0")
        monkeypatch.chdir(repo)
        monkeypatch.setenv("TAG_PREFIX", PREFIX)
        monkeypatch.setenv("RELEASE_TAG", f"{PREFIX}v1.3.0b1")
        assert main(["validate-tag"]) == 0
        assert "passed for 1.3.0b1" in capsys.readouterr().out

    def test_validate_tag_fails_on_bad_bump(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, "tag", f"{PREFIX}v1.0.0")
        monkeypatch.chdir(repo)
        monkeypatch.setenv("TAG_PREFIX", PREFIX)
        monkeypatch.setenv("RELEASE_TAG", f"{PREFIX}v1.3.0b1")
        assert main(["validate-tag"]) == 1
        assert "Invalid version bump" in capsys.readouterr().err

    def test_validate_tag_falls_back_to_github_ref_name(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(repo)
        monkeypatch.delenv("TAG_PREFIX", raising=False)
        monkeypatch.delenv("RELEASE_TAG", raising=False)
        monkeypatch.setenv("GITHUB_REF_NAME", "v1.3.0b1")
        assert main(["validate-tag"]) == 0

    def test_validate_tag_without_tag_fails(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(repo)
        monkeypatch.delenv("RELEASE_TAG", raising=False)
        monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
        assert main(["validate-tag"]) == 1

    def test_notes_start_tag_prints_last_final(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for tag in [f"{PREFIX}v1.2.0", f"{PREFIX}v1.3.0b1", f"{PREFIX}v1.3.0"]:
            _git(repo, "tag", tag)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("TAG_PREFIX", PREFIX)
        assert main(["notes-start-tag", "--version", "1.3.0"]) == 0
        assert capsys.readouterr().out.strip() == f"{PREFIX}v1.2.0"


class TestMakeTargets:
    def _make(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        # make runs `python`, so put this interpreter first on PATH.
        env = {
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        }
        return subprocess.run(
            ["make", "-f", str(BASE_MK), *args],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_package_version_reads_beta(self, repo: Path) -> None:
        """Jenkins tags ``PACKAGE_VERSION``, so it must keep the ``bN`` suffix."""
        result = self._make(repo, "debug")
        assert result.returncode == 0, result.stderr
        assert "PACKAGE_VERSION:                  1.3.0b1" in result.stdout

    def test_validate_tag_defaults_to_package_version_tag(self, repo: Path) -> None:
        """Jenkins sets no tag, so the target checks the tag ``tag-version`` would create."""
        _git(repo, "tag", "v1.0.0")
        result = self._make(repo, "validate-tag", "RELEASE_TAG=", "GITHUB_REF_NAME=")
        assert result.returncode != 0
        assert "1.3.0 is not one step from the last final release 1.0.0" in result.stderr

    def test_validate_tag_runs_python_validator(self, repo: Path) -> None:
        _git(repo, "tag", f"{PREFIX}v1.2.0")
        result = self._make(
            repo, "validate-tag", f"RELEASE_TAG={PREFIX}v1.3.0b1", f"TAG_PREFIX={PREFIX}"
        )
        assert result.returncode == 0, result.stderr
        assert "passed for 1.3.0b1" in result.stdout
