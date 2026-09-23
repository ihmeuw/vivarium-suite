"""Tests for the shared version helpers (``vivarium.build_utils.versions``).

Both functions were private near-duplicates in ``readme.py`` and
``dependency_graph/changes.py`` before they were hoisted here; these pin the two
behaviors their callers depend on, which were previously covered only in passing.
"""
import json
from pathlib import Path

import pytest

from vivarium.build_utils.versions import read_python_versions, version_key


class TestReadPythonVersions:
    """Tests for reading a library's ``python_versions.json``."""

    def test_reads_declared_versions(self, tmp_path: Path) -> None:
        """The file's contents come back as a list of strings."""
        (tmp_path / "python_versions.json").write_text(json.dumps(["3.11", "3.12"]))
        assert read_python_versions(tmp_path) == ["3.11", "3.12"]

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        """An absent file is not an error: callers decide whether that matters."""
        assert read_python_versions(tmp_path) == []

    def test_preserves_declared_order(self, tmp_path: Path) -> None:
        """Order is the file's - the Jenkins deploy reads the last entry as canonical."""
        (tmp_path / "python_versions.json").write_text(json.dumps(["3.13", "3.11"]))
        assert read_python_versions(tmp_path) == ["3.13", "3.11"]


class TestVersionKey:
    """Tests for the numeric version sort key."""

    def test_sorts_numerically_not_lexically(self) -> None:
        """The whole point: 3.9 orders below 3.10, which a string sort gets wrong."""
        assert sorted(["3.10", "3.9"], key=version_key) == ["3.9", "3.10"]

    @pytest.mark.parametrize(
        "version, expected", [("3.11", (3, 11)), ("3", (3,)), ("3.12.1", (3, 12, 1))]
    )
    def test_splits_on_dots(self, version: str, expected: tuple[int, ...]) -> None:
        """Every dotted component becomes an int, however many there are."""
        assert version_key(version) == expected
