"""Tests for recording and checking the environment of a psimulate run."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vivarium.cluster_tools.psimulate import pip_env


def test_list_installed_packages_uses_running_interpreter() -> None:
    """Packages come from the running interpreter, as ``<name>==<version>`` lines."""
    packages = pip_env._list_installed_packages()
    assert f"pytest=={importlib.metadata.version('pytest')}" in packages
    names = [line.split("==")[0] for line in packages]
    assert len(names) == len({pip_env._normalize_name(name) for name in names})


def test_validate_needs_no_pip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A venv with no ``pip`` on ``PATH`` still records its packages."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    environment_file = tmp_path / "requirements.txt"
    pip_env.validate(environment_file)
    assert environment_file.read_text().splitlines() == pip_env._list_installed_packages()


def test_validate_matching_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A restart in the same environment passes without asking to proceed."""
    confirm = MagicMock()
    monkeypatch.setattr(pip_env.click, "confirm", confirm)
    environment_file = tmp_path / "requirements.txt"
    pip_env.validate(environment_file)
    pip_env.validate(environment_file)
    confirm.assert_not_called()


def test_validate_reads_file_written_by_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file from an older run, with pip's spelling of names, matches by normalized name."""
    monkeypatch.setattr(
        pip_env, "_list_installed_packages", lambda: ["vivarium_cluster_tools==4.8.0"]
    )
    confirm = MagicMock()
    monkeypatch.setattr(pip_env.click, "confirm", confirm)
    environment_file = tmp_path / "requirements.txt"
    environment_file.write_text("Vivarium-Cluster.Tools==4.8.0\n")
    pip_env.validate(environment_file)
    confirm.assert_not_called()


def test_validate_changed_version_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        pip_env, "_list_installed_packages", lambda: ["vivarium-cluster-tools==4.9.0"]
    )
    confirm = MagicMock()
    monkeypatch.setattr(pip_env.click, "confirm", confirm)
    environment_file = tmp_path / "requirements.txt"
    environment_file.write_text("vivarium-cluster-tools==4.8.0\n")
    pip_env.validate(environment_file)
    confirm.assert_called_once()
