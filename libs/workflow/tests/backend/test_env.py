"""Tests for environment resolution (``backend.env``)."""

import json
import os
import re
import warnings
from pathlib import Path

import pytest

from vivarium.workflow.backend.env import resolve_env_bin_path, resolve_env_prefix


def _make_prefix(prefix: Path) -> Path:
    """Create an env prefix holding an empty ``bin/python`` and return it."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").touch()
    return prefix


def _fake_conda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *prefixes: Path) -> None:
    """Point ``CONDA_EXE`` at a script that lists ``prefixes`` as conda envs."""
    listing = tmp_path / "conda_envs.json"
    listing.write_text(json.dumps({"envs": [str(prefix) for prefix in prefixes]}))
    conda = tmp_path / "fake_conda"
    conda.write_text(f"#!/bin/sh\ncat '{listing}'\n")
    conda.chmod(0o755)
    monkeypatch.setenv("CONDA_EXE", str(conda))


def test_a_prefix_path_resolves_to_its_resolved_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path to a directory containing bin/python resolves to that directory, symlinks followed."""
    real = _make_prefix(tmp_path / "real")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    for env in (str(tmp_path / "link"), "./link", "~/link"):
        assert resolve_env_prefix(env) == str(real.resolve())


def test_a_path_without_bin_python_is_rejected(tmp_path: Path) -> None:
    """A path to a directory without bin/python raises RuntimeError naming the path."""
    prefix = tmp_path / "env"
    (prefix / "bin").mkdir(parents=True)

    with pytest.raises(RuntimeError, match=re.escape(str(prefix))):
        resolve_env_prefix(str(prefix))


def test_a_name_resolves_to_a_local_venv_without_calling_conda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name matching .venv/<name> under the working directory resolves there with CONDA_EXE unset."""
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.chdir(tmp_path)
    venv = _make_prefix(tmp_path / ".venv" / "analysis")
    real = _make_prefix(tmp_path / "elsewhere" / "real_env")
    (tmp_path / ".venv" / "linked").symlink_to(real, target_is_directory=True)

    assert resolve_env_prefix("analysis") == str(venv.resolve())
    assert resolve_env_prefix("linked") == str(real.resolve())


def test_a_name_resolves_through_conda_env_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that is not a local venv resolves to the conda env whose prefix has that basename."""
    monkeypatch.chdir(tmp_path)
    other = _make_prefix(tmp_path / "conda_envs" / "other")
    analysis = _make_prefix(tmp_path / "conda_envs" / "analysis")
    _fake_conda(tmp_path, monkeypatch, other, analysis)

    assert resolve_env_prefix("analysis") == str(analysis.resolve())


def test_an_unknown_name_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name that matches no venv and no conda env raises RuntimeError naming the env."""
    monkeypatch.chdir(tmp_path)
    _fake_conda(tmp_path, monkeypatch, _make_prefix(tmp_path / "conda_envs" / "other"))

    with pytest.raises(RuntimeError, match="no_such_env") as info:
        resolve_env_prefix("no_such_env")

    assert "listed" in str(info.value)


@pytest.mark.parametrize("listed", ["other_env", "venv_itself", "link_to_venv"])
def test_an_ambiguous_name_warns_and_prefers_the_local_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listed: str
) -> None:
    """A name that is both a local venv and a different conda env resolves to the venv and warns naming both; matches that resolve to the same directory do not warn."""
    monkeypatch.chdir(tmp_path)
    venv = _make_prefix(tmp_path / ".venv" / "analysis")
    conda_env = tmp_path / "conda_envs" / "analysis"
    if listed == "other_env":
        _make_prefix(conda_env)
    elif listed == "venv_itself":
        conda_env = venv
    else:
        conda_env.parent.mkdir()
        conda_env.symlink_to(venv, target_is_directory=True)
    _fake_conda(tmp_path, monkeypatch, conda_env)

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        prefix = resolve_env_prefix("analysis")

    assert prefix == str(venv.resolve())
    warned = [str(w.message) for w in record if issubclass(w.category, UserWarning)]
    message = "\n".join(warned)
    if listed == "other_env":
        assert str(venv.resolve()) in message
        assert str(conda_env.resolve()) in message
    else:
        assert warned == []


def test_bin_path_is_the_prefix_bin_for_a_conda_env(tmp_path: Path) -> None:
    """For a prefix with no pyvenv.cfg the bin path is <prefix>/bin."""
    prefix = _make_prefix(tmp_path / "env")

    assert resolve_env_bin_path(str(prefix)) == str(prefix / "bin")


def test_bin_path_appends_the_base_bin_for_a_venv(tmp_path: Path) -> None:
    """For a venv prefix the bin path is <prefix>/bin followed by the pyvenv.cfg home directory."""
    base_bin = _make_prefix(tmp_path / "base") / "bin"
    venv = _make_prefix(tmp_path / "venv")
    config = f"home = {base_bin}\ninclude-system-site-packages = false\n"
    (venv / "pyvenv.cfg").write_text(config)

    assert resolve_env_bin_path(str(venv)) == f"{venv / 'bin'}:{base_bin}"


@pytest.mark.parametrize(
    "script, cause",
    [
        (None, "could not be run"),
        ("#!/bin/sh\nexit 1\n", "status"),
        ("#!/bin/sh\necho 'not json'\n", "JSON"),
    ],
    ids=["missing", "nonzero_exit", "not_json"],
)
def test_a_failing_conda_lookup_raises_a_runtime_error_naming_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str | None, cause: str
) -> None:
    """A CONDA_EXE that is missing, exits non-zero, or prints non-JSON makes the name lookup raise RuntimeError naming the env."""
    monkeypatch.chdir(tmp_path)
    conda = tmp_path / "fake_conda"
    if script is not None:
        conda.write_text(script)
        conda.chmod(0o755)
    monkeypatch.setenv("CONDA_EXE", str(conda))

    with pytest.raises(RuntimeError) as info:
        resolve_env_prefix("no_such_env")

    assert "no_such_env" in str(info.value)
    assert cause in str(info.value)


def test_an_unknown_name_says_when_conda_was_not_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With CONDA_EXE unset, the error for an unknown name says conda was not searched because CONDA_EXE is not set."""
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError) as info:
        resolve_env_prefix("no_such_env")

    assert "no_such_env" in str(info.value)
    assert "CONDA_EXE" in str(info.value)


@pytest.mark.parametrize("case", ["unknown_user", "unsearchable_parent"])
def test_a_path_that_cannot_be_checked_raises_a_runtime_error_naming_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A path env under an unknown ~user, or under a parent directory that cannot be searched, raises RuntimeError naming the env."""
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.chdir(tmp_path)
    locked = tmp_path / "locked"
    if case == "unknown_user":
        env = "~no_such_user_xyz_vw/env"
    elif os.geteuid() == 0:
        pytest.skip("root can search a directory without permission")
    else:
        env = str(_make_prefix(locked / "env"))
        locked.chmod(0o000)

    try:
        with pytest.raises(RuntimeError, match=re.escape(repr(env))):
            resolve_env_prefix(env)
    finally:
        if case == "unsearchable_parent":
            locked.chmod(0o755)
