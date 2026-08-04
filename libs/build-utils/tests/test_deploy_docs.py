"""Tests for the ``deploy-docs`` target in ``resources/makefiles/base.mk``.

``deploy-docs`` publishes a built Sphinx tree to the shared docs server: it copies
``./docs/build/html`` to ``<DOCS_ROOT_PATH>/<PACKAGE_NAME>/<PACKAGE_VERSION>`` and
repoints a ``current`` symlink at that version. It runs on release builds and on
docs-only changes alike, so these tests pin the republish-in-place behavior that a
docs-only publish relies on. ``base.mk`` documents the full contract.

These tests drive the target against a throwaway ``tmp_path`` docs root, so nothing
touches the shared filesystem. See ``test_tag_version.py`` for the established
pattern for driving a ``base.mk`` target from pytest.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"

PACKAGE_NAME = "vivarium-fake-package"
VERSION = "1.2.3"


def _build_docs(source_dir: Path, marker: str) -> None:
    """Stand up a fake built Sphinx tree at ``<source_dir>/docs/build/html``."""
    html = source_dir / "docs" / "build" / "html"
    (html / "_static").mkdir(parents=True, exist_ok=True)
    (html / "index.html").write_text(marker)
    (html / "_static" / "styles.css").write_text(f"/* {marker} */")
    # Sphinx writes .buildinfo at the top of the built tree.
    (html / ".buildinfo").write_text(marker)


def _run_deploy_docs(
    source_dir: Path, docs_root: Path | None, version: str = VERSION
) -> subprocess.CompletedProcess[str]:
    """Run ``deploy-docs`` from ``source_dir``, omitting DOCS_ROOT_PATH when it is None."""
    variables = [f"PACKAGE_NAME={PACKAGE_NAME}", f"PACKAGE_VERSION={version}"]
    if docs_root is not None:
        variables.append(f"DOCS_ROOT_PATH={docs_root}")
    # make inherits the environment, so scrub DOCS_ROOT_PATH to keep a developer's
    # shell from pointing these tests at the real docs server. MAKEFLAGS goes too,
    # since `make test-all` runs pytest under make and would leak into this inner make.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DOCS_ROOT_PATH", "MAKEFLAGS")
    }
    return subprocess.run(
        ["make", "-f", str(BASE_MK), "deploy-docs", *variables],
        cwd=source_dir,
        capture_output=True,
        text=True,
        env=env,
    )


def _published_dir(docs_root: Path, version: str = VERSION) -> Path:
    """Return the directory a given version publishes to."""
    return docs_root / PACKAGE_NAME / version


def _current_link(docs_root: Path) -> Path:
    """Return the package's ``current`` symlink."""
    return docs_root / PACKAGE_NAME / "current"


class TestDeployDocs:
    @pytest.fixture
    def source_dir(self, tmp_path: Path) -> Path:
        source = tmp_path / "package"
        source.mkdir()
        _build_docs(source, "first build")
        return source

    @pytest.fixture
    def docs_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "docs_root"
        root.mkdir()
        return root

    def test_errors_when_docs_root_path_unset(self, source_dir: Path) -> None:
        """Fail with a clear message when DOCS_ROOT_PATH is not set."""
        result = _run_deploy_docs(source_dir, docs_root=None)
        assert result.returncode != 0
        assert "DOCS_ROOT_PATH is not set" in result.stdout + result.stderr

    def test_publishes_built_html_under_package_and_version(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Copy the built html tree to <root>/<package>/<version>/."""
        result = _run_deploy_docs(source_dir, docs_root)
        assert result.returncode == 0, result.stderr
        published = _published_dir(docs_root)
        assert (published / "index.html").read_text() == "first build"
        assert (published / "_static" / "styles.css").read_text() == "/* first build */"

    def test_publishes_dotfiles(self, source_dir: Path, docs_root: Path) -> None:
        """Include dotfiles such as Sphinx's .buildinfo in the published tree."""
        result = _run_deploy_docs(source_dir, docs_root)
        assert result.returncode == 0, result.stderr
        assert (_published_dir(docs_root) / ".buildinfo").read_text() == "first build"

    def test_current_symlink_resolves_to_deployed_version(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Point <root>/<package>/current at the version just deployed."""
        result = _run_deploy_docs(source_dir, docs_root)
        assert result.returncode == 0, result.stderr
        current = _current_link(docs_root)
        assert current.is_symlink()
        # Relative link, made from inside the package dir, so the docs root stays movable.
        assert os.readlink(current) == VERSION
        # resolve() is non-strict, so it alone would accept a dangling link.
        assert current.is_dir()
        assert current.resolve() == _published_dir(docs_root).resolve()

    def test_redeploying_same_version_republishes_in_place(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Overwrite an already-published version in place, leaving current on it."""
        first = _run_deploy_docs(source_dir, docs_root)
        assert first.returncode == 0, first.stderr
        published = _published_dir(docs_root)
        assert (published / "index.html").read_text() == "first build"

        _build_docs(source_dir, "second build")
        second = _run_deploy_docs(source_dir, docs_root)
        assert second.returncode == 0, second.stderr
        assert (published / "index.html").read_text() == "second build"
        assert (published / "_static" / "styles.css").read_text() == "/* second build */"
        assert os.readlink(_current_link(docs_root)) == VERSION
        # Read through the link: this is what the fix actually buys a docs reader.
        assert (_current_link(docs_root) / "index.html").read_text() == "second build"

    def test_deploying_new_version_repoints_current(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Repoint current at a newly deployed version, keeping the old one on disk."""
        new_version = "2.0.0"
        first = _run_deploy_docs(source_dir, docs_root, version=VERSION)
        assert first.returncode == 0, first.stderr

        _build_docs(source_dir, "next build")
        second = _run_deploy_docs(source_dir, docs_root, version=new_version)
        assert second.returncode == 0, second.stderr
        assert (_published_dir(docs_root) / "index.html").read_text() == "first build"
        assert (
            _published_dir(docs_root, new_version) / "index.html"
        ).read_text() == "next build"
        current = _current_link(docs_root)
        assert os.readlink(current) == new_version
        assert current.resolve() == _published_dir(docs_root, new_version).resolve()

    def test_errors_when_package_version_empty(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Fail rather than publish into the package root when PACKAGE_VERSION is empty."""
        published = _published_dir(docs_root)
        published.mkdir(parents=True)
        (published / "index.html").write_text("published")
        _current_link(docs_root).symlink_to(VERSION)

        result = _run_deploy_docs(source_dir, docs_root, version="")

        assert result.returncode != 0
        # Pin the guard specifically, so this keeps failing for the right reason if
        # make dies earlier for an unrelated one.
        assert "PACKAGE_VERSION is empty" in result.stdout + result.stderr
        # An empty version collapses every path to the package root: the build would
        # land beside the version directories and `current` would link to itself.
        assert not (docs_root / PACKAGE_NAME / "index.html").exists()
        assert os.readlink(_current_link(docs_root)) == VERSION

    def test_errors_when_built_tree_is_empty(self, source_dir: Path, docs_root: Path) -> None:
        """Fail rather than repoint current at an empty tree when the build produced nothing."""
        published = _published_dir(docs_root)
        published.mkdir(parents=True)
        (published / "index.html").write_text("live docs")
        _current_link(docs_root).symlink_to(VERSION)
        for path in (source_dir / "docs" / "build" / "html").rglob("*"):
            if path.is_file():
                path.unlink()

        result = _run_deploy_docs(source_dir, docs_root, version="2.0.0")

        assert result.returncode != 0
        assert "empty or missing" in result.stdout + result.stderr
        # Publishing an empty tree would take the live docs offline.
        assert os.readlink(_current_link(docs_root)) == VERSION
        assert not _published_dir(docs_root, "2.0.0").exists()

    def test_republish_does_not_remove_pages_dropped_from_the_build(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Pin cp -R's overlay semantics: a page dropped from the build stays published."""
        html = source_dir / "docs" / "build" / "html"
        (html / "removed.html").write_text("first build")
        assert _run_deploy_docs(source_dir, docs_root).returncode == 0

        (html / "removed.html").unlink()
        _build_docs(source_dir, "second build")
        assert _run_deploy_docs(source_dir, docs_root).returncode == 0

        published = _published_dir(docs_root)
        assert (published / "index.html").read_text() == "second build"
        # Stale pages persist until the next version bump gets a fresh directory.
        assert (published / "removed.html").exists()

    def test_published_tree_is_group_writable(
        self, source_dir: Path, docs_root: Path
    ) -> None:
        """Leave the published tree group-writable so another account can republish over it."""
        result = _run_deploy_docs(source_dir, docs_root)
        assert result.returncode == 0, result.stderr
        published = _published_dir(docs_root)
        for path in [published, *published.rglob("*")]:
            assert path.stat().st_mode & stat.S_IWGRP, path
