"""Tests of the code hash of Python files and notebooks."""

import copy
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from vivarium.dagger.backend.code_hash import CodeHash, notebook_hash, python_file_hash

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


# Python files: edits that do not change the code


@pytest.mark.parametrize(
    "edit", ["comment", "blank_lines", "rewrap", "quote_style", "redundant_parentheses"]
)
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


@pytest.mark.parametrize(
    "edit",
    [
        "code",
        "docstring",
        "docstring_reformat",
        "annotation",
        "decorator",
        "import_order",
        "string_literal",
    ],
)
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


# Python files: the same hash on every supported Python version


def test_the_hash_of_fixed_sources_matches_the_stored_digests(tmp_path: Path) -> None:
    """For each Python file and notebook in a fixed set of sources stored in the repository, the hash equals the digest stored beside them, which was taken on another Python version; the set includes non-ASCII string literals, f-strings, type annotations, decorators, a match statement and a notebook with IPython lines."""
    stored = json.loads((_FIXED_SOURCES / "digests.json").read_text(encoding="utf-8"))
    names = sorted(
        path.name for path in _FIXED_SOURCES.iterdir() if path.suffix in (".py", ".ipynb")
    )
    assert names, f"no fixed sources in {_FIXED_SOURCES}"
    assert sorted(stored) == names, "digests.json must have one entry per fixed source"

    results: dict[str, CodeHash] = {}
    for name in names:
        # Hash a copy in another directory, because the hash must not depend on the path.
        shutil.copyfile(_FIXED_SOURCES / name, tmp_path / name)
        hash_file = notebook_hash if name.endswith(".ipynb") else python_file_hash
        results[name] = hash_file(tmp_path / name)

    missing = [name for name in names if stored[name]["digest"] is None]
    if missing:
        pytest.fail(
            "\n".join(
                f"digests.json has no digest for {name}; fill it from the reference version"
                f" (this Python gives {results[name]})"
                for name in missing
            )
        )
    for name in names:
        expected = CodeHash(digest=stored[name]["digest"], parsed=stored[name]["parsed"])
        assert results[name] == expected, name


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


@pytest.mark.parametrize(
    "syntax", ["line_magic", "shell_escape", "shell_assignment", "help", "cell_magic"]
)
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
