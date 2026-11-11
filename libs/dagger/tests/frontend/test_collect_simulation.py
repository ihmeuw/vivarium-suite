"""Unit tests of the check that collects a psimulate run (``frontend._collect_simulation``),
called directly, without a subprocess."""

import ast
import inspect
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.frontend.simulations import snapshot
from vivarium.dagger.frontend import _collect_simulation
from vivarium.dagger.frontend._collect_simulation import (
    _MESSAGE_FILE,
    _PREVIOUS_SUFFIX,
    _SENTINEL,
    CollectFailed,
    collect_run,
    has_complete_run,
    main,
    previous_run_sentence,
)

_MODEL = "toy_model"
"""The model name directory psimulate writes its run under."""

_STAMP = "2025_01_01_00_00_00"
"""The timestamp directory of the run under the scratch directory."""

_LATER_STAMP = "2025_01_01_00_00_01"
"""The timestamp directory of a second run under the scratch directory."""

_NO_PREVIOUS = "There was no previous run."


@dataclass(frozen=True)
class _Paths:
    """The scratch directory, the run psimulate writes in it, and the step's destination."""

    scratch: Path
    destination: Path

    @property
    def run(self) -> Path:
        """Return the one run directory psimulate writes under the scratch directory."""
        return self.scratch / _MODEL / _STAMP

    @property
    def previous(self) -> Path:
        """Return where the destination is moved while a new run replaces it."""
        return self.destination.with_name(f"{self.destination.name}{_PREVIOUS_SUFFIX}")

    @property
    def message_file(self) -> Path:
        """Return the file in the scratch directory that says why the run failed."""
        return self.scratch / _MESSAGE_FILE

    def kept(self) -> str:
        """Return the sentence that says the previous complete run was kept."""
        return f"The previous complete run at {self.destination} was kept."


@pytest.fixture
def paths(tmp_path: Path) -> _Paths:
    """Return the paths of a step 'sims/toy' under ``tmp_path``, whose parent exists."""
    sims = tmp_path.resolve() / "sims"
    sims.mkdir()
    return _Paths(scratch=sims / "toy.psimulate", destination=sims / "toy")


def _task_ids(count: int) -> list[str]:
    """Return ``count`` task ids, in sorted order."""
    return [f"sim_{index:03d}" for index in range(count)]


def _write_run(
    run: Path,
    *,
    simulations: int = 4,
    finished: int | None = None,
    metrics: Sequence[str] = ("dead",),
    metadata: bool = True,
) -> Path:
    """Write a psimulate run of ``simulations``, the first ``finished`` with every metric."""
    done = simulations if finished is None else finished
    (run / "logs").mkdir(parents=True)
    (run / "logs" / "main.log").write_text("psimulate's own log\n")
    (run / "model_specification.yaml").write_text("components: {}\n")
    if metadata:
        (run / "metadata").mkdir()
    for metric in metrics:
        (run / "results" / metric).mkdir(parents=True)
    for index, task_id in enumerate(_task_ids(simulations)):
        if metadata:
            (run / "metadata" / f"{task_id}.json").write_text(json.dumps({"index": index}))
        if index < done:
            for metric in metrics:
                (run / "results" / metric / f"{task_id}.parquet").write_bytes(b"parquet")
    return run


def _write_previous(destination: Path, sentinel: bool = True) -> dict[str, bytes]:
    """Write an earlier run at ``destination``; return the bytes of its files."""
    (destination / "results" / "dead").mkdir(parents=True)
    (destination / "results" / "dead" / "old.parquet").write_bytes(b"old results")
    if sentinel:
        (destination / _SENTINEL).write_text('{"run_id": "earlier"}')
    return snapshot(destination)


def _failure(paths: _Paths) -> str:
    """Call collect_run on ``paths``, assert it raises CollectFailed, and return its message."""
    with pytest.raises(CollectFailed) as excinfo:
        collect_run(paths.scratch, paths.destination)
    return str(excinfo.value)


def _sentinel(destination: Path) -> dict[str, object]:
    """Return the JSON object in ``destination``'s sentinel."""
    content: dict[str, object] = json.loads((destination / _SENTINEL).read_text())
    return content


def _no_run(paths: _Paths) -> str:
    """Leave the scratch directory empty; return the expected start of the message."""
    paths.scratch.mkdir()
    return f"psimulate wrote no run directory under {paths.scratch}."


def _two_runs(paths: _Paths) -> str:
    """Write two complete runs; return the expected start of the message."""
    _write_run(paths.run)
    _write_run(paths.scratch / _MODEL / _LATER_STAMP)
    return f"psimulate wrote 2 run directories under {paths.scratch}, expected one: "


def _no_metadata(paths: _Paths) -> str:
    """Write a run without metadata; return the expected start of the message."""
    _write_run(paths.run, metadata=False)
    return f"psimulate wrote no simulation metadata under {paths.run / 'metadata'}"


def _one_unfinished(paths: _Paths) -> str:
    """Write a run with 3 of 4 simulations finished; return the message's expected start."""
    _write_run(paths.run, finished=3)
    return f"psimulate finished 3 of 4 simulations; the partial run is at {paths.run}."


def _no_metric_directories(paths: _Paths) -> str:
    """Write a run with metadata and no metric directory; return the message's start."""
    _write_run(paths.run, metrics=())
    return f"psimulate finished 0 of 4 simulations; the partial run is at {paths.run}."


_FAILED_CHECKS: dict[str, Callable[[_Paths], str]] = {
    "no-run-directory": _no_run,
    "two-run-directories": _two_runs,
    "no-metadata": _no_metadata,
    "one-unfinished": _one_unfinished,
    "no-metric-directories": _no_metric_directories,
}
"""Each failed check, as a function that arranges it and returns the message's start."""


# The module runs on a model env's Python


def test_the_module_parses_as_python_3_9_and_imports_only_the_standard_library() -> None:
    """The module's source parses with ast.parse(source, feature_version=(3, 9)), and the top-level name of every import in it is in sys.stdlib_module_names."""
    source = inspect.getsource(_collect_simulation)

    tree = ast.parse(source, feature_version=(3, 9))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module is not None, ast.dump(node)
            imported.add(node.module.partition(".")[0])
    assert imported
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names


# A complete run


def test_a_complete_run_is_moved_to_the_destination_and_the_scratch_is_removed(
    paths: _Paths,
) -> None:
    """With one complete run under scratch and no destination, collect_run moves the run's files to the destination, writes the sentinel, and removes the scratch directory."""
    written = snapshot(_write_run(paths.run))

    collect_run(paths.scratch, paths.destination)

    collected = snapshot(paths.destination)
    assert collected.pop(_SENTINEL)
    assert collected == written
    assert not paths.scratch.exists()
    assert not paths.previous.exists()


def test_the_sentinel_holds_the_run_id_the_psimulate_run_the_count_and_the_metrics(
    paths: _Paths,
) -> None:
    """The sentinel is a JSON object with run_id (32 hex digits), psimulate_run ('<model name>/<timestamp>'), simulations (the number of metadata files) and metrics (the sorted metric directory names)."""
    _write_run(paths.run, simulations=6, metrics=("ylls", "dead"))

    collect_run(paths.scratch, paths.destination)

    sentinel = _sentinel(paths.destination)
    assert set(sentinel) == {"run_id", "psimulate_run", "simulations", "metrics"}
    assert re.fullmatch(r"[0-9a-f]{32}", str(sentinel["run_id"])), sentinel
    assert sentinel["psimulate_run"] == f"{_MODEL}/{_STAMP}"
    assert sentinel["simulations"] == 6
    assert sentinel["metrics"] == ["dead", "ylls"]


def test_the_sentinel_content_differs_between_two_collections_of_equal_runs(
    paths: _Paths,
) -> None:
    """Collecting two runs with identical files, one after the other, gives sentinels with different content."""
    _write_run(paths.run)
    collect_run(paths.scratch, paths.destination)
    first = (paths.destination / _SENTINEL).read_bytes()

    _write_run(paths.run)
    collect_run(paths.scratch, paths.destination)

    assert (paths.destination / _SENTINEL).read_bytes() != first


def test_an_existing_destination_is_replaced_and_nothing_is_left_beside_it(
    paths: _Paths,
) -> None:
    """A complete run replaces an existing destination: a file only in the old destination is gone, and neither '<destination>.previous' nor the scratch directory remains."""
    _write_previous(paths.destination)
    written = snapshot(_write_run(paths.run))

    collect_run(paths.scratch, paths.destination)

    collected = snapshot(paths.destination)
    assert collected.pop(_SENTINEL) != b'{"run_id": "earlier"}'
    assert collected == written
    assert not (paths.destination / "results" / "dead" / "old.parquet").exists()
    assert not paths.previous.exists()
    assert not paths.scratch.exists()
    assert sorted(path.name for path in paths.destination.parent.iterdir()) == ["toy"]


def test_a_leftover_previous_directory_is_removed_before_the_move(paths: _Paths) -> None:
    """A '<destination>.previous' left by an earlier crash is removed, and the new run is collected."""
    _write_previous(paths.previous)
    _write_previous(paths.destination)
    written = snapshot(_write_run(paths.run))

    collect_run(paths.scratch, paths.destination)

    collected = snapshot(paths.destination)
    assert collected.pop(_SENTINEL)
    assert collected == written
    assert not paths.previous.exists()
    assert not paths.scratch.exists()


# Failed checks leave the destination as it was


def test_no_run_directory_fails_naming_the_scratch_and_leaves_the_destination_unchanged(
    paths: _Paths,
) -> None:
    """With nothing under scratch, collect_run raises CollectFailed saying psimulate wrote no run directory under the scratch path, and the destination and its sentinel are byte-identical to before."""
    before = _write_previous(paths.destination)
    start = _no_run(paths)

    message = _failure(paths)

    assert message.startswith(start), message
    assert snapshot(paths.destination) == before
    assert not paths.previous.exists()


def test_two_run_directories_fail_naming_both_and_leave_the_destination_unchanged(
    paths: _Paths,
) -> None:
    """With two run directories under scratch, collect_run raises CollectFailed naming both, leaves both in place, and the destination is byte-identical to before."""
    before = _write_previous(paths.destination)
    start = _two_runs(paths)
    later = paths.scratch / _MODEL / _LATER_STAMP
    written = (snapshot(paths.run), snapshot(later))

    message = _failure(paths)

    assert message.startswith(start), message
    assert str(paths.run) in message
    assert str(later) in message
    assert (snapshot(paths.run), snapshot(later)) == written
    assert snapshot(paths.destination) == before
    assert not paths.previous.exists()


def test_no_metadata_fails_naming_the_psimulate_requirement(paths: _Paths) -> None:
    """A run with no metadata/*.json raises CollectFailed saying psimulate wrote no simulation metadata and that the step needs vivarium-psimulate or vivarium-cluster-tools 3.0.0 or later."""
    before = _write_previous(paths.destination)
    start = _no_metadata(paths)

    message = _failure(paths)

    assert message.startswith(start), message
    assert "vivarium-psimulate" in message
    assert "vivarium-cluster-tools 3.0.0 or later" in message
    assert snapshot(paths.destination) == before


def test_an_unfinished_simulation_fails_with_the_counts_and_the_partial_run(
    paths: _Paths,
) -> None:
    """A run with 4 metadata files and 3 result files raises CollectFailed giving '3 of 4' and the partial run's path, and the partial run stays under scratch."""
    before = _write_previous(paths.destination)
    start = _one_unfinished(paths)
    written = snapshot(paths.run)

    message = _failure(paths)

    assert message.startswith(start), message
    assert snapshot(paths.run) == written
    assert snapshot(paths.destination) == before
    assert not paths.previous.exists()


def test_a_simulation_missing_from_one_metric_counts_as_unfinished(paths: _Paths) -> None:
    """With two metric directories, a simulation whose file is in one but not the other counts as unfinished, so 4 simulations with one gap give '3 of 4'."""
    before = _write_previous(paths.destination)
    _write_run(paths.run, metrics=("dead", "ylls"))
    (paths.run / "results" / "ylls" / "sim_003.parquet").unlink()

    message = _failure(paths)

    assert message.startswith(
        f"psimulate finished 3 of 4 simulations; the partial run is at {paths.run}."
    ), message
    assert snapshot(paths.destination) == before


def test_a_run_without_metric_directories_has_no_finished_simulation(paths: _Paths) -> None:
    """A run with metadata files but no results/ subdirectory raises CollectFailed giving '0 of <n>'."""
    before = _write_previous(paths.destination)
    start = _no_metric_directories(paths)

    message = _failure(paths)

    assert message.startswith(start), message
    assert snapshot(paths.destination) == before


@pytest.mark.parametrize("arrange", list(_FAILED_CHECKS.values()), ids=list(_FAILED_CHECKS))
def test_each_failed_check_writes_its_message_to_the_scratch_message_file(
    paths: _Paths, arrange: Callable[[_Paths], str]
) -> None:
    """For each failed check, the scratch directory's message file holds the same text as the CollectFailed message."""
    _write_previous(paths.destination)
    start = arrange(paths)

    message = _failure(paths)

    assert message.startswith(start), message
    assert paths.message_file.read_text().strip() == message.strip()


def test_a_missing_scratch_directory_is_created_for_the_message_file(paths: _Paths) -> None:
    """When the scratch directory does not exist, collect_run creates it, writes the message file there, and raises CollectFailed saying there is no run directory."""
    assert not paths.scratch.exists()

    message = _failure(paths)

    assert message.startswith(f"psimulate wrote no run directory under {paths.scratch}.")
    assert paths.message_file.read_text().strip() == message.strip()


# The previous-run rule


def test_a_failure_with_a_complete_previous_run_says_it_was_kept(paths: _Paths) -> None:
    """When the destination holds its sentinel, a failed check's message ends 'The previous complete run at <destination> was kept.'."""
    _write_previous(paths.destination)
    _one_unfinished(paths)

    message = _failure(paths)

    assert has_complete_run(paths.destination)
    assert previous_run_sentence(paths.destination, True) == paths.kept()
    assert message.rstrip().endswith(paths.kept()), message


def test_a_failure_without_a_destination_says_there_was_no_previous_run(
    paths: _Paths,
) -> None:
    """When the destination does not exist, a failed check's message ends 'There was no previous run.'."""
    _one_unfinished(paths)

    message = _failure(paths)

    assert not has_complete_run(paths.destination)
    assert previous_run_sentence(paths.destination, False) == _NO_PREVIOUS
    assert message.rstrip().endswith(_NO_PREVIOUS), message
    assert not paths.destination.exists()


def test_a_destination_without_a_sentinel_does_not_count_as_a_previous_run(
    paths: _Paths,
) -> None:
    """When the destination exists without its sentinel, has_complete_run is False and a failed check's message ends 'There was no previous run.'."""
    before = _write_previous(paths.destination, sentinel=False)
    _one_unfinished(paths)

    assert not has_complete_run(paths.destination)
    message = _failure(paths)

    assert message.rstrip().endswith(_NO_PREVIOUS), message
    assert "was kept" not in message
    assert snapshot(paths.destination) == before


# A failed move is rolled back


def test_a_failed_second_rename_puts_the_previous_run_back(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _rename raises OSError for the move of the run to the destination, CollectFailed names the destination, the error and the run, the previous run is back at the destination with its sentinel byte-identical, and the new run is at its path under scratch."""
    before = _write_previous(paths.destination)
    written = snapshot(_write_run(paths.run))
    rename = _collect_simulation._rename

    def failing_rename(source: Path, target: Path) -> None:
        """Refuse the move of the run to the destination; make every other rename."""
        if Path(source).resolve() == paths.run.resolve():
            raise OSError(28, "No space left on device (simulated)")
        rename(source, target)

    monkeypatch.setattr(_collect_simulation, "_rename", failing_rename)

    message = _failure(paths)

    assert f"moving the run to {paths.destination} failed" in message, message
    assert "No space left on device (simulated)" in message
    assert f"the run is at {paths.run}" in message
    assert message.rstrip().endswith(paths.kept()), message
    assert snapshot(paths.destination) == before
    assert snapshot(paths.run) == written
    assert not paths.previous.exists()
    assert paths.message_file.read_text().strip() == message.strip()


def test_a_failed_sentinel_write_puts_the_previous_run_back(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _write_sentinel writes part of the sentinel and raises OSError, CollectFailed is raised, the previous run is back at the destination with its sentinel byte-identical, the new run is at its path under scratch, and it holds no sentinel."""
    before = _write_previous(paths.destination)
    written = snapshot(_write_run(paths.run))
    attempts: list[Path] = []

    def partial_write(path: Path, content: str) -> None:
        """Write the first few characters of the sentinel, then fail as a full disk does."""
        attempts.append(Path(path))
        Path(path).write_text(content[:5])
        raise OSError(28, "No space left on device (simulated)")

    monkeypatch.setattr(_collect_simulation, "_write_sentinel", partial_write)

    message = _failure(paths)

    assert attempts, "the sentinel write was not attempted"
    assert f"moving the run to {paths.destination} failed" in message, message
    assert "No space left on device (simulated)" in message
    assert f"the run is at {paths.run}" in message
    assert message.rstrip().endswith(paths.kept()), message
    assert snapshot(paths.destination) == before
    assert snapshot(paths.run) == written
    assert not (paths.run / _SENTINEL).exists()
    assert not paths.previous.exists()


def _new_run_not_moved_back(paths: _Paths) -> tuple[tuple[Path, Path], Path]:
    """Return the undo rename that leaves the new run at the destination, and that path."""
    return (paths.destination, paths.run), paths.destination


def _previous_run_not_moved_back(paths: _Paths) -> tuple[tuple[Path, Path], Path]:
    """Return the undo rename that leaves the new run under scratch, and that path."""
    return (paths.previous, paths.destination), paths.run


_FAILED_UNDOS: dict[str, Callable[[_Paths], tuple[tuple[Path, Path], Path]]] = {
    "new-run-not-moved-back": _new_run_not_moved_back,
    "previous-run-not-moved-back": _previous_run_not_moved_back,
}
"""Each undo rename a test refuses, with the path where refusing it leaves the new run."""


@pytest.mark.parametrize("failed_undo", list(_FAILED_UNDOS.values()), ids=list(_FAILED_UNDOS))
def test_a_failed_undo_of_the_move_says_where_each_run_is(
    paths: _Paths,
    monkeypatch: pytest.MonkeyPatch,
    failed_undo: Callable[[_Paths], tuple[tuple[Path, Path], Path]],
) -> None:
    """When undoing a failed move also fails, the message says where the new run and the previous run are instead of the previous-run sentence."""
    before = _write_previous(paths.destination)
    written = snapshot(_write_run(paths.run))
    refused, new_run = failed_undo(paths)
    rename = _collect_simulation._rename

    def partial_write(path: Path, content: str) -> None:
        """Write the first few characters of the sentinel, then fail as a full disk does."""
        Path(path).write_text(content[:5])
        raise OSError(28, "No space left on device (simulated)")

    def refusing_rename(source: Path, target: Path) -> None:
        """Refuse the one undo rename; make every other rename."""
        if (Path(source).resolve(), Path(target).resolve()) == refused:
            raise OSError(16, "Device or resource busy (simulated)")
        rename(source, target)

    monkeypatch.setattr(_collect_simulation, "_write_sentinel", partial_write)
    monkeypatch.setattr(_collect_simulation, "_rename", refusing_rename)

    message = _failure(paths)

    assert f"moving the run to {paths.destination} failed" in message, message
    assert "No space left on device (simulated)" in message
    assert str(paths.previous) in message, message
    assert str(new_run) in message, message
    assert paths.kept() not in message, message
    assert _NO_PREVIOUS not in message, message
    assert snapshot(paths.previous) == before
    assert snapshot(new_run) == written
    assert {path for path in (paths.destination, paths.run) if path.exists()} == {new_run}
    assert paths.message_file.read_text().strip() == message.strip()


# The script entry point


def test_main_returns_zero_and_collects_the_run_named_on_the_command_line(
    paths: _Paths,
) -> None:
    """main([scratch, destination]) collects a complete run and returns 0."""
    _write_run(paths.run)

    status = main([str(paths.scratch), str(paths.destination)])

    assert status == 0
    assert (paths.destination / _SENTINEL).is_file()
    assert len(list((paths.destination / "metadata").glob("*.json"))) == 4
    assert not paths.scratch.exists()


def test_main_prints_the_message_to_stderr_and_returns_one_on_failure(
    paths: _Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    """main([scratch, destination]) with an unfinished run prints the CollectFailed message to stderr and returns 1."""
    start = _one_unfinished(paths)

    status = main([str(paths.scratch), str(paths.destination)])

    assert status == 1
    message = paths.message_file.read_text().strip()
    assert message.startswith(start), message
    assert message in capsys.readouterr().err
    assert not paths.destination.exists()


@pytest.mark.parametrize("count", [1, 3])
def test_main_prints_a_usage_line_and_returns_two_without_exactly_two_arguments(
    paths: _Paths, capsys: pytest.CaptureFixture[str], count: int
) -> None:
    """main with one or three arguments prints a usage line to stderr, returns 2 and changes nothing."""
    written = snapshot(_write_run(paths.run))
    arguments = [str(paths.scratch), str(paths.destination), "extra"][:count]

    status = main(arguments)

    assert status == 2
    assert "usage" in capsys.readouterr().err.lower()
    assert snapshot(paths.run) == written
    assert not paths.message_file.exists()
    assert not paths.destination.exists()
