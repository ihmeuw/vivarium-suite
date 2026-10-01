"""Tests for ``resources/scripts/resolve_deploy_base.sh``.

The script picks the commit Jenkins' deploy checks (``has_changelog_update`` and
``has_deployable_change``) diff HEAD against. These tests run it against throwaway
repos, so nothing touches the network.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_resources_path

RESOURCES = Path(get_resources_path())
SCRIPT = RESOURCES / "scripts" / "resolve_deploy_base.sh"
VARS = Path(__file__).parents[1] / "vars"

_UNSET = object()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _commit(repo: Path, message: str, files: dict[str, str] | None = None) -> str:
    """Commit ``files`` (or an empty commit) and return the new HEAD's full SHA."""
    if files:
        for name, content in files.items():
            (repo / name).write_text(content)
            _git(repo, "add", name)
        _git(repo, "commit", "-m", message)
    else:
        _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run_script(
    repo: Path, candidate: str | object = _UNSET
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("CANDIDATE_BASE_COMMIT", None)
    if candidate is not _UNSET:
        assert isinstance(candidate, str)
        env["CANDIDATE_BASE_COMMIT"] = candidate
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True
    )


def _resolved(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    return result.stdout.rstrip("\n")


def _groovy_function_body(source: str, name: str) -> str:
    """Return the body of top-level ``def <name>()`` in groovy ``source``."""
    pattern = rf"^def {re.escape(name)}\(\)\s*\{{(.*?)^\}}"
    match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found"
    return match.group(1)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


class TestUsesCandidate:
    def test_uses_candidate_when_strict_ancestor_of_head(self, repo: Path) -> None:
        """Print the candidate's full SHA when it is an ancestor of HEAD other than HEAD."""
        base = _commit(repo, "base")
        _commit(repo, "middle")
        _commit(repo, "tip")
        assert _resolved(_run_script(repo, base)) == base

    def test_resolves_abbreviated_candidate_to_full_sha(self, repo: Path) -> None:
        """Print the full SHA when the candidate is given as an abbreviated SHA."""
        base = _commit(repo, "base")
        _commit(repo, "middle")
        _commit(repo, "tip")
        assert len(base) == 40
        assert _resolved(_run_script(repo, base[:10])) == base

    def test_multi_commit_push_sees_changelog_bump_in_non_final_commit(
        self, repo: Path
    ) -> None:
        """Make a changelog bump from an earlier commit of a multi-commit push visible to the deploy diff."""
        base = _commit(
            repo,
            "base",
            {"CHANGELOG.rst": "**1.0.0 - 01/01/26**\n\n - Initial\n", "src.py": "x = 1\n"},
        )
        _commit(
            repo,
            "bump changelog",
            {
                "CHANGELOG.rst": "**1.1.0 - 01/02/26**\n\n - Change\n\n"
                "**1.0.0 - 01/01/26**\n\n - Initial\n"
            },
        )
        _commit(repo, "touch source", {"src.py": "x = 2\n"})

        # The pre-existing HEAD~1 base misses the bump.
        assert (
            "CHANGELOG.rst" not in _git(repo, "diff", "--name-only", "HEAD~1", "HEAD").split()
        )

        resolved = _resolved(_run_script(repo, base))
        assert resolved == base
        assert "CHANGELOG.rst" in _git(repo, "diff", "--name-only", resolved, "HEAD").split()
        base_first = _git(repo, "show", f"{resolved}:CHANGELOG.rst").splitlines()[0]
        head_first = _git(repo, "show", "HEAD:CHANGELOG.rst").splitlines()[0]
        assert base_first != head_first


class TestFallsBackToParent:
    @pytest.fixture
    def parent(self, repo: Path) -> str:
        """Build a three-commit history and return HEAD~1's full SHA."""
        _commit(repo, "first")
        parent = _commit(repo, "second")
        _commit(repo, "third")
        return parent

    def test_falls_back_when_candidate_unset(self, repo: Path, parent: str) -> None:
        """Print HEAD~1's full SHA when CANDIDATE_BASE_COMMIT is unset."""
        assert _resolved(_run_script(repo)) == parent

    def test_falls_back_when_candidate_empty(self, repo: Path, parent: str) -> None:
        """Print HEAD~1's full SHA when CANDIDATE_BASE_COMMIT is empty."""
        assert _resolved(_run_script(repo, "")) == parent

    def test_falls_back_when_candidate_missing_from_clone(
        self, repo: Path, parent: str
    ) -> None:
        """Print HEAD~1's full SHA when the candidate is not an object in the clone."""
        assert _resolved(_run_script(repo, "deadbeef" * 5)) == parent

    def test_falls_back_when_candidate_not_ancestor_of_head(self, repo: Path) -> None:
        """Print HEAD~1's full SHA when the candidate is on rewritten or unrelated history."""
        _commit(repo, "first")
        _commit(repo, "second")
        rewritten = _commit(repo, "rewritten away")
        _git(repo, "reset", "--hard", "HEAD~1")
        parent = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, "replacement")
        # The old commit still exists in the clone but is no longer HEAD's ancestor.
        assert _git(repo, "cat-file", "-t", rewritten).strip() == "commit"
        assert _resolved(_run_script(repo, rewritten)) == parent

    def test_falls_back_when_candidate_is_head(self, repo: Path, parent: str) -> None:
        """Print HEAD~1's full SHA when the candidate is HEAD itself, as on a forced rebuild."""
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert _resolved(_run_script(repo, head)) == parent


class TestFailure:
    def test_fails_when_head_is_root_commit_and_no_candidate(self, repo: Path) -> None:
        """Exit nonzero with nothing on stdout when HEAD has no parent and no usable candidate."""
        _commit(repo, "root")
        result = _run_script(repo)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.strip()


class TestJenkinsWiring:
    def test_library_resources_referenced_by_vars_exist(self) -> None:
        """Every ``libraryResource('<path>')`` in ``vars/`` names a file under ``resources/``."""
        pattern = re.compile(r"""libraryResource\(\s*(['"])(.+?)\1\s*\)""")
        referenced = [
            (groovy.name, match.group(2))
            for groovy in sorted(VARS.glob("*.groovy"))
            for match in pattern.finditer(groovy.read_text())
        ]
        assert referenced, f"no libraryResource calls found under {VARS}"
        missing = [
            (name, path) for name, path in referenced if not (RESOURCES / path).is_file()
        ]
        assert not missing, f"libraryResource paths not found under {RESOURCES}: {missing}"

    def test_deploy_base_reads_last_successful_build_commit(self) -> None:
        """``getCommitInfo`` diffs against ``getDeployBaseCommit``.

        It passes a null-safe GIT_PREVIOUS_SUCCESSFUL_COMMIT to the script.
        """
        source = (VARS / "git_utils.groovy").read_text()
        body = _groovy_function_body(source, "getDeployBaseCommit")
        assert "GIT_PREVIOUS_SUCCESSFUL_COMMIT" in body
        # Without ``?: ''`` a null env var interpolates as the string "null".
        assert re.search(r"GIT_PREVIOUS_SUCCESSFUL_COMMIT\s*\?:\s*''", body)
        assert "scripts/resolve_deploy_base.sh" in body
        assert "getDeployBaseCommit()" in _groovy_function_body(source, "getCommitInfo")
