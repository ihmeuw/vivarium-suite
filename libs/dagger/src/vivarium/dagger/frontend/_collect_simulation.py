"""Check a finished psimulate run and move it to a simulation step's run directory.

A simulation step's command runs this module's source as a script, with
``python -I -c <source> <scratch> <destination>``, on the declared environment's own
Python. That environment is a model's environment, not the pipeline's, so this module
imports only the standard library and uses no syntax or standard-library API newer than
Python 3.9. :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` reads the
source when a step is defined. Editing this file therefore changes every simulation
step's command, which makes those steps stale.

psimulate writes one run to ``<scratch>/<model name>/<timestamp>/``. The run is complete
when every simulation that psimulate recorded under ``metadata/`` wrote its file under
every metric directory in ``results/``. A complete run is moved to ``destination`` and a
sentinel file is written into it last, so the destination's sentinel exists only for a
complete run. Any failure writes the reason to a message file in ``scratch``. It
also leaves ``destination`` as it was and keeps the run under ``scratch`` for
inspection, unless undoing a failed move also fails, in which case the message says
where each run is.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

_SENTINEL = "_SUCCESS"
"""The file written last into a complete run directory; its content is the node's state."""

_MESSAGE_FILE = "_INCOMPLETE"
"""The file in the scratch directory that holds why the run was not collected."""

_PREVIOUS_SUFFIX = ".previous"
"""Appended to the destination's name while a new run replaces it."""


class CollectFailed(Exception):
    """The run under the scratch directory could not be collected.

    The message says why, in one paragraph that names the paths involved and says
    whether the previous complete run at the destination was kept or there was none.
    """


def has_complete_run(destination: Path) -> bool:
    """Return whether ``destination`` holds a complete run, that is, its sentinel file.

    A destination directory without the sentinel, as a crash between the move and the
    sentinel write leaves it, does not count as a complete run.
    """
    return (destination / _SENTINEL).is_file()


def previous_run_sentence(destination: Path, had_previous: bool) -> str:
    """Return the sentence that says whether a previous complete run was kept.

    Parameters
    ----------
    destination
        The run directory.
    had_previous
        Whether :func:`has_complete_run` was true for ``destination`` before anything
        was changed.

    Returns
    -------
        ``"The previous complete run at <destination> was kept."`` when
        ``had_previous`` is true, and ``"There was no previous run."`` otherwise.
    """
    if had_previous:
        return f"The previous complete run at {destination} was kept."
    return "There was no previous run."


def collect_run(scratch: Path, destination: Path) -> None:
    """Move the one complete psimulate run under ``scratch`` to ``destination``.

    Whether a previous complete run exists is read from ``destination`` before anything
    is changed, by :func:`has_complete_run`. The checks, in order:

    - ``scratch`` holds exactly one run directory, ``<scratch>/<model name>/<timestamp>/``.
    - The run holds at least one ``metadata/<task id>.json``, one per simulation.
    - Every simulation is finished: each subdirectory of ``results/`` (one per metric)
      holds ``<task id>.parquet`` for every simulation. A run without any metric
      directory has no finished simulation.

    When they pass, a leftover ``<destination>.previous`` is removed, an existing
    ``destination`` is renamed to ``<destination>.previous`` (the first rename), the run
    is renamed to ``destination`` (the second rename), the sentinel is written into it,
    and then ``<destination>.previous`` and ``scratch`` are removed. Every rename is
    made by ``_rename`` and the sentinel is written by ``_write_sentinel``. The sentinel
    holds a JSON object with the keys ``run_id`` (a new uuid4 hex on every call, so the
    content differs on every run), ``psimulate_run`` (``"<model name>/<timestamp>"``),
    ``simulations`` (the number of simulations) and ``metrics`` (the metric directory
    names, sorted).

    On any failure the message is written to ``<scratch>/_INCOMPLETE``, creating
    ``scratch`` if it is missing, and :class:`CollectFailed` is raised with the same
    message. ``destination`` and its sentinel are then as they were, and the run is
    under ``scratch`` as psimulate left it, unless undoing a failed move also fails. In
    that case the new run can be left at ``destination`` and the previous run at
    ``<destination>.previous``, and the message says where each run is.

    Parameters
    ----------
    scratch
        The directory psimulate wrote its run under, given to it as ``-o``.
    destination
        The step's run directory. Its parent must exist.

    Raises
    ------
    CollectFailed
        If a check fails or the move fails. The messages are:

        - no run directory: ``psimulate wrote no run directory under <scratch>.``
          followed by the previous-run sentence;
        - two or more: ``psimulate wrote <N> run directories under <scratch>, expected
          one: <run>, <run>.`` naming each, followed by the previous-run sentence;
        - no metadata: ``psimulate wrote no simulation metadata under <run>/metadata;
          this step needs vivarium-psimulate, or vivarium-cluster-tools 3.0.0 or
          later.`` followed by the previous-run sentence;
        - unfinished simulations: ``psimulate finished <k> of <n> simulations; the
          partial run is at <run>.`` followed by the previous-run sentence;
        - an :class:`OSError` while moving: ``psimulate finished, but moving the run to
          <destination> failed: <error>; the run is at <run>.`` followed by the
          previous-run sentence. The move is first rolled back: a partly written
          sentinel is removed, the new run is renamed back to ``<run>``, and
          ``<destination>.previous`` is renamed back to ``destination``. If the
          rollback also fails, the message says where each run now is instead of the
          previous-run sentence.

        The previous-run sentence is :func:`previous_run_sentence`'s.
    """
    previous = previous_run_sentence(destination, has_complete_run(destination))
    runs = _run_directories(scratch)
    if not runs:
        raise _failed(
            scratch, f"psimulate wrote no run directory under {scratch}. {previous}"
        )
    if len(runs) > 1:
        listed = ", ".join(str(run) for run in runs)
        raise _failed(
            scratch,
            f"psimulate wrote {len(runs)} run directories under {scratch}, expected one: "
            f"{listed}. {previous}",
        )
    run = runs[0]
    simulations = sorted(path.stem for path in (run / "metadata").glob("*.json"))
    if not simulations:
        raise _failed(
            scratch,
            f"psimulate wrote no simulation metadata under {run / 'metadata'}; this step "
            "needs vivarium-psimulate, or vivarium-cluster-tools 3.0.0 or later. "
            f"{previous}",
        )
    metrics = _metric_directories(run)
    finished = _count_finished(run, simulations, metrics)
    if finished < len(simulations):
        raise _failed(
            scratch,
            f"psimulate finished {finished} of {len(simulations)} simulations; the partial "
            f"run is at {run}. {previous}",
        )
    content = json.dumps(
        {
            "run_id": uuid.uuid4().hex,
            "psimulate_run": f"{run.parent.name}/{run.name}",
            "simulations": len(simulations),
            "metrics": metrics,
        }
    )
    aside = destination.with_name(destination.name + _PREVIOUS_SUFFIX)
    moved_aside = False
    moved_in = False
    try:
        if os.path.lexists(aside):
            _remove(aside)
        if os.path.lexists(destination):
            _rename(destination, aside)
            moved_aside = True
        _rename(run, destination)
        moved_in = True
        _write_sentinel(destination / _SENTINEL, content)
    except OSError as error:
        location, rollback = _roll_back(run, destination, aside, moved_aside, moved_in)
        raise _failed(
            scratch,
            f"psimulate finished, but moving the run to {destination} failed: {error}; "
            f"the run is at {location}. {previous if rollback is None else rollback}",
        ) from None
    # The new run is complete once its sentinel is written, so cleanup is best effort;
    # the step's next run removes anything left here.
    shutil.rmtree(aside, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Collect the run named by the command line and return the process's exit status.

    Parameters
    ----------
    argv
        The scratch directory and the destination, in that order. Defaults to
        ``sys.argv[1:]``.

    Returns
    -------
        0 when :func:`collect_run` succeeds. 1 when it raises :class:`CollectFailed`,
        after the message is printed to standard error. 2, after a usage line on
        standard error, when ``argv`` does not hold exactly two arguments.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2:
        print("usage: python -I -c <source> <scratch> <destination>", file=sys.stderr)
        return 2
    try:
        collect_run(Path(args[0]), Path(args[1]))
    except CollectFailed as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _rename(source: Path, target: Path) -> None:
    """Rename ``source`` to ``target`` with :func:`os.rename`."""
    os.rename(source, target)


def _write_sentinel(path: Path, content: str) -> None:
    """Write ``content`` to the sentinel file at ``path``."""
    path.write_text(content, encoding="utf-8")


def _failed(scratch: Path, message: str) -> CollectFailed:
    """Write ``message`` to the scratch directory's message file and return the error."""
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / _MESSAGE_FILE).write_text(message, encoding="utf-8")
    except OSError:
        # main still prints the message to standard error, where pytask's report shows it.
        pass
    return CollectFailed(message)


def _run_directories(scratch: Path) -> list[Path]:
    """Return the ``<model name>/<timestamp>`` directories under ``scratch``, sorted."""
    if not scratch.is_dir():
        return []
    return sorted(
        run
        for model in scratch.iterdir()
        if model.is_dir()
        for run in model.iterdir()
        if run.is_dir()
    )


def _metric_directories(run: Path) -> list[str]:
    """Return the sorted names of the metric directories under ``run``'s ``results``."""
    results = run / "results"
    if not results.is_dir():
        return []
    return sorted(path.name for path in results.iterdir() if path.is_dir())


def _count_finished(run: Path, simulations: list[str], metrics: list[str]) -> int:
    """Return how many simulations wrote their parquet file under every metric directory."""
    if not metrics:
        return 0
    results = run / "results"
    return sum(
        all((results / metric / f"{task_id}.parquet").is_file() for metric in metrics)
        for task_id in simulations
    )


def _remove(path: Path) -> None:
    """Remove the file, symlink or directory tree at ``path``."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _roll_back(
    run: Path, destination: Path, aside: Path, moved_aside: bool, moved_in: bool
) -> tuple[Path, str | None]:
    """Undo a failed move; return where the new run is and, if undoing failed, why."""
    if moved_in:
        try:
            sentinel = destination / _SENTINEL
            if os.path.lexists(sentinel):
                sentinel.unlink()
            _rename(destination, run)
        except OSError as error:
            kept = f" What was at {destination} before is at {aside}." if moved_aside else ""
            return destination, f"Undoing the move also failed: {error}.{kept}"
    if moved_aside:
        try:
            _rename(aside, destination)
        except OSError as error:
            return (
                run,
                f"Undoing the move also failed: {error}. What was at {destination} before "
                f"is at {aside}.",
            )
    return run, None


if __name__ == "__main__":
    sys.exit(main())
