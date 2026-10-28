"""Validate a location's primary-data handoff and return it as root inputs.

A data analyst delivers a location's primary data as files under a handoff root, at
``<root>/<location>/primary/<file>``. A pipeline declares which files every location
must supply and the columns each file must have. :func:`handoff_inputs` checks every
declared file before anything runs, reading only column names, and returns the files
keyed for an :class:`~vivarium.workflow.backend.wiring.ArtifactRegistry`'s
``root_inputs``. A step reads a handoff file by naming the key that :func:`handoff_key`
returns as an input, and the step's function receives the file's path.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vivarium.workflow.backend.wiring import ArtifactKey, WiringError

PRIMARY_DIR = "primary"
"""Subdirectory of each location's directory that holds its primary data."""


class HandoffError(WiringError):
    """A declared handoff file is missing, unreadable, or lacks a required column."""


def handoff_key(location: str, filename: str) -> ArtifactKey:
    """Return the artifact key a step names to read ``filename`` for ``location``.

    The key is the file's path relative to the handoff root,
    ``<location>/primary/<filename>``.
    """
    return f"{location}/{PRIMARY_DIR}/{filename}"


def _csv_columns(path: Path) -> list[str]:
    """Read the column names from a CSV file's header row."""
    return [str(column) for column in pd.read_csv(path, nrows=0).columns]


def _parquet_columns(path: Path) -> list[str]:
    """Read the column names from a Parquet file's footer."""
    return list(pq.read_schema(path).names)


_COLUMN_READERS: dict[str, Callable[[Path], list[str]]] = {
    ".csv": _csv_columns,
    ".parquet": _parquet_columns,
}
"""Supported handoff suffixes mapped to a reader that loads only the column names."""


def _is_single_component(name: str) -> bool:
    """Return whether ``name`` is one path component other than ``.`` or ``..``."""
    return name not in {"", ".", ".."} and Path(name).name == name


def _check_declaration(locations: list[str], files: Mapping[str, Sequence[str]]) -> None:
    """Reject a bad location or file name, an unsupported suffix, or a string column list."""
    for location in locations:
        if not _is_single_component(location):
            raise ValueError(f"location {location!r} must be a single directory name.")
    for filename, columns in files.items():
        if not _is_single_component(filename):
            raise ValueError(f"handoff file {filename!r} must be a file name, not a path.")
        suffix = Path(filename).suffix
        if suffix not in _COLUMN_READERS:
            supported = ", ".join(sorted(_COLUMN_READERS))
            raise ValueError(
                f"handoff file {filename!r} has unsupported suffix {suffix!r}; "
                f"supported suffixes are {supported}."
            )
        if isinstance(columns, str):
            raise ValueError(
                f"required columns for {filename!r} must be a list of names, not the "
                f"string {columns!r}."
            )


def _same_stem_files(path: Path) -> list[str]:
    """Return the names of the files beside ``path`` that share its stem, ignoring case."""
    prefix = f"{path.stem}.".casefold()
    try:
        siblings = [p for p in path.parent.iterdir() if p.is_file()]
    except OSError:
        return []
    return sorted(p.name for p in siblings if p.name.casefold().startswith(prefix))


def _names(names: Iterable[str]) -> str:
    """Join names, quoted so surrounding spaces show."""
    return ", ".join(repr(name) for name in names)


def _unreadable(location: str, path: Path, error: Exception) -> str:
    """Describe a delivered file that could not be read."""
    return (
        f"{location}: {path.name} at {path} could not be read: "
        f"{type(error).__name__}: {error}"
    )


def _problem(location: str, path: Path, columns: Sequence[str]) -> str | None:
    """Describe what is wrong with one delivered file, or return None if it is valid."""
    try:
        is_file = path.is_file()
    except OSError as error:
        return _unreadable(location, path, error)
    if not is_file:
        others = _same_stem_files(path)
        found = f" (found {_names(others)})" if others else ""
        return f"{location}: {path.name} is missing; expected at {path}{found}."
    try:
        present = _COLUMN_READERS[path.suffix](path)
    except (ValueError, OSError, pa.ArrowException) as error:
        return _unreadable(location, path, error)
    missing = [column for column in columns if column not in present]
    if missing:
        return (
            f"{location}: {path.name} at {path} is missing required columns "
            f"{_names(missing)}; its columns are {_names(present)}."
        )
    return None


def handoff_inputs(
    root: Path | str, locations: Iterable[str], files: Mapping[str, Sequence[str]]
) -> dict[ArtifactKey, Path]:
    """Validate each location's primary-data files and return them as root inputs.

    Every declared file of every location is checked before anything is returned: it
    must exist, be readable in the format its suffix names, and have every required
    column. Only the column names are read (the CSV header or the Parquet footer), so
    the check is cheap for large files. Values, dtypes, and row counts are not checked.

    Parameters
    ----------
    root
        The handoff root. Location ``loc``'s file ``name.ext`` is expected at
        ``<root>/<loc>/primary/<name.ext>``.
    locations
        Locations whose handoff the pipeline reads, each a single directory name. A
        location listed more than once is checked once.
    files
        Maps each file name every location must supply (a ``.csv`` or ``.parquet``
        file name) to the columns it must have. Extra columns and any column order
        are accepted.

    Returns
    -------
        Each file's path under the resolved root, keyed by :func:`handoff_key`, to
        pass as, or merge into, an ``ArtifactRegistry``'s ``root_inputs``.

    Raises
    ------
    HandoffError
        If any declared file is missing, cannot be read (including when a directory
        on its path cannot be searched or the reader rejects the file), or lacks a
        required column. One error lists every problem found, grouped by location,
        and names each file's expected path; a missing-columns problem lists the
        missing columns and then the columns present, each quoted.
    ValueError
        Before any file is read, if ``locations`` is a bare string, a location or
        file name is not a single path component, a file's suffix is not ``.csv`` or
        ``.parquet``, or a column list is given as a bare string.
    """
    if isinstance(locations, str):
        raise ValueError(
            f"locations must be a list of location names, not the string {locations!r}."
        )
    unique_locations = list(dict.fromkeys(locations))
    _check_declaration(unique_locations, files)
    root_dir = Path(root).resolve()

    found: dict[ArtifactKey, Path] = {}
    problems: list[str] = []
    for location in unique_locations:
        for filename, columns in files.items():
            path = root_dir / location / PRIMARY_DIR / filename
            problem = _problem(location, path, columns)
            if problem is None:
                found[handoff_key(location, filename)] = path
            else:
                problems.append(problem)
    if problems:
        count = f"{len(problems)} problem{'s' if len(problems) > 1 else ''}"
        listing = "\n".join(f"  - {problem}" for problem in problems)
        raise HandoffError(
            f"the primary-data handoff under {root_dir} is not ready ({count}):\n{listing}"
        )
    return found
