"""Tests for the shared ``update-readme`` tool (``vivarium.build_utils.readme``).

The pure core is ``update_readme_text``; the ``main`` tests exercise the CLI's
file I/O, ``--check``/``--require-line`` modes, and default path resolution.
"""
import importlib.metadata
import json
from pathlib import Path

import pytest

from vivarium.build_utils.readme import load_versions, main, update_readme_text


@pytest.mark.parametrize(
    "text, versions, expected, count",
    [
        # Bold markup is preserved.
        (
            "**Supported Python versions: 3.9, 3.10**\n",
            ["3.10", "3.11", "3.12"],
            "**Supported Python versions: 3.10, 3.11, 3.12**\n",
            1,
        ),
        # Non-bold stays non-bold.
        (
            "Supported Python versions: 3.9, 3.10\n",
            ["3.10", "3.11"],
            "Supported Python versions: 3.10, 3.11\n",
            1,
        ),
        # A single-version list still matches.
        (
            "**Supported Python versions: 3.10, 3.11**\n",
            ["3.13"],
            "**Supported Python versions: 3.13**\n",
            1,
        ),
        # Versions are ordered numerically, not lexically ("3.9" < "3.10").
        (
            "**Supported Python versions: 3.8**\n",
            ["3.11", "3.9", "3.10"],
            "**Supported Python versions: 3.9, 3.10, 3.11**\n",
            1,
        ),
        # No marker: text unchanged, no substitutions.
        (
            "A README with no supported-Python declarations.\n",
            ["3.10", "3.11"],
            "A README with no supported-Python declarations.\n",
            0,
        ),
    ],
)
def test_update_readme_text(
    text: str, versions: list[str], expected: str, count: int
) -> None:
    updated, made = update_readme_text(text, versions)
    assert updated == expected
    assert made == count


def test_update_readme_text_is_idempotent() -> None:
    versions = ["3.10", "3.11", "3.12"]
    once, _ = update_readme_text("**Supported Python versions: 3.9**\n", versions)
    twice, _ = update_readme_text(once, versions)
    assert once == twice


def _write_lib(tmp_path: Path, readme: str, versions: list[str]) -> Path:
    """Write a README and python_versions.json into ``tmp_path``; return the dir."""
    (tmp_path / "README.rst").write_text(readme)
    (tmp_path / "python_versions.json").write_text(json.dumps(versions))
    return tmp_path


@pytest.mark.parametrize(
    "readme, versions, argv, exit_code, readme_substrings, stderr_substrings",
    [
        # Write mode fixes drift on disk (also covers positional-root resolution).
        (
            "**Supported Python versions: 3.10, 3.11, 3.12, 3.13**\n",
            ["3.11", "3.12", "3.13"],
            [],
            0,
            ["**Supported Python versions: 3.11, 3.12, 3.13**"],
            [],
        ),
        # --check on an in-sync README passes and writes nothing.
        (
            "**Supported Python versions: 3.10, 3.11**\n",
            ["3.10", "3.11"],
            ["--check"],
            0,
            ["3.10, 3.11"],
            [],
        ),
        # --check on drift fails, prints a diff, and leaves the file untouched.
        (
            "**Supported Python versions: 3.10, 3.11, 3.12, 3.13**\n",
            ["3.11", "3.12", "3.13"],
            ["--check"],
            1,
            ["3.10"],  # still on disk; --check writes nothing
            ["out of sync", "3.10"],  # diff reports the removed version
        ),
        # No marker: warn, exit 0.
        (
            "No version line here.\n",
            ["3.10", "3.11"],
            [],
            0,
            [],
            ["no supported-Python marker"],
        ),
        # No marker with --require-line: error, exit 1.
        (
            "No version line here.\n",
            ["3.10", "3.11"],
            ["--require-line"],
            1,
            [],
            ["ERROR"],
        ),
    ],
)
def test_main(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    readme: str,
    versions: list[str],
    argv: list[str],
    exit_code: int,
    readme_substrings: list[str],
    stderr_substrings: list[str],
) -> None:
    root = _write_lib(tmp_path, readme, versions)
    assert main([*argv, str(root)]) == exit_code
    err = capsys.readouterr().err
    on_disk = (root / "README.rst").read_text()
    for substring in readme_substrings:
        assert substring in on_disk
    for substring in stderr_substrings:
        assert substring in err


def test_main_updates_markdown_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("**Supported Python versions: 3.9**\n")
    (tmp_path / "python_versions.json").write_text(json.dumps(["3.10", "3.11"]))
    assert main([str(tmp_path)]) == 0
    assert "3.10, 3.11" in (tmp_path / "README.md").read_text()


def test_main_prefers_rst_over_md(tmp_path: Path) -> None:
    (tmp_path / "README.rst").write_text("**Supported Python versions: 3.9**\n")
    (tmp_path / "README.md").write_text("**Supported Python versions: 3.9**\n")
    (tmp_path / "python_versions.json").write_text(json.dumps(["3.10", "3.11"]))
    assert main([str(tmp_path)]) == 0
    assert "3.10, 3.11" in (tmp_path / "README.rst").read_text()
    assert "3.9" in (tmp_path / "README.md").read_text()  # untouched


def test_load_versions_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "python_versions.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_versions(path)


def test_console_script_entry_point_registered() -> None:
    scripts = importlib.metadata.entry_points(group="console_scripts")
    assert any(ep.name == "update-readme" for ep in scripts)
