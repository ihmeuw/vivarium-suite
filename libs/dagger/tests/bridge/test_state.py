"""Tests for a bridge run's state file (``bridge.state``)."""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from vivarium.dagger.bridge.state import (
    RUNS_DIR,
    STATE_FILE,
    RunState,
    latest_run_state,
    read_run_state,
    write_run_state,
)

_FIRST_NAME = "20261104-120000-aaaaaa"
"""The name of a run directory that sorts before :data:`_SECOND_NAME`."""

_SECOND_NAME = "20261104-120001-bbbbbb"
"""The name of a run directory that sorts after :data:`_FIRST_NAME`."""

_KEYS = {"status", "workflow_id", "workflow_status", "error", "pending"}
"""The keys of the JSON object in a state file."""


def _run_directory(root: Path, name: str) -> Path:
    """Create and return the run directory called ``name`` under ``root / RUNS_DIR``."""
    directory = root / RUNS_DIR / name
    directory.mkdir(parents=True)
    return directory


def _full_state(workdir: Path) -> RunState:
    """Return a finished state of ``workdir`` with every field set."""
    return RunState(
        workdir=workdir,
        status="finished",
        workflow_id=4242,
        workflow_status="E",
        error="RuntimeError: Jobmon stopped, on purpose.",
        pending=("b.parquet", "c.parquet"),
    )


def _bare_state(workdir: Path) -> RunState:
    """Return a running state of ``workdir`` with every optional field empty."""
    return RunState(
        workdir=workdir,
        status="running",
        workflow_id=None,
        workflow_status=None,
        error=None,
        pending=(),
    )


def _set_mtime(path: Path, seconds: float) -> None:
    """Set the access and modification times of ``path`` to ``seconds``."""
    os.utime(path, (seconds, seconds))


def test_a_written_state_reads_back_with_every_field(tmp_path: Path) -> None:
    """read_run_state of the file that write_run_state wrote returns a RunState equal to the one written, for a state with a workflow id, a status code, an error and two pending names, and for one whose workflow_id, workflow_status and error are None and whose pending is empty."""
    full = _full_state(_run_directory(tmp_path, _FIRST_NAME))
    bare = _bare_state(_run_directory(tmp_path, _SECOND_NAME))

    write_run_state(full)
    write_run_state(bare)

    assert read_run_state(full.workdir / STATE_FILE) == full
    assert read_run_state(bare.workdir / STATE_FILE) == bare


def test_the_state_file_is_a_json_object_with_the_documented_keys(tmp_path: Path) -> None:
    """write_run_state writes workdir / STATE_FILE as a JSON object whose keys are exactly status, workflow_id, workflow_status, error and pending, with pending as a list in the order given."""
    state = _full_state(_run_directory(tmp_path, _FIRST_NAME))

    write_run_state(state)

    written = json.loads((state.workdir / STATE_FILE).read_text(encoding="utf-8"))
    assert isinstance(written, dict), written
    assert set(written) == _KEYS
    assert written == {
        "status": "finished",
        "workflow_id": 4242,
        "workflow_status": "E",
        "error": "RuntimeError: Jobmon stopped, on purpose.",
        "pending": ["b.parquet", "c.parquet"],
    }


def test_writing_a_state_again_replaces_the_earlier_one(tmp_path: Path) -> None:
    """After write_run_state of a 'running' state and then of a 'finished' state to the same workdir, read_run_state returns the 'finished' state."""
    workdir = _run_directory(tmp_path, _FIRST_NAME)
    finished = _full_state(workdir)

    write_run_state(_bare_state(workdir))
    write_run_state(finished)

    assert read_run_state(workdir / STATE_FILE) == finished


def test_writing_a_state_leaves_no_temporary_file(tmp_path: Path) -> None:
    """After write_run_state, the workdir holds only STATE_FILE."""
    workdir = _run_directory(tmp_path, _FIRST_NAME)

    write_run_state(_bare_state(workdir))
    write_run_state(_full_state(workdir))

    assert os.listdir(workdir) == [STATE_FILE]


def test_a_state_is_replaced_whole_and_never_written_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_run_state moves a finished temporary file onto the state file with os.replace: when os.replace is made to raise, the earlier state file is unchanged and the temporary file is gone."""
    workdir = _run_directory(tmp_path, _FIRST_NAME)
    write_run_state(_bare_state(workdir))
    before = (workdir / STATE_FILE).read_bytes()
    moved: list[tuple[dict[str, Any], str]] = []

    def refuse(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        """Note what would have been moved where, then fail."""
        moved.append((json.loads(Path(source).read_text(encoding="utf-8")), str(target)))
        raise OSError("os.replace refused, on purpose")

    monkeypatch.setattr(os, "replace", refuse)
    with contextlib.suppress(OSError):
        write_run_state(_full_state(workdir))
    monkeypatch.undo()

    [(temporary, target)] = moved
    assert temporary["status"] == "finished"
    assert temporary["pending"] == ["b.parquet", "c.parquet"]
    assert Path(target) == workdir / STATE_FILE
    assert (workdir / STATE_FILE).read_bytes() == before
    assert os.listdir(workdir) == [STATE_FILE]


def test_read_run_state_takes_the_workdir_from_the_files_directory(tmp_path: Path) -> None:
    """After a run directory with a state file is moved to another path, read_run_state of the moved file returns a state whose workdir is the new directory."""
    workdir = _run_directory(tmp_path / "old_root", _FIRST_NAME)
    write_run_state(_full_state(workdir))
    moved = tmp_path / "new_root" / _FIRST_NAME
    moved.parent.mkdir()

    workdir.rename(moved)

    assert read_run_state(moved / STATE_FILE) == _full_state(moved)


@pytest.mark.parametrize(
    "how",
    [
        "not_json",
        "json_array",
        "missing_key",
        "unknown_status",
        "workflow_id_as_text",
        "workflow_id_as_bool",
        "workflow_status_as_number",
        "error_as_number",
        "pending_as_text",
        "pending_with_a_number",
    ],
)
def test_read_run_state_rejects_a_file_that_is_not_a_run_state(
    tmp_path: Path, how: str
) -> None:
    """read_run_state raises ValueError naming the file for text that is not JSON, a JSON array, an object without 'pending', a status that is not a RunStatus, a workflow_id that is a string or a boolean, a workflow_status or error that is a number, a pending that is a string, or a pending with an element that is not a string."""
    path = _run_directory(tmp_path, _FIRST_NAME) / STATE_FILE
    fields: dict[str, Any] = {
        "status": "finished",
        "workflow_id": 4242,
        "workflow_status": "E",
        "error": None,
        "pending": ["b.parquet", "c.parquet"],
    }
    spoiled: dict[str, str] = {
        "not_json": "{ this is not JSON",
        "json_array": "[]",
        "missing_key": json.dumps({key: fields[key] for key in fields if key != "pending"}),
        "unknown_status": json.dumps({**fields, "status": "paused"}),
        "workflow_id_as_text": json.dumps({**fields, "workflow_id": "4242"}),
        "workflow_id_as_bool": json.dumps({**fields, "workflow_id": True}),
        "workflow_status_as_number": json.dumps({**fields, "workflow_status": 1}),
        "error_as_number": json.dumps({**fields, "error": 1}),
        "pending_as_text": json.dumps({**fields, "pending": "b.parquet"}),
        "pending_with_a_number": json.dumps({**fields, "pending": ["b.parquet", 3]}),
    }
    path.write_text(spoiled[how], encoding="utf-8")

    with pytest.raises(ValueError) as info:
        read_run_state(path)

    assert str(path) in str(info.value), str(info.value)


def test_latest_run_state_is_none_without_a_runs_directory(tmp_path: Path) -> None:
    """latest_run_state of a root that has no .pytask/jobmon directory returns None."""
    assert not (tmp_path / RUNS_DIR).exists()

    assert latest_run_state(tmp_path) is None


def test_latest_run_state_is_none_when_no_run_has_a_state_file(tmp_path: Path) -> None:
    """latest_run_state returns None when root / RUNS_DIR holds run directories but none of them has a state file."""
    for name in (_FIRST_NAME, _SECOND_NAME):
        recipe = _run_directory(tmp_path, name) / "signature.payload.json"
        recipe.write_text("{}", encoding="utf-8")

    assert latest_run_state(tmp_path) is None


def test_latest_run_state_returns_the_state_written_last(tmp_path: Path) -> None:
    """With two run directories whose state files have different modification times, latest_run_state returns the state of the one modified last, also when its directory name sorts first."""
    first = _full_state(_run_directory(tmp_path, _FIRST_NAME))
    second = _bare_state(_run_directory(tmp_path, _SECOND_NAME))
    write_run_state(first)
    write_run_state(second)

    _set_mtime(second.workdir / STATE_FILE, 1_000_000_000)
    _set_mtime(first.workdir / STATE_FILE, 1_000_000_100)
    first_is_latest = latest_run_state(tmp_path)
    _set_mtime(second.workdir / STATE_FILE, 1_000_000_200)
    second_is_latest = latest_run_state(tmp_path)

    assert first_is_latest == first
    assert second_is_latest == second


def test_latest_run_state_skips_a_newer_run_directory_without_a_state_file(
    tmp_path: Path,
) -> None:
    """A run directory created after the latest state file, but holding no state file, does not change what latest_run_state returns."""
    state = _full_state(_run_directory(tmp_path, _FIRST_NAME))
    write_run_state(state)
    _set_mtime(state.workdir / STATE_FILE, 1_000_000_000)

    newer = _run_directory(tmp_path, _SECOND_NAME)
    recipe = newer / "signature.payload.json"
    recipe.write_text("{}", encoding="utf-8")
    _set_mtime(recipe, 1_000_000_100)
    _set_mtime(newer, 1_000_000_100)

    assert latest_run_state(tmp_path) == state


def test_latest_run_state_with_bound_only_passes_over_a_newer_state_without_a_workflow_id(
    tmp_path: Path,
) -> None:
    """With bound_only=True, latest_run_state passes over a newer state that has no workflow id and returns the newest state that has one; without bound_only it returns the newer state, and with only states that have no workflow id it returns None."""
    bound = _full_state(_run_directory(tmp_path, _FIRST_NAME))
    unbound = _bare_state(_run_directory(tmp_path, _SECOND_NAME))
    write_run_state(bound)
    write_run_state(unbound)
    _set_mtime(bound.workdir / STATE_FILE, 1_000_000_000)
    _set_mtime(unbound.workdir / STATE_FILE, 1_000_000_100)
    only_unbound = tmp_path / "only_unbound"
    write_run_state(_bare_state(_run_directory(only_unbound, _FIRST_NAME)))

    assert latest_run_state(tmp_path, bound_only=True) == bound
    assert latest_run_state(tmp_path) == unbound
    assert latest_run_state(only_unbound, bound_only=True) is None


def test_latest_run_state_raises_when_the_runs_directory_cannot_be_listed(
    tmp_path: Path,
) -> None:
    """latest_run_state raises PermissionError, and does not return None, when root / RUNS_DIR exists but cannot be listed (skipped when the tests run as root)."""
    if os.geteuid() == 0:
        pytest.skip("The root user can list a directory that has no read permission.")
    write_run_state(_full_state(_run_directory(tmp_path, _FIRST_NAME)))
    runs = tmp_path / RUNS_DIR
    runs.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            latest_run_state(tmp_path)
    finally:
        runs.chmod(0o700)


def test_latest_run_state_raises_when_the_latest_state_file_is_not_a_run_state(
    tmp_path: Path,
) -> None:
    """latest_run_state raises ValueError naming the file when the latest state file holds text that is not JSON."""
    older = _full_state(_run_directory(tmp_path, _FIRST_NAME))
    write_run_state(older)
    _set_mtime(older.workdir / STATE_FILE, 1_000_000_000)
    spoiled = _run_directory(tmp_path, _SECOND_NAME) / STATE_FILE
    spoiled.write_text("{ this is not JSON", encoding="utf-8")
    _set_mtime(spoiled, 1_000_000_100)

    with pytest.raises(ValueError) as info:
        latest_run_state(tmp_path)

    assert str(spoiled) in str(info.value), str(info.value)


def test_the_state_module_imports_only_the_standard_library() -> None:
    """In a fresh interpreter, importing vivarium.dagger.bridge.state leaves pytask, vivarium.dagger.bridge.execute and every jobmon module out of sys.modules."""
    code = (
        "import json, sys\n"
        "import vivarium.dagger.bridge.state\n"
        "names = ('pytask', '_pytask', 'jobmon', 'vivarium.dagger.bridge.execute')\n"
        "loaded = [\n"
        "    module for module in sys.modules\n"
        "    if any(module == name or module.startswith(name + '.') for name in names)\n"
        "]\n"
        "print(json.dumps(sorted(loaded)), end='')"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout) == []
