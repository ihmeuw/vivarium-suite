"""Smoke tests for the ``vivarium-build-utils`` package.

vbu's bulk content is Jenkins shared-library code (``vars/``, ``bootstrap/``)
and shared makefiles (``resources/makefiles/``), none of which is importable
Python. The Python surface is a thin pair of helpers in
``vivarium.build_utils.resources``. These tests verify:

1. The distribution installs and reports a parseable version (satisfies
   ``make test-all`` for the release workflow).
2. ``get_makefiles_path()`` returns a real directory shipped with the wheel
   (catches a regression where CustomBuildPy stops copying ``resources/``).
"""
from importlib.metadata import metadata
from pathlib import Path

from packaging.version import Version

from vivarium.build_utils.resources import get_makefiles_path, get_resources_path


def test_version_resolves_to_installed_distribution() -> None:
    Version(metadata("vivarium-build-utils")["Version"])


def test_get_resources_path_returns_existing_dir() -> None:
    assert Path(get_resources_path()).is_dir()


def test_get_makefiles_path_returns_dir_with_base_mk() -> None:
    makefiles = Path(get_makefiles_path())
    assert (makefiles / "base.mk").is_file()
