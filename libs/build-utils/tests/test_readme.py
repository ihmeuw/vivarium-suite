"""Tests for the shared ``update-readme`` tool (``vivarium.build_utils.readme``).

The pure core is ``update_readme_text``; the ``main`` tests exercise the CLI's
file I/O, ``--check``/``--require-line`` modes, and default path resolution.
"""
import importlib.metadata
import json
from pathlib import Path

import pytest

from vivarium.build_utils.readme import load_versions, main, update_readme_text


def test_bold_enumerated_preserves_markup() -> None:
    text = "**Supported Python versions: 3.9, 3.10**\n"
    updated, count = update_readme_text(text, ["3.10", "3.11", "3.12"])
    assert updated == "**Supported Python versions: 3.10, 3.11, 3.12**\n"
    assert count == 1


def test_non_bold_enumerated_stays_non_bold() -> None:
    text = "Supported Python versions: 3.9, 3.10\n"
    updated, _ = update_readme_text(text, ["3.10", "3.11"])
    assert updated == "Supported Python versions: 3.10, 3.11\n"
    assert "**" not in updated


def test_single_version_enumerated() -> None:
    text = "**Supported Python versions: 3.10, 3.11**\n"
    updated, count = update_readme_text(text, ["3.13"])
    assert updated == "**Supported Python versions: 3.13**\n"
    assert count == 1


def test_floor_form_updates_to_min() -> None:
    text = "requires Python 3.9+ to run\n"
    updated, count = update_readme_text(text, ["3.10", "3.11"])
    assert updated == "requires Python 3.10+ to run\n"
    assert count == 1


def test_install_pin_updates_to_max() -> None:
    text = "conda create -n ENV python=3.10\n"
    updated, count = update_readme_text(text, ["3.10", "3.11", "3.12"])
    assert updated == "conda create -n ENV python=3.12\n"
    assert count == 1


def test_config_tree_double_form() -> None:
    text = "**Supported Python versions: 3.9, 3.10**\n" "   conda create -n ENV python=3.9\n"
    updated, count = update_readme_text(text, ["3.10", "3.11", "3.12", "3.13"])
    assert "**Supported Python versions: 3.10, 3.11, 3.12, 3.13**" in updated
    assert "python=3.13" in updated
    assert count == 2


def test_idempotent() -> None:
    versions = ["3.10", "3.11", "3.12"]
    text = "**Supported Python versions: 3.9**\n   python=3.9\n"
    once, _ = update_readme_text(text, versions)
    twice, _ = update_readme_text(once, versions)
    assert once == twice


def test_numeric_sort_not_lexical() -> None:
    text = "**Supported Python versions: 3.8**\n"
    updated, _ = update_readme_text(text, ["3.11", "3.9", "3.10"])
    # Lexical sorting would order "3.10" < "3.9"; numeric must not.
    assert updated == "**Supported Python versions: 3.9, 3.10, 3.11**\n"


def test_pip_double_equals_untouched() -> None:
    text = "pip install vivarium-x==3.13\n"
    updated, count = update_readme_text(text, ["3.10", "3.11"])
    assert updated == text
    assert count == 0


def test_no_marker_returns_zero_substitutions() -> None:
    text = "A README with no supported-Python declarations.\n"
    updated, count = update_readme_text(text, ["3.10", "3.11"])
    assert updated == text
    assert count == 0


def _write_lib(tmp_path: Path, readme: str, versions: list[str]) -> Path:
    """Write a README and python_versions.json into ``tmp_path``; return the dir."""
    (tmp_path / "README.rst").write_text(readme)
    (tmp_path / "python_versions.json").write_text(json.dumps(versions))
    return tmp_path


def test_load_versions_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "python_versions.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_versions(path)


def test_write_mode_updates_file(tmp_path: Path) -> None:
    root = _write_lib(
        tmp_path,
        "**Supported Python versions: 3.10, 3.11, 3.12, 3.13**\n",
        ["3.11", "3.12", "3.13"],
    )
    assert main([str(root)]) == 0
    assert (
        root / "README.rst"
    ).read_text() == "**Supported Python versions: 3.11, 3.12, 3.13**\n"


def test_root_positional_resolves_defaults(tmp_path: Path) -> None:
    root = _write_lib(tmp_path, "**Supported Python versions: 3.9**\n", ["3.10", "3.11"])
    assert main([str(root)]) == 0
    assert "3.10, 3.11" in (root / "README.rst").read_text()


def test_check_clean_returns_zero(tmp_path: Path) -> None:
    root = _write_lib(
        tmp_path, "**Supported Python versions: 3.10, 3.11**\n", ["3.10", "3.11"]
    )
    assert main(["--check", str(root)]) == 0


def test_check_drift_returns_one_and_diffs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _write_lib(
        tmp_path,
        "**Supported Python versions: 3.10, 3.11, 3.12, 3.13**\n",
        ["3.11", "3.12", "3.13"],
    )
    assert main(["--check", str(root)]) == 1
    err = capsys.readouterr().err
    assert "out of sync" in err
    assert "3.10" in err  # the removed version appears in the diff
    # --check must not modify the file.
    assert "3.10" in (root / "README.rst").read_text()


def test_no_marker_warns_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _write_lib(tmp_path, "No version line here.\n", ["3.10", "3.11"])
    assert main([str(root)]) == 0
    assert "no supported-Python marker" in capsys.readouterr().err


def test_require_line_flag_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _write_lib(tmp_path, "No version line here.\n", ["3.10", "3.11"])
    assert main(["--require-line", str(root)]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_console_script_entry_point_registered() -> None:
    scripts = importlib.metadata.entry_points(group="console_scripts")
    assert any(ep.name == "update-readme" for ep in scripts)
