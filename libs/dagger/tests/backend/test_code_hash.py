"""Tests of the code hash of Python files and notebooks."""

import builtins
import copy
import json
import os
import shutil
import sys
import uuid
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from vivarium.dagger.backend import code_hash as code_hash_module
from vivarium.dagger.backend.code_hash import CodeHash, notebook_hash, python_file_hash
from vivarium.dagger.backend.wiring import FileRegistry
from vivarium.dagger.frontend.notebook_step import notebook_step

_FIXED_SOURCES = Path(__file__).parent / "data" / "code_hash"
"""The sources whose digests are stored, for the test across Python versions."""

_BASE_SOURCE = '''\
"""Combine values with a factor."""

import functools
import os
import sys


@functools.cache
def scale(values: tuple[float, ...], factor: float = 2.0) -> tuple[float, ...]:
    """Return the values combined with the factor.

    The factor defaults to two.
    """
    total = factor * 2
    return combine(values, total, os.sep, sys.platform, "label")
'''
"""A Python file that the edits below change."""

_SAME_CODE_EDITS: dict[str, tuple[str, str]] = {
    "comment": (
        "    total = factor * 2\n",
        "    # Double the factor.\n    total = factor * 2  # doubled\n",
    ),
    "blank_lines": ("import sys\n", "import sys\n\n\n\n"),
    "rewrap": (
        'combine(values, total, os.sep, sys.platform, "label")',
        "combine(\n"
        "        values,\n"
        "        total,\n"
        "        os.sep,\n"
        "        sys.platform,\n"
        '        "label"\n'
        "    )",
    ),
    "quote_style": ('"label"', "'label'"),
    "redundant_parentheses": ("factor * 2", "(factor * 2)"),
}
"""Edits of ``_BASE_SOURCE`` that leave its code the same, as (old text, new text)."""

_CODE_EDITS: dict[str, tuple[str, str]] = {
    "code": ("factor * 2", "factor * 3"),
    "docstring": ("combined with the factor", "combined with a factor"),
    "docstring_reformat": (
        "    The factor defaults to two.\n",
        "        The factor defaults to two.\n",
    ),
    "annotation": ("factor: float", "factor: complex"),
    "decorator": ("@functools.cache", "@functools.lru_cache"),
    "import_order": ("import os\nimport sys\n", "import sys\nimport os\n"),
    "string_literal": ('"label"', '"name"'),
}
"""Edits of ``_BASE_SOURCE`` that change its code, as (old text, new text)."""

_PARAMETERS_CELL = 1
"""The index in ``_base_cells`` of the code cell tagged ``parameters``."""

_FRAME_CELL = 2
"""The index in ``_base_cells`` of the code cell that builds a data frame."""

_SUMMARY_CELL = 3
"""The index in ``_base_cells`` of the code cell with a line magic and a shell line."""

_IPYTHON_CELLS: dict[str, str] = {
    "line_magic": "%load_ext autoreload\nvalue = 1",
    "shell_escape": "!pip list\nvalue = 1",
    "shell_assignment": "files = !ls\ncount = len(files)",
    "help": "len?\nvalue = 1",
    "cell_magic": "%%time\ntotal = sum(range(10))",
}
"""The source of a code cell with each kind of IPython syntax."""


def _edited(text: str, old: str, new: str) -> str:
    """Return ``text`` with its one occurrence of ``old`` replaced by ``new``."""
    assert text.count(old) == 1, f"the edit expects exactly one {old!r} in the text"
    return text.replace(old, new)


def _python_hash(directory: Path, name: str, source: str | bytes) -> CodeHash:
    """Write a Python file into ``directory`` and return its code hash."""
    path = directory / name
    path.write_bytes(source.encode("utf-8") if isinstance(source, str) else source)
    return python_file_hash(path)


def _code_cell(source: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Return an unexecuted nbformat 4 code cell."""
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {} if tags is None else {"tags": tags},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def _markdown_cell(source: str) -> dict[str, Any]:
    """Return an nbformat 4 markdown cell."""
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def _notebook(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Return an nbformat 4 notebook with ``cells``."""
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


def _base_cells() -> list[dict[str, Any]]:
    """Return the cells of a notebook that the edits below change."""
    return [
        _markdown_cell("# Coverage by year"),
        _code_cell('location = "Kenya"\nyear = 2025', tags=["parameters"]),
        _code_cell(
            "import pandas as pd\n"
            "\n"
            'frame = pd.DataFrame({"year": [year]})\n'
            'frame["location"] = location'
        ),
        _code_cell("%matplotlib inline\n!echo starting\nsummary = frame.describe()\nsummary"),
    ]


def _edit_cell(cells: list[dict[str, Any]], index: int, old: str, new: str) -> None:
    """Replace the one occurrence of ``old`` in the source of ``cells[index]``."""
    source = "".join(cells[index]["source"])
    cells[index]["source"] = _edited(source, old, new).splitlines(keepends=True)


def _notebook_hash(directory: Path, name: str, notebook: dict[str, Any]) -> CodeHash:
    """Write a notebook into ``directory`` and return its code hash."""
    path = directory / name
    path.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    return notebook_hash(path)


def _cell_hash(directory: Path, name: str, source: str) -> CodeHash:
    """Write a notebook with one code cell into ``directory`` and return its code hash."""
    return _notebook_hash(directory, name, _notebook([_code_cell(source)]))


# Python files: edits that do not change the code


@pytest.mark.parametrize("edit", list(_SAME_CODE_EDITS))
def test_an_edit_that_does_not_change_the_code_keeps_a_python_files_hash(
    tmp_path: Path, edit: str
) -> None:
    """A comment, added blank lines, a re-wrapped call, a change of quote style, or redundant parentheses around an expression leave the hash of a Python file unchanged, and the result says it was parsed."""
    old, new = _SAME_CODE_EDITS[edit]
    original = _python_hash(tmp_path, "original.py", _BASE_SOURCE)
    edited = _python_hash(tmp_path, "edited.py", _edited(_BASE_SOURCE, old, new))
    assert original.parsed
    assert edited.parsed
    assert edited.digest == original.digest


# Python files: edits that change the code


@pytest.mark.parametrize("edit", list(_CODE_EDITS))
def test_an_edit_of_the_code_changes_a_python_files_hash(tmp_path: Path, edit: str) -> None:
    """A change to a statement, to a docstring (also only re-indenting its text), to an annotation, to a decorator, to the order of two imports, or to a string literal changes the hash of a Python file."""
    old, new = _CODE_EDITS[edit]
    original = _python_hash(tmp_path, "original.py", _BASE_SOURCE)
    edited = _python_hash(tmp_path, "edited.py", _edited(_BASE_SOURCE, old, new))
    assert original.parsed
    assert edited.parsed
    assert edited.digest != original.digest


def test_two_files_with_different_code_have_different_hashes(tmp_path: Path) -> None:
    """Two Python files whose code differs only in one constant (1 and 1.0, or True and 1) have different hashes."""
    pairs = [("value = 1\n", "value = 1.0\n"), ("value = True\n", "value = 1\n")]
    for index, (first_source, second_source) in enumerate(pairs):
        first = _python_hash(tmp_path, f"first_{index}.py", first_source)
        second = _python_hash(tmp_path, f"second_{index}.py", second_source)
        assert first.parsed and second.parsed
        assert first.digest != second.digest, (first_source, second_source)


def test_an_int_of_any_size_is_hashed_by_its_parsed_code(tmp_path: Path) -> None:
    """A Python file that assigns an int with more decimal digits than the int-to-string limit is parsed and gets the same hash whatever sys.set_int_max_str_digits is set to, another such int gets another hash, and True, False, 1 and 0 each get a different hash."""
    source = f"X = 0x{'f' * 5000}\n"
    default_limit = sys.get_int_max_str_digits()
    results: list[CodeHash] = []
    try:
        for index, limit in enumerate([640, 0, default_limit]):
            sys.set_int_max_str_digits(limit)
            results.append(
                _python_hash(tmp_path, f"limit_{index}.py", f"# limit {limit}\n{source}")
            )
    finally:
        sys.set_int_max_str_digits(default_limit)
    assert all(result.parsed for result in results), results
    assert len({result.digest for result in results}) == 1, results

    other = _python_hash(tmp_path, "other.py", f"X = 0x{'f' * 4999}e\n")
    assert other.parsed
    assert other.digest != results[0].digest

    values = ["True", "False", "1", "0"]
    digests = {
        value: _python_hash(tmp_path, f"value_{index}.py", f"value = {value}\n").digest
        for index, value in enumerate(values)
    }
    assert len(set(digests.values())) == len(values), digests


# Python files: the same hash on every supported Python version


def test_the_hash_of_fixed_sources_matches_the_stored_digests(tmp_path: Path) -> None:
    """For each Python file and notebook in a fixed set of sources stored in the repository, the hash equals the digest stored beside them, which was taken on another Python version; the set includes non-ASCII string literals, f-strings, type annotations, decorators, a match statement and a notebook with IPython lines."""
    stored = json.loads((_FIXED_SOURCES / "digests.json").read_text(encoding="utf-8"))
    names = sorted(
        path.name for path in _FIXED_SOURCES.iterdir() if path.suffix in (".py", ".ipynb")
    )
    assert names, f"no fixed sources in {_FIXED_SOURCES}"
    assert sorted(stored) == names, "digests.json must have one entry per fixed source"

    for name in names:
        # Hash a copy in another directory, because the hash must not depend on the path.
        shutil.copyfile(_FIXED_SOURCES / name, tmp_path / name)
        hash_file = notebook_hash if name.endswith(".ipynb") else python_file_hash
        expected = CodeHash(digest=stored[name]["digest"], parsed=stored[name]["parsed"])
        assert hash_file(tmp_path / name) == expected, name


# Python files that cannot be parsed


def test_a_python_file_with_a_syntax_error_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    """A Python file with a syntax error gets a hash without raising, the result says it was not parsed, and a change to any byte of it changes the hash."""
    source = "def broken(:  # the first version\n    return 1\n"
    original = _python_hash(tmp_path, "broken.py", source)
    assert not original.parsed

    edits = [("first", "other"), ("return 1\n", "return 1 \n"), ("broken", "brokem")]
    for index, (old, new) in enumerate(edits):
        edited = _python_hash(tmp_path, f"broken_{index}.py", _edited(source, old, new))
        assert not edited.parsed
        assert edited.digest != original.digest, (old, new)


@pytest.mark.parametrize("marker", ["encoding_declaration", "byte_order_mark"])
def test_a_python_file_with_an_encoding_marker_is_hashed_by_its_code(
    tmp_path: Path, marker: str
) -> None:
    """A Python file that starts with an encoding declaration for latin-1 and holds a latin-1 encoded string literal, or one that starts with a UTF-8 byte-order mark, is parsed, and its hash equals that of a plain UTF-8 file with the same code."""
    source = 'city = "Malmö"\nnote = "naïve café"\n'
    if marker == "encoding_declaration":
        data = b"# -*- coding: latin-1 -*-\n" + source.encode("latin-1")
    else:
        data = b"\xef\xbb\xbf" + source.encode("utf-8")
    plain = _python_hash(tmp_path, "plain.py", source)
    marked = _python_hash(tmp_path, "marked.py", data)
    assert plain.parsed
    assert marked.parsed
    assert marked.digest == plain.digest


def test_a_python_file_nested_deeper_than_the_recursion_limit_is_parsed(
    tmp_path: Path,
) -> None:
    """A Python file whose expression nests deeper than Python's default recursion limit, but which the parser accepts, is parsed, and a change to its innermost term changes the hash."""
    terms = ["1"] * 2000
    original = _python_hash(tmp_path, "deep.py", "x = " + " + ".join(terms) + "\n")
    edited = _python_hash(
        tmp_path, "edited.py", "x = " + " + ".join(["2", *terms[1:]]) + "\n"
    )
    assert original.parsed
    assert edited.parsed
    assert edited.digest != original.digest


def test_a_syntax_warning_does_not_change_a_python_files_hash(tmp_path: Path) -> None:
    """A Python file with an invalid escape such as "\\d", which gives a SyntaxWarning, gets the same parsed result with warnings turned into errors as a file with the same code does without."""
    source = 'pattern = "\\d"\n'
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        strict = _python_hash(tmp_path, "strict.py", f"# warnings as errors\n{source}")
    default = _python_hash(tmp_path, "default.py", f"# default warnings\n{source}")
    assert strict.parsed
    assert strict == default


@pytest.mark.parametrize("filters", ["default", "error"])
def test_hashing_a_file_with_a_syntax_warning_leaves_the_warning_filters_unchanged(
    tmp_path: Path, filters: str
) -> None:
    """Hashing a Python file that gives a SyntaxWarning leaves warnings.filters exactly as it was, under the default filters and with warnings turned into errors."""
    # A unique comment, so the per-content cache cannot skip the parse.
    source = f'# {uuid.uuid4().hex}\nx = "\\d"\n'
    with warnings.catch_warnings():
        if filters == "error":
            warnings.simplefilter("error")
        before = list(warnings.filters)
        result = _python_hash(tmp_path, "escape.py", source)
        after = list(warnings.filters)
    assert result.parsed
    assert after == before


def test_a_python_file_with_a_null_byte_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    """A Python file that contains a NUL byte gets a hash without raising, and the result says it was not parsed."""
    result = _python_hash(tmp_path, "null.py", b"value = 1\n\x00\nother = 2\n")
    assert not result.parsed


def test_a_decimal_int_over_the_int_string_limit_is_hashed_by_its_bytes(
    tmp_path: Path,
) -> None:
    """A Python file with a decimal int literal longer than the process's int-to-string limit is not parsed and gets a hash of its bytes."""
    # A unique comment, so the per-content cache cannot return an earlier result.
    source = f"# {uuid.uuid4().hex}\nX = 1{'0' * 700}\n"
    default_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        result = _python_hash(tmp_path, "long_int.py", source)
    finally:
        sys.set_int_max_str_digits(default_limit)
    assert not result.parsed
    assert result.digest.startswith("bytes:"), result


def test_an_empty_python_file_is_parsed_and_an_undecodable_one_is_not(
    tmp_path: Path,
) -> None:
    """An empty Python file and one holding only a comment are parsed and have equal hashes, while one with invalid UTF-8 and no encoding declaration is not parsed."""
    empty = _python_hash(tmp_path, "empty.py", "")
    comment = _python_hash(tmp_path, "comment.py", "# Only a comment.\n")
    assert empty.parsed
    assert comment.parsed
    assert comment.digest == empty.digest

    undecodable = _python_hash(tmp_path, "undecodable.py", b"x = '\xff'\n")
    assert not undecodable.parsed


def test_a_missing_python_file_raises_oserror(tmp_path: Path) -> None:
    """python_file_hash raises OSError for a path that does not exist."""
    with pytest.raises(OSError):
        python_file_hash(tmp_path / "missing.py")


# Notebooks: what is not part of the hash


def test_a_notebook_and_its_executed_copy_have_the_same_hash(tmp_path: Path) -> None:
    """A notebook and a copy of it that differs in its cell outputs, execution counts, cell and notebook metadata, and kernel name have the same hash, and both results say they were parsed."""
    original = _notebook(_base_cells())
    executed = copy.deepcopy(original)
    count = 0
    for cell in executed["cells"]:
        cell["metadata"]["collapsed"] = False
        if cell["cell_type"] != "code":
            continue
        count += 1
        cell["execution_count"] = count
        cell["metadata"]["execution"] = {"iopub.status.busy": "2026-11-24T10:00:00.000000Z"}
        cell["outputs"] = [
            {"name": "stdout", "output_type": "stream", "text": ["starting\n"]},
            {
                "data": {"text/plain": [str(count)]},
                "execution_count": count,
                "metadata": {},
                "output_type": "execute_result",
            },
        ]
    executed["metadata"] = {
        "kernelspec": {"display_name": "Dagger step", "language": "python", "name": "dagger"},
        "language_info": {"name": "python", "version": "3.12.4"},
        "papermill": {"duration": 1.5, "parameters": {"location": "Kenya"}},
    }

    original_hash = _notebook_hash(tmp_path, "original.ipynb", original)
    executed_hash = _notebook_hash(tmp_path, "executed.ipynb", executed)
    assert original_hash.parsed
    assert executed_hash.parsed
    assert executed_hash.digest == original_hash.digest


@pytest.mark.parametrize(
    "edit",
    [
        "comment_in_cell",
        "rewrap_in_cell",
        "empty_code_cell",
        "markdown_cell",
        "trailing_semicolon",
    ],
)
def test_an_edit_that_does_not_change_a_notebooks_code_keeps_its_hash(
    tmp_path: Path, edit: str
) -> None:
    """A comment or a re-wrapped line in a code cell, an added empty code cell, an edited or added markdown cell, or a semicolon added at the end of a cell's last line leaves a notebook's hash unchanged."""
    cells = _base_cells()
    if edit == "comment_in_cell":
        _edit_cell(
            cells,
            _FRAME_CELL,
            "import pandas as pd\n",
            "# Build the frame.\nimport pandas as pd  # for data frames\n",
        )
    elif edit == "rewrap_in_cell":
        _edit_cell(
            cells,
            _FRAME_CELL,
            'pd.DataFrame({"year": [year]})',
            'pd.DataFrame(\n    {"year": [year]}\n)',
        )
    elif edit == "empty_code_cell":
        cells.insert(_PARAMETERS_CELL, _code_cell(""))
    elif edit == "markdown_cell":
        _edit_cell(cells, 0, "Coverage by year", "Coverage in each year")
        cells.insert(_PARAMETERS_CELL, _markdown_cell("The pipeline replaces these values."))
    elif edit == "trailing_semicolon":
        _edit_cell(
            cells, _SUMMARY_CELL, "frame.describe()\nsummary", "frame.describe()\nsummary;"
        )
    else:
        pytest.fail(f"no notebook edit named {edit}")

    original = _notebook_hash(tmp_path, "original.ipynb", _notebook(_base_cells()))
    edited = _notebook_hash(tmp_path, "edited.ipynb", _notebook(cells))
    assert edited == original


# Notebooks: what is part of the hash


@pytest.mark.parametrize(
    "edit",
    ["code_in_cell", "cells_swapped", "magic_line", "shell_line", "parameters_tag_moved"],
)
def test_an_edit_of_a_notebooks_code_changes_its_hash(tmp_path: Path, edit: str) -> None:
    """A code change in a cell, two code cells swapped, a changed %magic line, a changed !shell line, or the parameters tag moved to another code cell changes a notebook's hash."""
    cells = _base_cells()
    if edit == "code_in_cell":
        _edit_cell(cells, _FRAME_CELL, 'frame["location"]', 'frame["place"]')
    elif edit == "cells_swapped":
        cells[_FRAME_CELL], cells[_SUMMARY_CELL] = cells[_SUMMARY_CELL], cells[_FRAME_CELL]
    elif edit == "magic_line":
        _edit_cell(cells, _SUMMARY_CELL, "%matplotlib inline", "%matplotlib widget")
    elif edit == "shell_line":
        _edit_cell(cells, _SUMMARY_CELL, "!echo starting", "!echo started")
    elif edit == "parameters_tag_moved":
        cells[_PARAMETERS_CELL]["metadata"] = {}
        cells[_FRAME_CELL]["metadata"] = {"tags": ["parameters"]}
    else:
        pytest.fail(f"no notebook edit named {edit}")

    original = _notebook_hash(tmp_path, "original.ipynb", _notebook(_base_cells()))
    edited = _notebook_hash(tmp_path, "edited.ipynb", _notebook(cells))
    assert edited.digest != original.digest


@pytest.mark.parametrize("syntax", list(_IPYTHON_CELLS))
def test_a_cell_with_ipython_syntax_is_hashed_as_parsed_code(
    tmp_path: Path, syntax: str
) -> None:
    """A notebook whose code cell holds a %line magic, a !shell line, a shell assignment such as files = !ls, a help line such as len?, or a %%time cell magic gets a result that says it was parsed, and a comment added to that cell leaves the hash unchanged."""
    source = _IPYTHON_CELLS[syntax]
    original = _notebook_hash(tmp_path, "original.ipynb", _notebook([_code_cell(source)]))
    commented = _notebook_hash(
        tmp_path,
        "commented.ipynb",
        _notebook([_code_cell(source + "\n# Explain the value.")]),
    )
    assert original.parsed
    assert commented.parsed
    assert commented.digest == original.digest


@pytest.mark.parametrize(
    "first, second",
    [
        pytest.param("x: int = !ls", "x: int = !ls -a", id="annotated_shell_assignment"),
        pytest.param("%%bash\necho a", "%%bash\necho a\n# c", id="cell_magic_body_comment"),
        pytest.param("len?", "sorted?", id="help_target"),
    ],
)
def test_ipython_text_that_python_does_not_see_is_part_of_the_hash(
    tmp_path: Path, first: str, second: str
) -> None:
    """The command of an annotated shell assignment such as x: int = !ls, the body of a %%bash cell down to a comment line in it, and the name before a ? help mark are parsed and part of the hash, so a change to them changes it."""
    first_hash = _cell_hash(tmp_path, "first.ipynb", first)
    second_hash = _cell_hash(tmp_path, "second.ipynb", second)
    assert first_hash.parsed
    assert second_hash.parsed
    assert first_hash.digest != second_hash.digest


@pytest.mark.parametrize(
    "first, second",
    [
        pytest.param("%cd /tmp\nx = f()\npass", "pass\nx = f()\n%cd /tmp", id="line_magic"),
        pytest.param("x = !ls\nx = None", "x = None\nx = !ls", id="shell_assignment"),
        pytest.param("!echo a\n!echo b", "!echo b\n!echo a", id="two_shell_lines"),
    ],
)
def test_the_position_of_an_ipython_line_is_part_of_the_hash(
    tmp_path: Path, first: str, second: str
) -> None:
    """Two cells that differ only in where an IPython line stands among the other lines have different hashes."""
    first_hash = _cell_hash(tmp_path, "first.ipynb", first)
    second_hash = _cell_hash(tmp_path, "second.ipynb", second)
    assert first_hash.parsed
    assert second_hash.parsed
    assert first_hash.digest != second_hash.digest


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("!pip install -q \\\n    numpy\nx = 1", id="shell_line"),
        pytest.param("%pip install -q \\\n    numpy\nx = 1", id="line_magic"),
        pytest.param("files = !pip download \\\n    numpy\nx = 1", id="shell_assignment"),
        pytest.param("!pip install \\\n    -q \\\n    numpy\nx = 1", id="two_continuations"),
    ],
)
def test_an_ipython_line_ending_in_a_backslash_continues_on_the_next_line(
    tmp_path: Path, source: str
) -> None:
    """A !shell line, a %line magic or a shell assignment that ends with a backslash continues on the next line, also over several lines, so the cell is parsed, and a change to the continuation text changes the hash."""
    original = _cell_hash(tmp_path, "original.ipynb", source)
    edited = _cell_hash(tmp_path, "edited.ipynb", _edited(source, "numpy", "pandas"))
    assert original.parsed
    assert edited.parsed
    assert edited.digest != original.digest


def test_a_string_continued_by_a_backslash_stays_open_on_the_next_line(
    tmp_path: Path,
) -> None:
    """A short string continued by a backslash stays open on the next line, so a %magic line after the string is still found, the cell is parsed, and a change to that line changes the hash."""
    source = 'x = ["abc\\\ndef"]\n%matplotlib inline'
    original = _cell_hash(tmp_path, "original.ipynb", source)
    edited = _cell_hash(tmp_path, "edited.ipynb", _edited(source, "inline", "widget"))
    assert original.parsed
    assert edited.parsed
    assert edited.digest != original.digest


@pytest.mark.parametrize(
    "line, inner",
    [
        pytest.param("%matplotlib inline", "%time a = 1; b = 2", id="line_magic"),
        pytest.param("!ls", "!echo a; echo b", id="shell_line"),
    ],
)
def test_a_semicolon_ending_a_cells_last_ipython_line_is_not_part_of_the_hash(
    tmp_path: Path, line: str, inner: str
) -> None:
    """A semicolon added at the end of a cell's last line leaves the hash unchanged when that line is a %magic or a !shell line, while a semicolon inside such a line is part of its text."""
    plain = _cell_hash(tmp_path, "plain.ipynb", f"value = 1\n{line}")
    ended = _cell_hash(tmp_path, "ended.ipynb", f"value = 1\n{line};")
    assert plain.parsed
    assert ended == plain

    with_inner = _cell_hash(tmp_path, "with_inner.ipynb", f"value = 1\n{inner}")
    without_inner = _cell_hash(
        tmp_path, "without_inner.ipynb", f"value = 1\n{inner.replace(';', '')}"
    )
    assert with_inner.parsed
    assert without_inner.parsed
    assert with_inner.digest != without_inner.digest


@pytest.mark.parametrize(
    "first, second, same",
    [
        pytest.param("!ls;\nx = 1", "!ls\nx = 1", False, id="not_the_last_line"),
        pytest.param("!ls;\n# note", "!ls\n# note", True, id="comment_after"),
        pytest.param("!ls;\n# note", "!ls", True, id="comment_after_and_none"),
        pytest.param("files = !ls;", "files = !ls", True, id="shell_assignment"),
        pytest.param(
            "!pip install -q \\\nnumpy;",
            "!pip install -q \\\nnumpy",
            True,
            id="backslash_continuation",
        ),
    ],
)
def test_a_semicolon_is_left_out_only_on_a_cells_last_ipython_line(
    tmp_path: Path, first: str, second: str, same: bool
) -> None:
    """A semicolon ending an IPython line is left out of the hash only when that line is the cell's last code line, where a comment line after it does not count, also for a shell assignment and a line continued by a backslash."""
    first_hash = _cell_hash(tmp_path, "first.ipynb", first)
    second_hash = _cell_hash(tmp_path, "second.ipynb", second)
    assert first_hash.parsed
    assert second_hash.parsed
    assert (first_hash.digest == second_hash.digest) == same


@pytest.mark.parametrize(
    "first, second, same",
    [
        pytest.param("a, b = !ls", "a, b = !ls -a", False, id="tuple_target"),
        pytest.param("naïve = !ls", "naïve = !ls -a", False, id="non_ascii_target"),
        pytest.param("x = 1\nx %= 3", "x = 1\nx%=3", True, id="augmented_assignment"),
        pytest.param("a = b = 1\na != b", "a = b = 1\na!=b", True, id="comparison"),
        pytest.param(
            'text = """\nb = !ls\n"""', "text = '\\nb = !ls\\n'", True, id="inside_a_string"
        ),
    ],
)
def test_a_shell_or_magic_assignment_is_recognised_only_at_a_statement_start(
    tmp_path: Path, first: str, second: str, same: bool
) -> None:
    """A shell assignment to a tuple target or to a non-ASCII name is parsed and its command is part of the hash, while an augmented assignment such as x %= 3, a comparison such as a != b, and such text inside a string stay Python, so a change of spacing that keeps their code leaves the hash unchanged."""
    first_hash = _cell_hash(tmp_path, "first.ipynb", first)
    second_hash = _cell_hash(tmp_path, "second.ipynb", second)
    assert first_hash.parsed
    assert second_hash.parsed
    assert (first_hash.digest == second_hash.digest) == same


@pytest.mark.parametrize(
    "split, joined",
    [
        pytest.param("x = (a\n% b)", "x = (a % b)", id="modulo_in_parentheses"),
        pytest.param("x = a \\\n% b", "x = a % b", id="modulo_after_a_backslash"),
        pytest.param("ok = (a\n!= b)", "ok = (a != b)", id="not_equal_in_parentheses"),
    ],
)
def test_a_percent_or_bang_that_continues_a_statement_stays_python(
    tmp_path: Path, split: str, joined: str
) -> None:
    """A line that starts with % or ! but continues a statement, inside parentheses or after a backslash, stays Python and has the hash of the same code on one line."""
    split_hash = _cell_hash(tmp_path, "split.ipynb", split)
    joined_hash = _cell_hash(tmp_path, "joined.ipynb", joined)
    assert split_hash.parsed
    assert joined_hash.parsed
    assert split_hash.digest == joined_hash.digest


@pytest.mark.parametrize(
    "source, old, new",
    [
        pytest.param("import asyncio\nawait asyncio.sleep(0)", "0", "1", id="statement"),
        pytest.param("result = await fetch()\nresult", "fetch", "load", id="assignment"),
    ],
)
def test_a_cell_with_a_top_level_await_is_parsed(
    tmp_path: Path, source: str, old: str, new: str
) -> None:
    """A code cell with an await at its top level is parsed, and a change to the awaited call changes the hash."""
    original = _cell_hash(tmp_path, "original.ipynb", source)
    edited = _cell_hash(tmp_path, "edited.ipynb", _edited(source, old, new))
    assert original.parsed
    assert edited.parsed
    assert edited.digest != original.digest


def test_a_notebook_without_code_has_the_hash_of_one_without_cells(
    tmp_path: Path,
) -> None:
    """A notebook with no cells and one holding only a markdown cell and an empty code cell have equal hashes."""
    no_cells = _notebook_hash(tmp_path, "no_cells.ipynb", _notebook([]))
    no_code = _notebook_hash(
        tmp_path, "no_code.ipynb", _notebook([_markdown_cell("# Notes"), _code_cell("")])
    )
    assert no_code.digest == no_cells.digest


# Notebooks that cannot be parsed


def test_a_notebook_cell_that_cannot_be_parsed_still_gives_a_hash(tmp_path: Path) -> None:
    """A notebook with one code cell that is not valid Python, even after its IPython syntax is replaced, gets a hash without raising, the result says it was not parsed, and a change to that cell's text changes the hash."""
    broken = "%matplotlib inline\ndef broken(:  # the first version\n    return value"
    original = _notebook_hash(
        tmp_path, "original.ipynb", _notebook([_code_cell("value = 1"), _code_cell(broken)])
    )
    assert not original.parsed

    edits = [("first", "other"), ("return value", "return values")]
    for index, (old, new) in enumerate(edits):
        cells = [_code_cell("value = 1"), _code_cell(_edited(broken, old, new))]
        edited = _notebook_hash(tmp_path, f"edited_{index}.ipynb", _notebook(cells))
        assert not edited.parsed
        assert edited.digest != original.digest, (old, new)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("/f 1", id="autocall"),
        pytest.param("  x = 1", id="common_indent"),
    ],
)
def test_an_autocall_line_or_a_cell_with_a_common_indent_is_not_parsed(
    tmp_path: Path, source: str
) -> None:
    """A cell with an autocall line such as /f 1, or a cell whose lines all have a common indent such as "  x = 1", gets a hash with a result that says it was not parsed."""
    assert not _cell_hash(tmp_path, "cell.ipynb", source).parsed


def test_a_file_that_is_not_a_notebook_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    """notebook_hash, given a file that is not valid notebook JSON, returns a hash without raising, the result says it was not parsed, and a change to any byte changes the hash."""
    contents = {
        "truncated": (
            '{"cells": [{"cell_type": "code", "source": ["x = 1"]',
            "x = 1",
            "x = 2",
        ),
        "array": ("[1, 2, 3]\n", "3", "4"),
    }
    for name, (text, old, new) in contents.items():
        original_path = tmp_path / f"{name}.ipynb"
        original_path.write_text(text, encoding="utf-8")
        edited_path = tmp_path / f"{name}_edited.ipynb"
        edited_path.write_text(_edited(text, old, new), encoding="utf-8")

        original = notebook_hash(original_path)
        edited = notebook_hash(edited_path)
        assert not original.parsed, name
        assert not edited.parsed, name
        assert edited.digest != original.digest, name


def test_a_missing_notebook_raises_oserror(tmp_path: Path) -> None:
    """notebook_hash raises OSError for a path that does not exist."""
    with pytest.raises(OSError):
        notebook_hash(tmp_path / "missing.ipynb")


# The cache of results


def test_a_file_edited_between_two_calls_gets_a_new_hash(tmp_path: Path) -> None:
    """In one process, a Python file and a notebook that are hashed, then edited with their size and modification time kept the same, then hashed again get the hash of their new code."""
    python_path = tmp_path / "module.py"
    python_path.write_text("value = 1\n", encoding="utf-8")
    notebook_path = tmp_path / "notebook.ipynb"
    notebook_path.write_text(
        json.dumps(_notebook([_code_cell("value = 1")]), indent=1), encoding="utf-8"
    )
    cases: list[tuple[Path, Callable[[Path | str], CodeHash]]] = [
        (python_path, python_file_hash),
        (notebook_path, notebook_hash),
    ]
    for path, hash_file in cases:
        before = hash_file(path)
        status = path.stat()
        new_text = _edited(path.read_text(encoding="utf-8"), "value = 1", "value = 2")
        path.write_text(new_text, encoding="utf-8")
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))
        assert path.stat().st_size == status.st_size
        assert path.stat().st_mtime_ns == status.st_mtime_ns

        after = hash_file(path)
        reference_path = tmp_path / f"reference{path.suffix}"
        reference_path.write_text(new_text, encoding="utf-8")
        assert after.digest != before.digest, path.name
        assert after == hash_file(reference_path), path.name


def test_two_paths_with_the_same_content_have_the_same_hash(tmp_path: Path) -> None:
    """Two Python files at different paths with the same bytes, and two notebooks at different paths with the same bytes, have equal hashes."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    python_bytes = _BASE_SOURCE.encode("utf-8")
    (first / "one.py").write_bytes(python_bytes)
    (second / "two.py").write_bytes(python_bytes)
    assert python_file_hash(first / "one.py") == python_file_hash(second / "two.py")

    notebook_bytes = json.dumps(_notebook(_base_cells()), indent=1).encode("utf-8")
    (first / "one.ipynb").write_bytes(notebook_bytes)
    (second / "two.ipynb").write_bytes(notebook_bytes)
    assert notebook_hash(first / "one.ipynb") == notebook_hash(str(second / "two.ipynb"))


def test_the_cache_keeps_results_under_different_int_string_limits_apart(
    tmp_path: Path,
) -> None:
    """One Python file's bytes hashed under two int-to-string limits are not served from one cached result."""
    path = tmp_path / "long_int.py"
    # Not made unique: the second call must see bytes that the first call put in the cache.
    path.write_text(f"X = 1{'0' * 5000}\n", encoding="utf-8")
    default_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)
        unlimited = python_file_hash(path)
        sys.set_int_max_str_digits(640)
        limited = python_file_hash(path)
    finally:
        sys.set_int_max_str_digits(default_limit)
    assert unlimited.parsed
    assert not limited.parsed
    assert limited.digest.startswith("bytes:"), limited


def test_each_content_is_parsed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hashing two paths with the same Python bytes twice each, and two paths with the same notebook bytes twice each, calls compile once per distinct content."""
    calls: list[object] = []

    def counting_compile(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0] if args else kwargs.get("source"))
        return builtins.compile(*args, **kwargs)

    monkeypatch.setattr(code_hash_module, "compile", counting_compile, raising=False)
    # A marker no other test writes, so no earlier call has cached these contents.
    marker = uuid.uuid4().hex
    python_bytes = f"# {marker}\n{_BASE_SOURCE}".encode("utf-8")
    notebook = _notebook([_code_cell(f"# {marker}\nvalue = 1")])
    notebook_bytes = json.dumps(notebook, indent=1).encode("utf-8")
    python_paths = [tmp_path / "one.py", tmp_path / "two.py"]
    notebook_paths = [tmp_path / "one.ipynb", tmp_path / "two.ipynb"]
    for path in python_paths:
        path.write_bytes(python_bytes)
    for path in notebook_paths:
        path.write_bytes(notebook_bytes)

    for _ in range(2):
        for path in python_paths:
            assert python_file_hash(path).parsed
    assert len(calls) == 1, (
        f"compile was called {len(calls)} times for one Python content; 0 means code_hash "
        "does not call compile by that name"
    )
    for _ in range(2):
        for path in notebook_paths:
            assert notebook_hash(path).parsed
    assert len(calls) == 2, f"compile was called {len(calls) - 1} times for one notebook"


# The parameters cell shared with notebook_step


def test_notebook_step_and_the_hash_agree_on_the_parameters_cell(tmp_path: Path) -> None:
    """For code cells whose sources are lists of strings split inside a name, notebook_step accepts a name the joined parameters cell assigns, the notebook's hash equals that of the same notebook with each source as one string, and moving the parameters tag to a cell that does not assign the name makes notebook_step reject the name and changes the hash."""
    sources = [["oth", "er = 1"], ["loca", "tion = None\n", "year = 2025"]]

    def notebook(tagged: int, as_lists: bool) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        for index, source in enumerate(sources):
            cell = _code_cell("", tags=["parameters"] if index == tagged else None)
            cell["source"] = source if as_lists else "".join(source)
            cells.append(cell)
        return _notebook(cells)

    listed = _notebook_hash(tmp_path, "listed.ipynb", notebook(tagged=1, as_lists=True))
    joined = _notebook_hash(tmp_path, "joined.ipynb", notebook(tagged=1, as_lists=False))
    moved = _notebook_hash(tmp_path, "moved.ipynb", notebook(tagged=0, as_lists=True))
    assert listed.parsed
    assert listed == joined
    assert moved.digest != listed.digest

    registry = FileRegistry(tmp_path / "out", "run1")
    notebook_step(
        tmp_path / "listed.ipynb",
        executed="listed_run.ipynb",
        registry=registry,
        env="env",
        parameters={"location": "Kenya"},
    )
    with pytest.raises(ValueError, match="does not assign 'location'"):
        notebook_step(
            tmp_path / "moved.ipynb",
            executed="moved_run.ipynb",
            registry=registry,
            env="env",
            parameters={"location": "Kenya"},
        )
