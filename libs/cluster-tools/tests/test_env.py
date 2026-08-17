"""Unit tests for conda/venv environment resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("jobmon")

from vivarium.cluster_tools.core.jobmon import env as env_module
from vivarium.cluster_tools.core.jobmon.env import resolve_env_bin_path, resolve_env_prefix


@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host machine's active conda env / venv."""
    for var in ("CONDA_DEFAULT_ENV", "CONDA_PREFIX", "CONDA_EXE", "VIRTUAL_ENV"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def warning(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Capture ambiguity warnings emitted by the env module."""
    mock = MagicMock()
    monkeypatch.setattr(env_module.logger, "warning", mock)
    return mock


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
    """Verify each lookup mechanism and their precedence in ``resolve_env_prefix``."""

    def test_conda_env_list_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_conda_env_list(
            monkeypatch, ["/opt/conda/envs/other", "/opt/conda/envs/my_env"]
        )
        assert resolve_env_prefix("my_env") == "/opt/conda/envs/my_env"

    def test_local_venv_lookup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A shell with no conda at all (the autouse fixture clears
        ``CONDA_EXE``) resolves a venv under ``.venv/`` by name."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_model_simulation")
        monkeypatch.chdir(tmp_path)
        assert resolve_env_prefix("my_model_simulation") == str(venv.resolve())

    def test_explicit_path_that_is_not_an_env_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="is not a conda env or venv"):
            resolve_env_prefix(str(tmp_path / "not_an_env"))

    def test_unresolvable_name_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="Could not resolve environment 'ghost'"):
            resolve_env_prefix("ghost")

    def test_prefixes_are_normalized(self, tmp_path: Path) -> None:
        """The same env reached through a symlink resolves to one canonical
        prefix - Jobmon task hashes depend on the string being stable."""
        venv = _make_env_prefix(tmp_path / "real" / "my_env")
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path / "real")
        assert resolve_env_prefix(str(alias / "my_env")) == str(venv.resolve())


class TestResolveEnvPrefixAmbiguity:
    """A name matching several environments picks by precedence and warns."""

    def test_local_venv_wins_over_conda_env_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warning: MagicMock
    ) -> None:
        """The local overlay is preferred over a same-named conda env - the
        default names from ``make build-env`` and ``make build-shared-env``
        collide, and the overlay is the recommended setup."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        _patch_conda_env_list(monkeypatch, ["/opt/conda/envs/my_env"])
        assert resolve_env_prefix("my_env") == str(venv.resolve())
        warning.assert_called_once()
        assert "ambiguous" in warning.call_args.args[0]

    def test_active_environments_are_ignored_for_named_lookup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warning: MagicMock
    ) -> None:
        """A name resolves the same way regardless of shell activation state:
        an active env matching the name neither wins nor counts as a match."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere/my_env")
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "my_env")
        monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/my_env")
        assert resolve_env_prefix("my_env") == str(venv.resolve())
        warning.assert_not_called()


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
