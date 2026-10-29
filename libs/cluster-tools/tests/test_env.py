"""Unit tests for conda/venv environment resolution."""

from __future__ import annotations

import json
import os
import re
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from vivarium.cluster_tools.core.jobmon import env as env_module
from vivarium.cluster_tools.core.jobmon.env import resolve_env_bin_path, resolve_env_prefix


@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host machine's active conda env / venv."""
    for var in ("CONDA_DEFAULT_ENV", "CONDA_PREFIX", "CONDA_EXE", "VIRTUAL_ENV"):
        monkeypatch.delenv(var, raising=False)


def _resolve_recording_warnings(env: str) -> tuple[str, list[str]]:
    """Resolve *env*; return the prefix and the messages of any ``UserWarning``."""
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        prefix = resolve_env_prefix(env)
    return prefix, [str(w.message) for w in record if issubclass(w.category, UserWarning)]


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
        ``CONDA_EXE``) resolves a venv under ``.venv/`` by name, following a
        symlinked venv to its target."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_model_simulation")
        real = _make_env_prefix(tmp_path / "elsewhere" / "real_env")
        (tmp_path / ".venv" / "linked").symlink_to(real, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        assert resolve_env_prefix("my_model_simulation") == str(venv.resolve())
        assert resolve_env_prefix("linked") == str(real.resolve())

    def test_explicit_path_that_is_not_an_env_raises(self, tmp_path: Path) -> None:
        not_an_env = tmp_path / "not_an_env"
        (not_an_env / "bin").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="is not a conda env or venv") as info:
            resolve_env_prefix(str(not_an_env))
        assert repr(str(not_an_env)) in str(info.value)

    @pytest.mark.parametrize("conda_exe", [None, ""], ids=["unset", "empty"])
    def test_unresolvable_name_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conda_exe: str | None
    ) -> None:
        """With ``CONDA_EXE`` unset or empty, the error says conda was not searched."""
        if conda_exe is not None:
            monkeypatch.setenv("CONDA_EXE", conda_exe)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError) as info:
            resolve_env_prefix("ghost")
        assert "Could not resolve environment 'ghost'" in str(info.value)
        assert "conda was not searched because CONDA_EXE is not set" in str(info.value)

    def test_name_missing_from_conda_env_list_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _patch_conda_env_list(monkeypatch, ["/opt/conda/envs/other"])
        with pytest.raises(RuntimeError) as info:
            resolve_env_prefix("ghost")
        assert "Could not resolve environment 'ghost'" in str(info.value)
        assert "no conda env of that name is listed" in str(info.value)

    def test_prefixes_are_normalized(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The same env reached through a symlink, a relative path, or ``~``
        resolves to one canonical prefix - Jobmon task hashes depend on the
        string being stable."""
        venv = _make_env_prefix(tmp_path / "real" / "my_env")
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path / "real")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        for env in (str(alias / "my_env"), "./alias/my_env", "~/alias/my_env"):
            assert resolve_env_prefix(env) == str(venv.resolve())


class TestResolveEnvPrefixFailures:
    """Every failure to resolve raises a ``RuntimeError`` that names the env."""

    @pytest.mark.parametrize(
        "script, cause",
        [
            (None, "could not be run"),
            ("#!/bin/sh\necho 'conda is broken' >&2\nexit 1\n", "status 1: conda is broken"),
            ("#!/bin/sh\necho 'not json'\n", "JSONDecodeError"),
            ("#!/bin/sh\necho '{}'\n", "KeyError"),
            ("#!/bin/sh\necho '{\"envs\": null}'\n", "TypeError"),
            ("#!/bin/sh\necho '[]'\n", "TypeError"),
        ],
        ids=[
            "missing",
            "nonzero_exit",
            "not_json",
            "no_envs_list",
            "null_envs",
            "not_an_object",
        ],
    )
    def test_failing_conda_lookup_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        script: str | None,
        cause: str,
    ) -> None:
        conda = tmp_path / "fake_conda"
        if script is not None:
            conda.write_text(script)
            conda.chmod(0o755)
        monkeypatch.setenv("CONDA_EXE", str(conda))
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError) as info:
            resolve_env_prefix("ghost")
        assert "Could not resolve environment 'ghost'" in str(info.value)
        assert cause in str(info.value)

    @pytest.mark.parametrize("case", ["unknown_user", "unsearchable_parent", "symlink_loop"])
    def test_path_that_cannot_be_checked_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
    ) -> None:
        """An unknown ``~user``, an unsearchable parent, or a symlink loop
        raises naming the env as given."""
        monkeypatch.chdir(tmp_path)
        locked = tmp_path / "locked"
        if case == "unknown_user":
            env = "~no_such_user_xyz_vw/env"
        elif case == "symlink_loop":
            (tmp_path / "loop").symlink_to("loop")
            env = str(tmp_path / "loop")
        elif os.geteuid() == 0:
            pytest.skip("root can search a directory without permission")
        else:
            env = str(_make_env_prefix(locked / "env"))
            locked.chmod(0o000)
        try:
            with pytest.raises(RuntimeError, match=re.escape(repr(env))):
                resolve_env_prefix(env)
        finally:
            if case == "unsearchable_parent":
                locked.chmod(0o755)


class TestResolveEnvPrefixAmbiguity:
    """A name matching several environments picks by precedence and warns."""

    def test_local_venv_wins_over_conda_env_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The local overlay is preferred over a same-named conda env - the
        default names from ``make build-env`` and ``make build-shared-env``
        collide, and the overlay is the recommended setup."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        _patch_conda_env_list(monkeypatch, ["/opt/conda/envs/my_env"])
        prefix, messages = _resolve_recording_warnings("my_env")
        assert prefix == str(venv.resolve())
        assert len(messages) == 1
        message = messages[0]
        assert "ambiguous" in message
        assert str(venv.resolve()) in message
        assert "/opt/conda/envs/my_env" in message

    @pytest.mark.parametrize("listed", ["venv_itself", "link_to_venv"])
    def test_matches_in_the_same_directory_do_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, listed: str
    ) -> None:
        """A conda listing of the local venv itself, or of a symlink to it,
        is one environment, not two."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        conda_env = venv
        if listed == "link_to_venv":
            conda_env = tmp_path / "conda_envs" / "my_env"
            conda_env.parent.mkdir()
            conda_env.symlink_to(venv, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        _patch_conda_env_list(monkeypatch, [str(conda_env)])
        assert _resolve_recording_warnings("my_env") == (str(venv.resolve()), [])

    def test_active_environments_are_ignored_for_named_lookup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A name resolves the same way regardless of shell activation state:
        an active env matching the name neither wins nor counts as a match."""
        venv = _make_env_prefix(tmp_path / ".venv" / "my_env")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere/my_env")
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "my_env")
        monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/my_env")
        assert _resolve_recording_warnings("my_env") == (str(venv.resolve()), [])


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

    @pytest.mark.parametrize(
        "config",
        ["version = 3.11.0\n", "home =\nversion = 3.11.0\n"],
        ids=["no_home", "empty_home"],
    )
    def test_venv_without_home_uses_its_bin(self, tmp_path: Path, config: str) -> None:
        """A missing or empty ``home`` key adds no PATH entry."""
        venv = _make_env_prefix(tmp_path / "venv")
        (venv / "pyvenv.cfg").write_text(config)
        assert resolve_env_bin_path(str(venv)) == f"{venv}/bin"
