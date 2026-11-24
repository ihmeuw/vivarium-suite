"""Hash the code of a Python file or a Jupyter notebook so that an edit that does not change
the code, such as a comment or a re-wrapped line, does not change the hash.

A Python file is hashed by a canonical text of its syntax tree. The text holds each node's
name and its fields, and leaves out what only describes how the code was written. For a
file that Python 3.11, 3.12 and 3.13 can all parse, the text is the same on all three, so
a hash taken on one of them equals the hash taken on another. Docstrings, annotations,
decorators, the order of imports and string literals are part of the code and change the
hash.

A notebook is hashed by its non-empty code cells, in order. Outputs, execution counts,
markdown cells and the notebook's metadata are not read, so they do not change the hash; a
cell that papermill injects into an executed copy does. IPython syntax in a cell is
replaced so that the cell parses as Python, and the text and the position of each replaced
line are part of the hash. Replaced are lines that start a statement with ``!``, ``%`` or
``?``, help lines such as ``len?``, and assignments such as ``x = !ls`` or ``x: int =
!ls``, each with any lines that it continues onto with a backslash. The body of a
``%%time``, ``%%timeit``, ``%%capture`` or ``%%prun`` cell is parsed as Python; the body
of any other cell magic, such as ``%%bash``, is hashed exactly as written, so a comment or
a semicolon in it changes the hash, and the result is still parsed. Other IPython syntax,
such as an autocall line or a cell with a common indent, does not parse; that cell is
hashed by its bytes. The positions of the code cells tagged ``parameters`` and
``injected-parameters`` are part of the hash, because they decide where the pipeline's
values are injected. A semicolon at the end of a cell's last line of code is not, also on
an IPython line, because it only hides a displayed value.

A file or a cell that cannot be parsed is hashed by its bytes, and the result says so.
This includes code that uses syntax newer than the running Python, code nested deeper than
the running Python's parser accepts, and a decimal int literal with more digits than the
process's limit for converting ints to text; such a file can have a parsed hash on one
Python and a bytes hash on another. Neither function raises for a file whose content
cannot be parsed.

"""

import ast
import hashlib
import json
import re
import sys
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

PARAMETERS_TAG = "parameters"
"""The tag of the notebook cell that papermill injects parameters after. Papermill and
:mod:`~vivarium.dagger.frontend.notebook_step` use only the first cell that carries it;
the notebook hash records the position of every cell that carries it."""

_AST_PREFIX = "ast:"
_BYTES_PREFIX = "bytes:"
_NOTEBOOK_PREFIX = "nb:"

_DROPPED_FIELDS = frozenset({"ctx", "kind", "type_comment"})
"""Syntax-tree fields that only describe how the code was written."""

_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)

_PYTHON_CELL_MAGICS = frozenset({"time", "timeit", "capture", "prun"})
"""Cell magics whose body is Python."""

_PARAMETER_TAGS = (PARAMETERS_TAG, "injected-parameters")

_IPYTHON_CALL = "__ipython__"
"""The name of the call that stands for an IPython line in the Python a cell becomes."""

_OPERATOR_BEFORE_EQUALS = frozenset("=!<>+-*/%&|^@:")
"""Characters that, directly before an ``=``, make it part of another operator."""

_HELP = re.compile(r"[\w.*]+\?{1,2}")
"""Matches a help line such as ``len?`` or ``np.*load*??``."""


@dataclass(frozen=True)
class CodeHash:
    """The hash of the code of one file."""

    digest: str
    """The hash. When ``parsed`` is ``True``, two files with the same code have the same
    digest on one Python version, and files with different code have different digests;
    a bytes digest changes with any edit. It starts with
    ``ast:`` for a Python file hashed by its parsed code, ``nb:`` for a notebook hashed
    by its cells, and ``bytes:`` for a file hashed by its bytes."""
    parsed: bool
    """Whether the digest was taken from parsed code. ``False`` means that the file, or
    at least one code cell of a notebook, could not be parsed and its bytes were hashed
    instead."""


_results: dict[tuple[str, str, int], CodeHash] = {}
"""Results by kind, SHA-256 of the file's bytes and the process's int-string digit limit,
so that an edited file is never given a stale result, whatever its size and modification
time. The limit is in the key because it decides whether a long decimal int literal
parses."""

_warnings_lock = threading.Lock()
"""Held around the parse's temporary warning filter, because ``warnings.catch_warnings``
saves and restores the process-wide filters and two threads that overlap would restore
each other's."""


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
    return python_code_hash(Path(path).read_bytes())


def python_code_hash(data: bytes) -> CodeHash:
    """Return the hash of the code in a Python file whose bytes are ``data``.

    The result is the one :func:`python_file_hash` gives for a file with these bytes, and
    it shares that function's cache, so the result for one content is computed once per
    process. The syntax tree is not kept; a caller that also needs the tree gets it from
    :func:`parse_source`, which parses the content again.

    Parameters
    ----------
    data
        The bytes of the Python file.

    Returns
    -------
        The content's code hash.
    """
    return _cached_hash(data, "python", _python_code_hash)


def parse_source(source: str | bytes) -> ast.AST | None:
    """Return the syntax tree of Python source, or ``None`` if it cannot be parsed.

    The source is parsed as the code hash parses it: a top-level ``await`` is accepted,
    and the process's warning filters are ignored, so that a ``SyntaxWarning`` never
    becomes an error. Source with a syntax error, source nested deeper than the parser
    accepts, and source with a decimal int literal that has more digits than the
    process's int-string limit give ``None``. Each call parses the source; nothing is
    cached.

    Parameters
    ----------
    source
        The Python source, as text or as the bytes of a file, with its encoding
        declaration or byte-order mark if it has one.

    Returns
    -------
        The source's syntax tree, an :class:`ast.Module`, or ``None``.
    """
    return _parsed(source)


def notebook_hash(path: Path | str) -> CodeHash:
    """Return the hash of the code in the Jupyter notebook at ``path``.

    The hash covers the notebook's non-empty code cells in order, the text and position of
    the IPython lines in them, and the positions of the cells tagged ``parameters`` and
    ``injected-parameters``; a tag on a markdown or raw cell is not read. A cell that
    cannot be parsed after its IPython syntax is replaced is hashed by its bytes, and so
    is a file that is not a notebook. In both cases the result's ``parsed`` is ``False``.
    The result for one content is computed once per process; a file whose bytes changed is
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
    return _cached_hash(Path(path).read_bytes(), "notebook", _notebook_code_hash)


def cell_source(source: object) -> str | None:
    """Return the text of a notebook cell's source.

    A notebook stores a cell's source as one string or as a list of strings, which are
    joined with nothing between them.

    Parameters
    ----------
    source
        The value of the cell's source field, as read from the notebook's JSON.

    Returns
    -------
        The cell's text, or ``None`` if ``source`` is neither a string nor a list of
        strings.
    """
    if isinstance(source, str):
        return source
    if isinstance(source, list) and all(isinstance(part, str) for part in source):
        return "".join(source)
    return None


def _cached_hash(data: bytes, kind: str, compute: Callable[[bytes], CodeHash]) -> CodeHash:
    """Return the code hash of bytes, computed once per content, kind and int limit."""
    key = (kind, hashlib.sha256(data).hexdigest(), sys.get_int_max_str_digits())
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


def _syntax_tree_digest(source: str | bytes) -> str | None:
    """Return the digest of the canonical text of the source's syntax tree, or None."""
    tree = _parsed(source)
    if tree is None:
        return None
    try:
        text = _canonical_text(tree)
    except _PARSE_ERRORS:
        return None
    return _AST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parsed(source: str | bytes) -> ast.AST | None:
    """Return the source's syntax tree, or None if it cannot be parsed."""
    try:
        return _parse(source)
    except _PARSE_ERRORS:
        return None


def _parse(source: str | bytes) -> ast.AST:
    """Return the source's syntax tree, whatever the warning filters."""
    # Only a syntax tree is built, so a top-level await is accepted.
    with _warnings_lock, warnings.catch_warnings():
        # A SyntaxWarning, such as for an invalid escape sequence, becomes a SyntaxError
        # under an "error" filter, which would make the hash depend on the caller's
        # warning settings.
        warnings.simplefilter("ignore")
        tree: ast.AST = compile(
            source, "<code_hash>", "exec", ast.PyCF_ONLY_AST, dont_inherit=True
        )
    return tree


class _Text(str):
    """Text written into the canonical text as it is."""


def _canonical_text(tree: ast.AST) -> str:
    """Return the text of a syntax tree that is the same on Python 3.11 to 3.13."""
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
        elif isinstance(item, int) and not isinstance(item, bool):
            # Hex has no digit limit; decimal past sys.get_int_max_str_digits() raises.
            parts.append(f"int:{item:#x}")
        else:
            # ascii(), not repr(): repr() leaves a character unescaped when the
            # interpreter's Unicode tables call it printable, and those tables differ
            # between Python versions.
            parts.append(f"{type(item).__name__}:{ascii(item)}")
        stack.extend(reversed(children))
    return "".join(parts)


class _JsonInt:
    """A JSON integer of a notebook, kept as its text."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class _CodeCell(NamedTuple):
    """The source of a code cell and its parameter tags."""

    source: str
    tags: tuple[str, ...]


class _LineState(NamedTuple):
    """The open string quote, bracket depth and backslash continuation at a line's end."""

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
    # int() of a JSON integer raises past sys.get_int_max_str_digits(), for example on an
    # output that the hash does not read. An integer is kept as neither int nor str, so
    # that one where a source or a tag belongs is still rejected or ignored.
    try:
        notebook = json.loads(data, parse_int=_JsonInt)
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
        source = cell_source(raw.get("source", raw.get("input", "")))
        if source is None:
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
    lines = source.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), 0)
    header = lines[first].strip()
    if header.startswith("%%"):
        words = header[2:].split()
        if not words or words[0] not in _PYTHON_CELL_MAGICS:
            return ["magic", header, "\n".join(lines[first + 1 :])]
        lines = [_ipython_call(header), *lines[first + 1 :]]
    digest = _syntax_tree_digest(_cell_python(lines))
    if digest is None:
        return ["bytes", _bytes_digest(source.encode("utf-8", "surrogatepass"))]
    return ["code", digest]


def _cell_python(lines: list[str]) -> str:
    """Return a cell's Python, each IPython line replaced by a call that holds its text."""
    last = max((i for i, line in enumerate(lines) if _is_code(line)), default=-1)
    python: list[str] = []
    state = _LineState()
    start = 0
    while start < len(lines):
        line = lines[start]
        split = _split_ipython_line(line) if state.at_statement_start else None
        if split is None:
            python.append(line)
            state = _scan_line(line, state)
            start += 1
            continue
        end = start
        while end + 1 < len(lines) and _continues(lines[end]):
            end += 1
        target, head = split
        text = _joined([head, *lines[start + 1 : end + 1]])
        if end >= last and text.endswith(";"):
            text = text[:-1].rstrip()
        call = _ipython_call(text)
        indent = line[: len(line) - len(line.lstrip())]
        python.append(indent + call if target is None else f"{target.rstrip()} = {call}")
        start = end + 1
    return "\n".join(python)


def _is_code(line: str) -> bool:
    """Return whether a line is neither blank nor only a comment."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _continues(line: str) -> bool:
    """Return whether a line ends with a backslash that continues it on the next line."""
    return line.rstrip("\r").endswith("\\")


def _joined(parts: list[str]) -> str:
    """Return an IPython line and its continuation lines as one line of text."""
    pieces = [part.rstrip("\r")[:-1].strip() for part in parts[:-1]] + [parts[-1].strip()]
    return " ".join(piece for piece in pieces if piece)


def _ipython_call(text: str) -> str:
    """Return the Python call that stands for the IPython text."""
    return f"{_IPYTHON_CALL}({ascii(text)})"


def _split_ipython_line(line: str) -> tuple[str | None, str] | None:
    """Return an IPython line's target, or None, and its text; None for a line of Python."""
    stripped = line.lstrip()
    if stripped.startswith(("!", "%", "?")) or _HELP.fullmatch(stripped.rstrip()):
        return None, line
    equals = _ipython_assignment_equals(line)
    if equals is None:
        return None
    return line[:equals], line[equals + 1 :]


def _ipython_assignment_equals(line: str) -> int | None:
    """Return the index of the top-level ``=`` of a shell or magic assignment, or None."""
    depth = 0
    index = 0
    while index < len(line):
        char = line[index]
        if char == "#":
            return None
        if char in "\"'":
            end = _string_end(line, index)
            if end is None:
                return None
            index = end
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == "=" and depth == 0:
            if line[index - 1 : index] in _OPERATOR_BEFORE_EQUALS:
                return None
            if line[index + 1 : index + 2] == "=":
                return None
            if line[index + 1 :].lstrip(" \t").startswith(("!", "%")):
                return index
            return None
        index += 1
    return None


def _string_end(line: str, start: int) -> int | None:
    """Return the index after the string literal that starts at ``start``, or None."""
    char = line[start]
    quote = char * 3 if line.startswith(char * 3, start) else char
    index = start + len(quote)
    while index < len(line):
        if line[index] == "\\":
            index += 2
        elif line.startswith(quote, index):
            return index + len(quote)
        else:
            index += 1
    return None


def _scan_line(line: str, state: _LineState) -> _LineState:
    """Return the state at the end of a line of Python, given the state at its start."""
    quote, depth = state.quote, state.depth
    text = line.rstrip("\r")
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
            elif text.startswith(quote, index):
                index += len(quote)
                quote = None
            else:
                index += 1
            continue
        if char == "#":
            return _LineState(None, depth, continued=False)
        if char in "\"'":
            quote = char * 3 if text.startswith(char * 3, index) else char
            index += len(quote)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        index += 1
    # An escape that consumes the line end leaves the index one past the text; only then
    # does a short string go on to the next line, as in Python.
    if quote is not None and len(quote) == 1 and index == len(text):
        return _LineState(None, depth, continued=False)
    continued = quote is None and text.endswith("\\")
    return _LineState(quote, depth, continued)
