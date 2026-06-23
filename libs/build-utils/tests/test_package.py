"""Smoke tests for the ``vivarium-build-utils`` package.

vbu's bulk content is Jenkins shared-library code (``vars/``, ``bootstrap/``)
and shared makefiles (``resources/makefiles/``), none of which is importable
Python. The Python surface is a thin pair of helpers in
``vivarium.build_utils.resources``. These tests verify:

1. The package's ``__version__`` resolves to a parseable distribution
   version (catches a typo in ``__init__.py``'s ``version("...")`` lookup
   that would silently fall through to the ``not-installed`` sentinel).
2. ``get_makefiles_path()`` returns a real directory shipped with the wheel
   (catches a regression where CustomBuildPy stops copying ``resources/``).
"""
from pathlib import Path

from packaging.version import Version

import vivarium.build_utils
from vivarium.build_utils.resources import get_makefiles_path, get_resources_path


def test_version_resolves_to_installed_distribution() -> None:
    assert vivarium.build_utils.__version__ != "0.0.0+not-installed"
    Version(vivarium.build_utils.__version__)


def test_get_resources_path_returns_existing_dir() -> None:
    assert Path(get_resources_path()).is_dir()


def test_get_makefiles_path_returns_dir_with_base_mk() -> None:
    makefiles = Path(get_makefiles_path())
    assert (makefiles / "base.mk").is_file()
