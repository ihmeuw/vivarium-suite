"""Unit tests for conda/venv environment resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vivarium.cluster_tools.core.jobmon import env as env_module
from vivarium.cluster_tools.core.jobmon.env import resolve_env_bin_path, resolve_env_prefix


@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host machine's active conda env / venv."""
    for var in ("CONDA_DEFAULT_ENV", "CONDA_PREFIX", "CONDA_EXE", "VIRTUAL_ENV"):
        monkeypatch.delenv(var, raising=False)


def _make_env_prefix(prefix: Path, base_bin_dir: str | None = None) -> Path:
    """Fabricate an env prefix; with ``base_bin_dir``, make it a venv overlay."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").touch()
    if base_bin_dir is not None:
        (prefix / "pyvenv.cfg").write_text(
            f"home = {base_bin_dir}\nversion = 3.11.0\ninclude-system-site-packages = true\n"
        )
    return prefix


def _patch_conda_env_list(monkeypatch: pytest.MonkeyPatch, envs: list[str]) -> None:
    """Give the test a ``CONDA_EXE`` whose ``env list --json`` returns *envs*."""
    monkeypatch.setenv("CONDA_EXE", "/opt/conda/bin/conda")
    monkeypatch.setattr(
        env_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"envs": envs})),
    )


class TestResolveEnvPrefix:
    """Verify each lookup mechanism and their order in ``resolve_env_prefix``."""

    def test_active_conda_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "my_env")
        monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/my_env")
        assert resolve_env_prefix("my_env") == "/opt/conda/envs/my_env"

    def test_active_venv_matched_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/repo/.venv/my_model_simulation")
        assert resolve_env_prefix("my_model_simulation") == "/repo/.venv/my_model_simulation"

    def test_conda_env_list_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_conda_env_list(
            monkeypatch, ["/opt/conda/envs/other", "/opt/conda/envs/my_env"]
        )
        assert resolve_env_prefix("my_env") == "/opt/conda/envs/my_env"

    def test_local_venv_lookup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        venv = _make_env_prefix(tmp_path / ".venv" / "my_model_simulation")
        monkeypatch.chdir(tmp_path)
        assert resolve_env_prefix("my_model_simulation") == str(venv.resolve())

    def test_conda_env_list_wins_over_local_venv_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """On a name collision the conda env wins (backward compatible), but
        the silent shadowing of the local venv is called out."""
        _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        _patch_conda_env_list(monkeypatch, ["/opt/conda/envs/my_env"])
        warning = MagicMock()
        monkeypatch.setattr(env_module.logger, "warning", warning)
        assert resolve_env_prefix("my_env") == "/opt/conda/envs/my_env"
        warning.assert_called_once()
        assert "matches both" in warning.call_args.args[0]

    def test_local_venv_found_without_conda_installed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A shell with no conda at all (no ``CONDA_EXE``) can still resolve venvs."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        assert resolve_env_prefix("my_env") == str(venv.resolve())

    def test_explicit_path(self, tmp_path: Path) -> None:
        venv = _make_env_prefix(tmp_path / "some_env")
        assert resolve_env_prefix(str(venv)) == str(venv.resolve())

    def test_explicit_path_that_is_not_an_env_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="is not a conda env or venv"):
            resolve_env_prefix(str(tmp_path / "not_an_env"))

    def test_unresolvable_name_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="Could not resolve environment 'ghost'"):
            resolve_env_prefix("ghost")


class TestResolveEnvBinPath:
    """Verify the PATH-prepend string built for conda envs vs venv overlays."""

    def test_conda_env_uses_its_bin(self, tmp_path: Path) -> None:
        prefix = _make_env_prefix(tmp_path / "conda_env")
        assert resolve_env_bin_path(str(prefix)) == f"{prefix}/bin"

    def test_venv_appends_base_env_bin(self, tmp_path: Path) -> None:
        """A venv overlay's PATH must include the base env's bin so console
        scripts installed only there (e.g. ``psimulate``) still resolve."""
        base_bin = "/shared_envs/my_model_simulation_current/bin"
        venv = _make_env_prefix(tmp_path / "venv", base_bin_dir=base_bin)
        assert resolve_env_bin_path(str(venv)) == f"{venv}/bin:{base_bin}"

    def test_venv_without_home_key_uses_its_bin(self, tmp_path: Path) -> None:
        venv = _make_env_prefix(tmp_path / "venv")
        (venv / "pyvenv.cfg").write_text("version = 3.11.0\n")
        assert resolve_env_bin_path(str(venv)) == f"{venv}/bin"
