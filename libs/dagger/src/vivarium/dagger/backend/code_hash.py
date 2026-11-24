"""
=========
code_hash
=========

Hash the code of a Python file or a Jupyter notebook so that an edit that does not change
the code, such as a comment or a re-wrapped line, does not change the hash.

A Python file is hashed by a canonical text of its syntax tree. The text holds each node's
name and its fields, and leaves out what only describes how the code was written. It is
the same on every Python version that dagger supports, so a hash taken on one version
equals the hash of the same file taken on another. Docstrings, annotations, decorators,
the order of imports and string literals are part of the code and change the hash.

A notebook is hashed by its non-empty code cells, in order. Outputs, execution counts,
markdown cells and the notebook's metadata are not read, so a notebook and an executed
copy of it have the same hash. IPython syntax in a cell, such as a ``%magic`` or a
``!shell`` line, is replaced so that the cell parses as Python, and the text of each such
line is also part of the hash. The position of the cell tagged ``parameters`` is part of
the hash, because it decides where the pipeline's values are injected. A semicolon at the
end of a cell's last line is not, because it only hides a displayed value.

A file or a cell that cannot be parsed is hashed by its bytes, and the result says so.
Neither function raises for a file whose content cannot be parsed.

"""

import ast
import hashlib
import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

_AST_PREFIX = "ast:"
_BYTES_PREFIX = "bytes:"
_NOTEBOOK_PREFIX = "nb:"

_DROPPED_FIELDS = frozenset({"ctx", "kind", "type_comment"})
"""Syntax-tree fields that only describe how the code was written."""

_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)

_PYTHON_CELL_MAGICS = frozenset({"time", "timeit", "capture", "prun"})
"""Cell magics whose body is Python."""

_PARAMETER_TAGS = ("parameters", "injected-parameters")

_IPYTHON_ASSIGNMENT = re.compile(r"(?P<target>\s*[A-Za-z_][\w.\[\]'\"]*\s*=\s*)[!%]")
"""Matches a shell or magic assignment such as ``files = !ls`` or ``x = %who_ls``."""

_HELP = re.compile(r"[\w.*]+\?{1,2}")
"""Matches a help line such as ``len?`` or ``np.*load*??``."""


@dataclass(frozen=True)
class CodeHash:
    """The hash of the code of one file."""

    digest: str
    """The hash. Two files have the same digest only if their code is the same."""
    parsed: bool
    """Whether the digest was taken from parsed code. ``False`` means that the file, or
    at least one code cell of a notebook, could not be parsed and its bytes were hashed
    instead."""


_results: dict[tuple[str, str], CodeHash] = {}
"""Results by kind and SHA-256 of the file's bytes, so that an edited file is never given
a stale result, whatever its size and modification time."""


def python_file_hash(path: Path | str) -> CodeHash:
    """Return the hash of the code in the Python file at ``path``.

    The file is parsed as Python, with its encoding declaration or byte-order mark if it
    has one, and hashed by the canonical text of its syntax tree. A file that cannot be
    parsed is hashed by its bytes. The result for one content is computed once per
    process; a file whose bytes changed is parsed again.

    Parameters
    ----------
    path
        The Python file.

    Returns
    -------
        The file's code hash.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    return _cached_hash(path, "python", _python_code_hash)


def notebook_hash(path: Path | str) -> CodeHash:
    """Return the hash of the code in the Jupyter notebook at ``path``.

    The hash covers the notebook's non-empty code cells in order, the text of the IPython
    lines in them, and the position of the cell tagged ``parameters``. A cell that cannot
    be parsed after its IPython syntax is replaced is hashed by its bytes, and so is a
    file that is not a notebook. In both cases the result's ``parsed`` is ``False``. The
    result for one content is computed once per process; a file whose bytes changed is
    read again.

    Parameters
    ----------
    path
        The notebook file.

    Returns
    -------
        The notebook's code hash.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    return _cached_hash(path, "notebook", _notebook_code_hash)


def _cached_hash(
    path: Path | str, kind: str, compute: Callable[[bytes], CodeHash]
) -> CodeHash:
    """Return the hash of the file's bytes, computing it once per content and kind."""
    data = Path(path).read_bytes()
    key = (kind, hashlib.sha256(data).hexdigest())
    if key not in _results:
        _results[key] = compute(data)
    return _results[key]


def _bytes_digest(data: bytes) -> str:
    """Return the digest of raw bytes."""
    return _BYTES_PREFIX + hashlib.sha256(data).hexdigest()


def _python_code_hash(data: bytes) -> CodeHash:
    """Return the hash of a Python file's content."""
    digest = _syntax_tree_digest(data)
    if digest is None:
        return CodeHash(_bytes_digest(data), parsed=False)
    return CodeHash(digest, parsed=True)


def _syntax_tree_digest(source: str | bytes, flags: int = 0) -> str | None:
    """Return the digest of the canonical text of the source's syntax tree, or None."""
    try:
        with warnings.catch_warnings():
            # A SyntaxWarning, such as for an invalid escape sequence, becomes a
            # SyntaxError under an "error" filter, which would make the hash depend on the
            # caller's warning settings.
            warnings.simplefilter("ignore")
            tree = compile(
                source, "<code_hash>", "exec", ast.PyCF_ONLY_AST | flags, dont_inherit=True
            )
        # ValueError is also raised by ascii() for an int literal past the digit limit.
        text = _canonical_text(tree)
    except _PARSE_ERRORS:
        return None
    return _AST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Text(str):
    """Text written into the canonical text as it is."""


def _canonical_text(tree: ast.AST) -> str:
    """Return the text of a syntax tree that is the same on every supported Python."""
    # A stack, not recursion, so that a deeply nested expression that the parser accepts
    # does not exceed the recursion limit here.
    parts: list[str] = []
    stack: list[object] = [tree]
    while stack:
        item = stack.pop()
        children: list[object] = []
        if isinstance(item, _Text):
            parts.append(item)
            continue
        if isinstance(item, ast.AST):
            parts.append(type(item).__name__ + "(")
            for name in item._fields:
                value = getattr(item, name, None)
                if name in _DROPPED_FIELDS or value is None:
                    continue
                if isinstance(value, list) and not value:
                    continue
                children += [_Text(name + "="), value, _Text(",")]
            children.append(_Text(")"))
        elif isinstance(item, list):
            parts.append("[")
            for element in item:
                children += [element, _Text(",")]
            children.append(_Text("]"))
        else:
            # ascii(), not repr(): repr() leaves a character unescaped when the
            # interpreter's Unicode tables call it printable, and those tables differ
            # between Python versions.
            parts.append(f"{type(item).__name__}:{ascii(item)}")
        stack.extend(reversed(children))
    return "".join(parts)


class _CodeCell(NamedTuple):
    """The source of a code cell and its parameter tags."""

    source: str
    tags: tuple[str, ...]


class _TransformedCell(NamedTuple):
    """A cell's Python, None for a non-Python cell magic, and its IPython lines."""

    python: str | None
    ipython_lines: list[str]


class _LineState(NamedTuple):
    """The open triple quote, bracket depth and backslash continuation at a line's end."""

    quote: str | None = None
    depth: int = 0
    continued: bool = False

    @property
    def at_statement_start(self) -> bool:
        """Return whether the next line starts a statement."""
        return self.quote is None and self.depth == 0 and not self.continued


def _notebook_code_hash(data: bytes) -> CodeHash:
    """Return the hash of a notebook file's content."""
    cells = _code_cells(data)
    if cells is None:
        return CodeHash(_bytes_digest(data), parsed=False)
    entries: list[list[object]] = []
    tags: list[list[object]] = []
    parsed = True
    for cell in cells:
        if cell.source.strip():
            entry = _cell_entry(cell.source)
            parsed = parsed and entry[0] != "bytes"
            entries.append(entry)
        # The tag is kept as the number of non-empty code cells up to and including its
        # cell, which is where papermill injects the parameters.
        tags += [[len(entries), tag] for tag in cell.tags]
    text = json.dumps([entries, tags], ensure_ascii=True, separators=(",", ":"))
    digest = _NOTEBOOK_PREFIX + hashlib.sha256(text.encode("ascii")).hexdigest()
    return CodeHash(digest, parsed)


def _code_cells(data: bytes) -> list[_CodeCell] | None:
    """Return a notebook's code cells in order, or None if the data is not a notebook."""
    try:
        notebook = json.loads(data)
    except (ValueError, RecursionError):
        return None
    raw_cells = _raw_cells(notebook)
    if raw_cells is None:
        return None
    cells: list[_CodeCell] = []
    for raw in raw_cells:
        if not isinstance(raw, dict):
            return None
        if raw.get("cell_type") != "code":
            continue
        source = raw.get("source", raw.get("input", ""))
        if isinstance(source, list) and all(isinstance(part, str) for part in source):
            source = "".join(source)
        if not isinstance(source, str):
            return None
        cells.append(_CodeCell(source, _parameter_tags(raw.get("metadata"))))
    return cells


def _raw_cells(notebook: object) -> list[object] | None:
    """Return the cells of nbformat 4 or of the worksheets of nbformat 3, or None."""
    if not isinstance(notebook, dict):
        return None
    if "cells" in notebook:
        cells = notebook["cells"]
        return cells if isinstance(cells, list) else None
    worksheets = notebook.get("worksheets")
    if not isinstance(worksheets, list):
        return None
    worksheet_cells: list[object] = []
    for worksheet in worksheets:
        sheet_cells = worksheet.get("cells", []) if isinstance(worksheet, dict) else None
        if not isinstance(sheet_cells, list):
            return None
        worksheet_cells += sheet_cells
    return worksheet_cells


def _parameter_tags(metadata: object) -> tuple[str, ...]:
    """Return the parameter tags in a cell's metadata."""
    tags = metadata.get("tags") if isinstance(metadata, dict) else None
    if not isinstance(tags, list):
        return ()
    return tuple(tag for tag in _PARAMETER_TAGS if tag in tags)


def _cell_entry(source: str) -> list[object]:
    """Return what the notebook hash holds for one non-empty code cell."""
    cell = _transform_cell(source)
    if cell.python is None:
        return ["magic", cell.ipython_lines]
    digest = _syntax_tree_digest(cell.python, ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    if digest is None:
        return ["bytes", _bytes_digest(source.encode("utf-8", "surrogatepass"))]
    return ["code", digest, cell.ipython_lines]


def _transform_cell(source: str) -> _TransformedCell:
    """Replace a cell's IPython syntax so that the cell parses as Python."""
    lines = source.split("\n")
    ipython_lines: list[str] = []
    first = next((i for i, line in enumerate(lines) if line.strip()), 0)
    if lines[first].lstrip().startswith("%%"):
        magic_line = lines[first].strip()
        words = magic_line[2:].split()
        if not words or words[0] not in _PYTHON_CELL_MAGICS:
            return _TransformedCell(None, [magic_line, "\n".join(lines[first + 1 :])])
        ipython_lines.append(magic_line)
        lines = lines[first + 1 :]
    python_lines: list[str] = []
    state = _LineState()
    for line in lines:
        replacement = _replace_ipython_line(line) if state.at_statement_start else None
        if replacement is None:
            python_lines.append(line)
            state = _scan_line(line, state)
        else:
            python_lines.append(replacement)
            ipython_lines.append(line.strip())
    return _TransformedCell("\n".join(python_lines), ipython_lines)


def _replace_ipython_line(line: str) -> str | None:
    """Return the Python that replaces an IPython line, or None for a line of Python."""
    stripped = line.lstrip()
    assignment = _IPYTHON_ASSIGNMENT.match(line)
    if assignment:
        return assignment["target"] + "None"
    if stripped.startswith(("!", "%", "?")) or _HELP.fullmatch(stripped.rstrip()):
        return line[: len(line) - len(stripped)] + "pass"
    return None


def _scan_line(line: str, state: _LineState) -> _LineState:
    """Return the state at the end of a line of Python, given the state at its start."""
    quote, depth = state.quote, state.depth
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if char == "\\":
                index += 2
            elif line.startswith(quote, index):
                quote = None
                index += 3
            else:
                index += 1
            continue
        if char == "#":
            return _LineState(None, depth, continued=False)
        if char in "\"'" and line.startswith(char * 3, index):
            quote = char * 3
            index += 3
            continue
        if char in "\"'":
            index += 1
            while index < len(line) and line[index] != char:
                index += 2 if line[index] == "\\" else 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        index += 1
    continued = quote is None and line.rstrip("\r").endswith("\\")
    return _LineState(quote, depth, continued)
